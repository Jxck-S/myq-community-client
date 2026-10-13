"""Offline unit tests — no network, no account needed.

Covers PKCE, authorize-URL construction, login-form parsing, MFA detection, the
redirect/code capture, and model parsing for BOTH the live and demo JSON shapes.
"""

import base64
import hashlib

try:
    import pytest
except ImportError:  # allow running without pytest via the plain runner below
    class _P:
        class raises:
            def __init__(self, *a): pass
            def __enter__(self): return self
            def __exit__(self, et, ev, tb): return et is not None
    pytest = _P()

from myqcommunity import Community, Device, MfaChallenge, const
from myqcommunity.auth import (
    AuthSession,
    _first_text_input,
    _form_action,
    _hidden_fields,
    generate_pkce,
)


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
    with pytest.raises(Exception):
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


# --- unlock routing (offline, no network) -----------------------------------
import time
from myqcommunity import MyQCommunity, Token
from myqcommunity.errors import MyQRequestError


def _client():
    tok = Token(access_token="x", refresh_token="r", expires_at=time.time() + 9999)
    return MyQCommunity(tok)


class _Resp:
    def __init__(self, status): self.status_code = status


def test_unlock_access_panel_uses_panels_host():
    c = _client(); calls = []
    def fake(method, host, path, **kw):
        calls.append((host, path))
        return _Resp(200)
    c._request = fake
    d = Device.from_json({"serial_number": "S1", "device_family": "access_panel",
                          "device_type": "access_panel_door", "name": "Gate", "kind": "door"})
    assert c.unlock("acct", d) is True
    assert calls[0][0] == "panels" and calls[0][1].endswith("/doors/S1/unlock")


def test_unlock_remote_lock_uses_devices_host():
    c = _client(); calls = []
    def fake(method, host, path, **kw):
        calls.append((host, path)); return _Resp(200)
    c._request = fake
    d = Device.from_json({"serial_number": "L1", "device_family": "remote_lock",
                          "device_type": "rl_lock", "name": "Front Door"})
    assert c.unlock("acct", d) is True
    assert calls[0][0] == "devices" and calls[0][1].endswith("/devices/L1/actions/unlock")


def test_unlock_unknown_family_falls_back():
    c = _client(); calls = []
    def fake(method, host, path, **kw):
        calls.append(host)
        if host == "panels":
            raise MyQRequestError("404", status=404)
        return _Resp(200)
    c._request = fake
    assert c.unlock("acct", "S9") is True       # serial only, unknown family
    assert calls == ["panels", "devices"]        # tried panel, fell back to devices


def test_unlock_auth_error_not_masked():
    c = _client()
    def fake(method, host, path, **kw):
        raise MyQRequestError("401", status=401)
    c._request = fake
    try:
        c.unlock("acct", "S9"); assert False, "should have raised"
    except MyQRequestError as e:
        assert e.status == 401


def test_history_event_parsing():
    from myqcommunity import HistoryEvent
    e = HistoryEvent.from_json({"serial_number": "M2TE2-5Q-YY5HB", "device_name": "Gate",
                                "id": "abc", "event": "unlocked", "timestamp": "2026-10-13T04:21:07Z"})
    assert e.event == "unlocked" and e.device_name == "Gate"
    assert e.timestamp == "2026-10-13T04:21:07Z" and e.serial_number == "M2TE2-5Q-YY5HB"


def test_history_cursor_pagination():
    c = _client(); pages = [
        {"items": [{"id": "1", "event": "unlocked"}], "continue": "CUR"},
        {"items": [{"id": "2", "event": "unlocked"}], "continue": None},
    ]
    seen = []
    def fake(method, host, path, params=None, **kw):
        seen.append((host, (params or {}).get("continue")))
        class R:
            status_code = 200
            def json(self_): return pages[len(seen) - 1]
        return R()
    c._request = fake
    ev = c.history("acct", limit=None)
    assert [e.id for e in ev] == ["1", "2"]
    assert seen == [("history", None), ("history", "CUR")]   # followed the cursor
