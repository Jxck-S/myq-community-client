"""OAuth2 Authorization-Code + PKCE flow for myQ Community (IdentityServer).

The official app opens ``{identity}/connect/authorize`` in a browser, the user
logs in on id.myq.com, and IdentityServer redirects to ``com.myqops://mobile?code=…``.
The app then POSTs that code to ``{identity}/connect/token`` with the PKCE verifier.

There is **no** password/ROPC grant in the app, so a headless client must either:

* run the interactive flow once, read the redirect ``code`` (``AuthSession``),
  then persist the returned ``refresh_token`` and refresh forever; or
* drive the login form headlessly with ``login()`` (handles MFA via a callback).

See API_REFERENCE.md for the auth flow.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import os
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import const
from .errors import MyQAuthError

# A browser-like UA; the IdentityServer login pages are server-rendered HTML.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _form_action(page: str) -> str | None:
    """``action`` of the first POST form, regardless of attribute order."""
    for m in re.finditer(r"<form[^>]*>", page, re.I):
        tag = m.group(0)
        if not re.search(r'method="post"', tag, re.I):
            continue
        a = re.search(r'\baction="([^"]+)"', tag, re.I)
        if a:
            return _html.unescape(a.group(1))
    return None


def _hidden_fields(page: str) -> dict:
    """All <input> name=value pairs as rendered (hidden + visible)."""
    out: dict = {}
    for m in re.finditer(r"<input[^>]*>", page):
        tag = m.group(0)
        n = re.search(r'name="([^"]+)"', tag)
        if not n:
            continue
        v = re.search(r'value="([^"]*)"', tag)
        out[_html.unescape(n.group(1))] = _html.unescape(v.group(1)) if v else ""
    return out


@dataclass
class MfaChallenge:
    """Describes the one-time-code step so an otp_callback can prompt usefully."""

    method: str = "sms"          # "sms" or "email"
    masked_phone: str | None = None
    masked_email: str | None = None
    otp_field: str = "Otp"       # the form field the code goes in

    @classmethod
    def from_page(cls, page: str) -> "MfaChallenge":
        def val(name):
            m = re.search(
                r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), page
            )
            return _html.unescape(m.group(1)) if m and m.group(1) else None
        phone = val("MaskedPhoneNumber")
        email = val("MaskedEmail")
        selected = (val("SelectedMfaMethod") or "").lower()
        method = "email" if ("email" in selected or (email and not phone)) else "sms"
        otp_field = "Otp" if re.search(r'name="Otp"', page) else _first_text_input(page)
        return cls(method=method, masked_phone=phone, masked_email=email,
                   otp_field=otp_field or "Otp")


def _first_text_input(page: str) -> str | None:
    for m in re.finditer(r"<input[^>]*>", page):
        tag = m.group(0)
        if re.search(r'type="(text|number|tel)"', tag):
            n = re.search(r'name="([^"]+)"', tag)
            if n:
                return n.group(1)
    return None


def _b64url(data: bytes) -> str:
    """Base64url without padding — matches the app's Base64 flag 11."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` using S256, exactly like the app.

    The app uses 32 random bytes, base64url-encoded, as the verifier, then
    ``base64url(sha256(verifier))`` as the challenge.
    """
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass
class Token:
    access_token: str
    refresh_token: str | None
    expires_at: float
    token_type: str = "Bearer"

    @property
    def is_expired(self) -> bool:
        # 60s safety margin.
        return time.time() >= (self.expires_at - 60)

    @classmethod
    def from_response(cls, data: dict) -> "Token":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            token_type=data.get("token_type", "Bearer"),
        )


class AuthSession:
    """Drives the PKCE authorize + token exchange against id.myq.com."""

    def __init__(self, *, identity_host: str = const.IDENTITY_HOST, http: httpx.Client | None = None):
        self.identity_host = identity_host.rstrip("/")
        self._http = http or httpx.Client(
            timeout=30.0, headers={"User-Agent": const.USER_AGENT}, follow_redirects=False
        )
        self._verifier: str | None = None

    # -- step 1: build the URL the user opens in a browser --------------------
    def authorize_url(self) -> str:
        verifier, challenge = generate_pkce()
        self._verifier = verifier
        return self._authorize_url_with(challenge)

    def _authorize_url_with(self, challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": const.CLIENT_ID,
            "scope": const.AUTHORIZE_SCOPE,
            "redirect_uri": const.REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "acr_values": const.ACR_VALUES,
        }
        return f"{self.identity_host}/connect/authorize?{urlencode(params)}"

    # -- step 2a: the user pastes back the com.myqops://mobile?code=… URL -----
    @staticmethod
    def extract_code(redirect_url: str) -> str:
        qs = parse_qs(urlparse(redirect_url).query)
        if "error" in qs:
            raise MyQAuthError(f"authorize returned error: {qs['error'][0]}")
        code = qs.get("code", [None])[0]
        if not code:
            raise MyQAuthError(f"no ?code= found in redirect URL: {redirect_url!r}")
        return code

    # -- step 2b: exchange the code (+ PKCE verifier) for tokens ---------------
    def exchange_code(self, code: str, verifier: str | None = None) -> Token:
        verifier = verifier or self._verifier
        if not verifier:
            raise MyQAuthError("PKCE verifier missing; call authorize_url() first or pass verifier=")
        return self._token_request(
            {
                "response_type": "code",
                "scope": const.TOKEN_SCOPE,
                "redirect_uri": const.REDIRECT_URI,
                "client_id": const.CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
            }
        )

    def refresh(self, refresh_token: str) -> Token:
        tok = self._token_request(
            {
                "client_id": const.CLIENT_ID,
                "grant_type": "refresh_token",
                "scope": const.TOKEN_SCOPE,
                "refresh_token": refresh_token,
            }
        )
        # IdentityServer may or may not rotate the refresh token; keep the old
        # one if a new one wasn't issued.
        if not tok.refresh_token:
            tok.refresh_token = refresh_token
        return tok

    def _token_request(self, form: dict) -> Token:
        resp = self._http.post(
            f"{self.identity_host}/connect/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise MyQAuthError(f"token endpoint {resp.status_code}: {resp.text[:500]}")
        return Token.from_response(resp.json())

    # -- experimental: headless username/password -----------------------------
    def login(
        self,
        email: str,
        password: str,
        otp_callback: "Callable[[MfaChallenge], str] | None" = None,
        max_otp_attempts: int = 3,
    ) -> Token:
        """Fully headless login against the IdentityServer web flow.

        This mirrors what the hosted login does: GET /connect/authorize ->
        server-rendered login form (Email/Password/__RequestVerificationToken) ->
        POST credentials. The account then either (a) redirects straight to
        ``com.myqops://mobile?code=...`` (no MFA), or (b) lands on an OTP page.

        For MFA, ``otp_callback`` is called with an :class:`MfaChallenge` (it has
        ``.masked_phone`` / ``.masked_email`` / ``.method``) and must return the
        code the user received. It is re-invoked on an incorrect code, up to
        ``max_otp_attempts``. If MFA is required and no callback is given, an
        :class:`MyQAuthError` is raised.

        Verified against a live MFA-enabled account; validated to also handle the
        no-MFA path. Returns a :class:`Token` whose ``refresh_token`` can be stored
        for subsequent fully non-interactive use.
        """
        verifier, challenge = generate_pkce()
        self._verifier = verifier
        client = httpx.Client(
            timeout=30.0, headers={"User-Agent": _BROWSER_UA}, follow_redirects=False
        )
        try:
            # 1. Authorize -> follow to either the login page or straight to a code.
            resp, code = self._follow(client, self._authorize_url_with(challenge))
            if code:
                return self.exchange_code(code, verifier)
            page = resp.text
            action = _form_action(page)
            if not action or "Password" not in page:
                raise MyQAuthError(
                    "did not reach the expected login form (got %s); the login UI "
                    "may have changed." % str(resp.url)
                )
            fields = _hidden_fields(page)
            fields["Email"] = email
            fields["Password"] = password
            # 2. Submit credentials.
            resp, code = self._follow(
                client, str(resp.url.join(action)), method="POST", data=fields
            )
            if code:
                return self.exchange_code(code, verifier)

            # 3. MFA step?
            if "VerifyOtp" not in str(resp.url) and "Otp" not in resp.text:
                snippet = re.findall(
                    r"(invalid|incorrect|not match|locked|disabled)[^<]{0,60}",
                    resp.text, re.I,
                )
                raise MyQAuthError(
                    "login failed (no code, no MFA page). Check credentials. "
                    "Hints: %s" % (snippet[:2] or "none")
                )
            if otp_callback is None:
                raise MyQAuthError(
                    "account requires a one-time code but no otp_callback was provided."
                )

            challenge_obj = MfaChallenge.from_page(resp.text)
            # Use the EXACT form rendered when the code was sent; do not re-GET
            # (that rotates the expected code).
            otp_page = resp.text
            otp_url = str(resp.url.join(_form_action(otp_page) or str(resp.url)))
            for attempt in range(max_otp_attempts):
                otp = (otp_callback(challenge_obj) or "").strip()
                otp_fields = _hidden_fields(otp_page)
                otp_fields[challenge_obj.otp_field] = otp
                resp, code = self._follow(
                    client, otp_url, method="POST", data=otp_fields
                )
                if code:
                    return self.exchange_code(code, verifier)
                # Incorrect/expired: reuse the returned page for the next attempt.
                otp_page = resp.text
                otp_url = str(resp.url.join(_form_action(otp_page) or otp_url))
                challenge_obj = MfaChallenge.from_page(otp_page)
            raise MyQAuthError(
                "one-time code rejected after %d attempt(s)." % max_otp_attempts
            )
        finally:
            client.close()

    def _follow(self, client, url, method="GET", data=None, max_hops=12):
        """Walk a request's redirect chain, stopping at the com.myqops code.

        Returns ``(last_response, code_or_None)``. The code is read from the
        ``Location`` header the moment a hop points at the app's URL scheme, so
        the browser-only ``com.myqops://`` dead end never happens.
        """
        resp = client.request(method, url, data=data)
        for _ in range(max_hops):
            loc = resp.headers.get("location", "")
            if loc.startswith(const.REDIRECT_URI):
                return resp, self.extract_code(loc)
            if resp.status_code not in (301, 302, 303, 307, 308) or not loc:
                return resp, None
            nxt = loc if loc.startswith("http") else str(resp.url.join(loc))
            resp = client.get(nxt)
        return resp, None

    # Back-compat alias.
    login_with_password = login

    def close(self) -> None:
        self._http.close()
