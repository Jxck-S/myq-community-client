"""Offline unit tests — no network, no account needed.

Covers PKCE, authorize-URL construction, login-form parsing, MFA detection, the
two-step (start/finish) login against a mocked IdentityServer, token refresh +
rotation, model parsing for BOTH the live and demo JSON shapes, unlock routing
and pagination.
"""

import asyncio
import base64
import hashlib
import time

import httpx
import pytest
import respx

from myqcommunity import (
    AuthSession,
    Community,
    Device,
    HistoryEvent,
    MfaChallenge,
    MyQAuthError,
    MyQCommunity,
    MyQOtpError,
    PendingMfa,
    Token,
    const,
)
from myqcommunity.auth import _first_text_input, _form_action, _hidden_fields, generate_pkce
from myqcommunity.errors import MyQRequestError

ID = const.IDENTITY_HOST
TOKEN_URL = f"{ID}/connect/token"


def test_pkce_s256():
    v, c = generate_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert c == expected


def test_authorize_url():
    url = AuthSession().authorize_url()
    assert url.startswith("https://id.myq.com/connect/authorize?")
    assert f"client_id={const.CLIENT_ID}" in url
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=com.myqops" in url


def test_extract_code():
    assert AuthSession.extract_code("com.myqops://mobile?code=ABC123&scope=x") == "ABC123"
    with pytest.raises(MyQAuthError):
        AuthSession.extract_code("com.myqops://mobile?error=access_denied")


LOGIN_PAGE = '''<form action="/Account/LoginWithEmail?returnUrl=%2Fx" method="post">
<input name="ReturnUrl" value="/connect/authorize/callback?x=1"/>
<input name="Email" type="email"/>
<input name="Password" type="password"/>
<input name="__RequestVerificationToken" type="hidden" value="TOKENVAL"/></form>'''

OTP_PAGE = '''<form action="/AccountMfa/VerifyOtp?returnUrl=%2Fx" method="post">
<input name="SelectedMfaMethod" value="Sms"/>
<input name="MaskedPhoneNumber" value="(XXX) XXX-3392"/>
<input name="MaskedEmail" value="j***1@gmail.com"/>
<input name="Otp" type="number" value=""/>
<input name="__RequestVerificationToken" type="hidden" value="TOK2"/></form>'''


def test_form_parsing():
    assert _form_action(LOGIN_PAGE).startswith("/Account/LoginWithEmail")
    f = _hidden_fields(LOGIN_PAGE)
    assert f["__RequestVerificationToken"] == "TOKENVAL"
    assert "Email" in f and "Password" in f


def test_form_action_attribute_order():
    page = '<form method="post" class="x" action="/Account/Login?a=1&amp;b=2">'
    assert _form_action(page) == "/Account/Login?a=1&b=2"


def test_mfa_challenge_sms():
    ch = MfaChallenge.from_page(OTP_PAGE)
    assert ch.method == "sms"
    assert ch.masked_phone == "(XXX) XXX-3392"
    assert ch.otp_field == "Otp"


def test_mfa_challenge_email_fallback():
    page = OTP_PAGE.replace('value="Sms"', 'value="Email"').replace(
        '<input name="MaskedPhoneNumber" value="(XXX) XXX-3392"/>', ""
    )
    ch = MfaChallenge.from_page(page)
    assert ch.method == "email"


def test_first_text_input():
    assert _first_text_input(OTP_PAGE) == "Otp"


def test_token_dict_roundtrip():
    t = Token(access_token="a", refresh_token="r", expires_at=123.0)
    assert Token.from_dict(t.to_dict()) == t


# --- models ------------------------------------------------------------------

def test_device_live_shape():
    d = Device.from_json({
        "serial_number": "MDTE2-5Q-YY5HB_Door1", "device_family": "access_panel",
        "device_type": "access_panel_door", "name": "Gate", "online": True, "kind": "door",
    })
    assert d.name == "Gate" and d.family == "access_panel" and d.kind == "door"
    assert d.online is True and d.is_lock is True
    assert d.serial_number == "MDTE2-5Q-YY5HB_Door1"


def test_device_demo_shape():
    d = Device.from_json({
        "serial_number": "ULCK-1", "family": "remote_lock", "type": "rl_lock",
        "configuration": {"name": "Front Door"}, "state": {"online": True},
    })
    assert d.name == "Front Door" and d.is_lock is True and d.online is True


def test_device_non_lock():
    d = Device.from_json({"serial_number": "X", "device_family": "camera",
                          "device_type": "cam", "name": "Cam", "kind": "camera"})
    assert d.is_lock is False


def test_community():
    c = Community.from_json({"id": "d68c16b6-9ff3", "name": "AVE Las Colinas", "active": True})
    assert c.id == "d68c16b6-9ff3" and c.name == "AVE Las Colinas"


def test_history_event_parsing():
    e = HistoryEvent.from_json({"serial_number": "M2TE2-5Q-YY5HB", "device_name": "Gate",
                                "id": "abc", "event": "unlocked", "timestamp": "2026-10-13T04:21:07Z"})
    assert e.event == "unlocked" and e.device_name == "Gate"
    assert e.timestamp == "2026-10-13T04:21:07Z" and e.serial_number == "M2TE2-5Q-YY5HB"


# --- headless login (mocked IdentityServer) ----------------------------------

def _mock_login(mfa: bool = True):
    """Authorize -> login form -> (OTP form ->) com.myqops redirect -> token."""
    respx.get(url__startswith=f"{ID}/connect/authorize").respond(
        302, headers={"Location": "/Account/LoginWithEmail?returnUrl=%2Fx"})
    respx.get(url__startswith=f"{ID}/Account/LoginWithEmail").respond(200, text=LOGIN_PAGE)
    done = httpx.Response(302, headers={"Location": "com.myqops://mobile?code=AUTHCODE"})
    login_post = respx.post(url__startswith=f"{ID}/Account/LoginWithEmail")
    if mfa:
        login_post.respond(302, headers={"Location": "/AccountMfa/VerifyOtp?returnUrl=%2Fx"})
        respx.get(url__startswith=f"{ID}/AccountMfa/VerifyOtp").respond(200, text=OTP_PAGE)

        def check_otp(request):
            ok = b"Otp=123456" in request.content
            return done if ok else httpx.Response(200, text=OTP_PAGE)
        respx.post(url__startswith=f"{ID}/AccountMfa/VerifyOtp").mock(side_effect=check_otp)
    else:
        login_post.mock(return_value=done)
    return respx.post(TOKEN_URL).respond(
        200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})


@respx.mock
async def test_start_finish_login_with_retry():
    token_route = _mock_login(mfa=True)
    auth = AuthSession()
    pending = await auth.start_login("me@example.com", "pw")
    assert isinstance(pending, PendingMfa)
    assert pending.challenge.masked_phone == "(XXX) XXX-3392"

    with pytest.raises(MyQOtpError):
        await auth.finish_login(pending, "000000")       # wrong code, pending survives
    tok = await auth.finish_login(pending, " 123456 ")
    assert tok.refresh_token == "RT"
    assert b"code=AUTHCODE" in token_route.calls[0].request.content
    assert pending._client.is_closed
    await auth.aclose()


@respx.mock
async def test_start_login_without_mfa_returns_token():
    _mock_login(mfa=False)
    auth = AuthSession()
    tok = await auth.start_login("me@example.com", "pw")
    assert isinstance(tok, Token) and tok.access_token == "AT"
    await auth.aclose()


@respx.mock
async def test_login_with_async_callback_persists_token():
    _mock_login(mfa=True)
    prompts, saved = [], []

    async def get_code(ch):
        prompts.append(ch.method)
        return "123456"

    myq = await MyQCommunity.login("me@example.com", "pw", otp_callback=get_code,
                                   on_token_refresh=saved.append)
    assert prompts == ["sms"] and [t.refresh_token for t in saved] == ["RT"]
    await myq.aclose()


@respx.mock
async def test_login_mfa_without_callback_raises():
    _mock_login(mfa=True)
    with pytest.raises(MyQAuthError):
        await AuthSession().login("me@example.com", "pw")


# --- request plumbing / token refresh ----------------------------------------

def _client(**kw):
    tok = Token(access_token="x", refresh_token="r", expires_at=time.time() + 9999)
    return MyQCommunity(tok, **kw)


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


async def test_request_refreshes_and_persists_on_401():
    saved = []
    tok = Token(access_token="old", refresh_token="r1", expires_at=time.time() + 9999)
    c = MyQCommunity(tok, on_token_refresh=saved.append)
    with respx.mock:
        respx.post(TOKEN_URL).respond(
            200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600})
        route = respx.get(f"{const.HOSTS['facilities']}/{const.API_VERSION}/communities")
        route.side_effect = [httpx.Response(401), httpx.Response(200, json={"items": []})]
        assert await c.communities() == []
    assert route.calls[1].request.headers["Authorization"] == "Bearer new"
    assert [t.refresh_token for t in saved] == ["r2"]   # rotated token persisted
    await c.aclose()


async def test_concurrent_expired_requests_refresh_once():
    saved = []

    async def save(tok):  # async callbacks are awaited
        saved.append(tok.refresh_token)

    refreshed = []

    async def slow_refresh(rt):  # yields, so the other requests queue on the lock
        refreshed.append(rt)
        await asyncio.sleep(0.01)
        return Token(access_token="new", refresh_token="r2", expires_at=time.time() + 3600)

    tok = Token(access_token="old", refresh_token="r1", expires_at=0)
    c = MyQCommunity(tok, on_token_refresh=save)
    c._auth.refresh = slow_refresh
    with respx.mock:
        respx.get(url__startswith=const.HOSTS["facilities"]).respond(200, json={"items": []})
        await asyncio.gather(*(c.communities() for _ in range(5)))
    assert refreshed == ["r1"] and saved == ["r2"]
    await c.aclose()


async def test_refresh_failure_raises_auth_error():
    tok = Token(access_token="old", refresh_token="revoked", expires_at=0)
    c = MyQCommunity(tok)
    with respx.mock:
        respx.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
        with pytest.raises(MyQAuthError):
            await c.communities()
    await c.aclose()


async def test_shared_http_client_not_closed():
    shared = httpx.AsyncClient()
    c = _client(http=shared)
    await c.aclose()
    assert not shared.is_closed
    await shared.aclose()


async def test_communities_bare_list_response():
    c = _client()

    async def fake(*a, **kw):
        return _Resp(payload=[{"id": "C1", "name": "A"}])
    c._request = fake
    assert [x.id for x in await c.communities()] == ["C1"]


async def test_devices_page_pagination():
    c = _client(); seen = []
    pages = [
        {"items": [{"serial_number": "A"}], "metadata": {"total_pages": 2}},
        {"items": [{"serial_number": "B"}], "metadata": {"total_pages": 2}},
    ]

    async def fake(method, host, path, params=None, **kw):
        seen.append(params)
        return _Resp(payload=pages[len(seen) - 1])
    c._request = fake
    assert [d.serial_number for d in await c.devices("acct")] == ["A", "B"]
    assert seen == [None, {"page": 2}]


async def test_history_cursor_pagination():
    c = _client(); seen = []
    pages = [
        {"items": [{"id": "1", "event": "unlocked"}], "continue": "CUR"},
        {"items": [{"id": "2", "event": "unlocked"}], "continue": None},
    ]

    async def fake(method, host, path, params=None, **kw):
        seen.append((host, (params or {}).get("continue")))
        return _Resp(payload=pages[len(seen) - 1])
    c._request = fake
    ev = await c.history("acct", limit=None)
    assert [e.id for e in ev] == ["1", "2"]
    assert seen == [("history", None), ("history", "CUR")]   # followed the cursor


# --- unlock routing ------------------------------------------------------------

async def test_unlock_access_panel_uses_panels_host():
    c = _client(); calls = []

    async def fake(method, host, path, **kw):
        calls.append((host, path))
        return _Resp(200)
    c._request = fake
    d = Device.from_json({"serial_number": "S1", "device_family": "access_panel",
                          "device_type": "access_panel_door", "name": "Gate", "kind": "door"})
    assert await c.unlock("acct", d) is True
    assert calls[0][0] == "panels" and calls[0][1].endswith("/doors/S1/unlock")


async def test_unlock_remote_lock_uses_devices_host():
    c = _client(); calls = []

    async def fake(method, host, path, **kw):
        calls.append((host, path))
        return _Resp(200)
    c._request = fake
    d = Device.from_json({"serial_number": "L1", "device_family": "remote_lock",
                          "device_type": "rl_lock", "name": "Front Door"})
    assert await c.unlock("acct", d) is True
    assert calls[0][0] == "devices" and calls[0][1].endswith("/devices/L1/actions/unlock")


async def test_unlock_unknown_family_falls_back():
    c = _client(); calls = []

    async def fake(method, host, path, **kw):
        calls.append(host)
        if host == "panels":
            raise MyQRequestError("404", status=404)
        return _Resp(200)
    c._request = fake
    assert await c.unlock("acct", "S9") is True       # serial only, unknown family
    assert calls == ["panels", "devices"]              # tried panel, fell back to devices


async def test_unlock_auth_error_not_masked():
    c = _client()

    async def fake(method, host, path, **kw):
        raise MyQRequestError("401", status=401)
    c._request = fake
    with pytest.raises(MyQRequestError) as e:
        await c.unlock("acct", "S9")
    assert e.value.status == 401
