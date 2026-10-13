"""High-level myQ Community client: auth, list communities/devices, unlock.

Example
-------
>>> from myqcommunity import MyQCommunity
>>> myq = MyQCommunity.from_refresh_token("<refresh_token>")
>>> for comm in myq.communities():
...     for dev in myq.devices(comm.id):
...         if dev.is_lock and dev.name == "Front Door":
...             myq.unlock(comm.id, dev.serial_number)
"""

from __future__ import annotations

from typing import Callable

import httpx

from . import const
from .auth import AuthSession, Token
from .errors import MyQAuthError, MyQRequestError
from .models import Community, Device, HistoryEvent


def _items(data) -> list:
    """The ``items`` array of a response, which may also be a bare list."""
    if isinstance(data, list):
        return data
    return data.get("items") or []


class MyQCommunity:
    def __init__(
        self,
        token: Token,
        *,
        auth: AuthSession | None = None,
        hosts: dict[str, str] | None = None,
        on_token_refresh: Callable[[Token], None] | None = None,
    ):
        self._token = token
        self._auth = auth or AuthSession()
        self.hosts = {**const.HOSTS, **(hosts or {})}
        self._on_token_refresh = on_token_refresh
        self._http = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": const.USER_AGENT, "Accept": "application/json"},
        )

    # -- constructors ---------------------------------------------------------
    @classmethod
    def login(cls, email, password, otp_callback=None, *, identity_host=None, **kw) -> "MyQCommunity":
        """One-call headless login (email/password + optional MFA callback).

        ``otp_callback(challenge)`` returns the one-time code; see
        ``AuthSession.login``. The resulting (possibly rotated) refresh token is
        handed to ``on_token_refresh`` right away so it can be persisted.
        ``identity_host`` overrides the IdentityServer base for other deployments
        (default ``https://id.myq.com``; the app also uses
        ``https://identity.myqbusiness.com``).
        """
        auth = AuthSession(identity_host=identity_host or const.IDENTITY_HOST)
        token = auth.login(email, password, otp_callback=otp_callback)
        client = cls(token, auth=auth, **kw)
        client._persist()
        return client

    @classmethod
    def from_refresh_token(cls, refresh_token: str, *, identity_host=None, **kw) -> "MyQCommunity":
        """Recommended entry point: refresh a stored token and build a client.

        Refreshing may rotate the refresh token, so the new one is persisted via
        ``on_token_refresh`` immediately. ``identity_host`` overrides the token
        endpoint base for other deployments.
        """
        auth = AuthSession(identity_host=identity_host or const.IDENTITY_HOST)
        token = auth.refresh(refresh_token)
        client = cls(token, auth=auth, **kw)
        client._persist()
        return client

    @classmethod
    def from_redirect_url(cls, redirect_url: str, verifier: str, auth: AuthSession, **kw) -> "MyQCommunity":
        """Finish the interactive flow from the com.myqops://… redirect URL."""
        code = AuthSession.extract_code(redirect_url)
        token = auth.exchange_code(code, verifier)
        client = cls(token, auth=auth, **kw)
        client._persist()
        return client

    def _persist(self) -> None:
        if self._on_token_refresh:
            self._on_token_refresh(self._token)

    # -- token handling -------------------------------------------------------
    @property
    def token(self) -> Token:
        return self._token

    def _ensure_token(self) -> None:
        if self._token.is_expired:
            if not self._token.refresh_token:
                raise MyQAuthError("access token expired and no refresh token available")
            self._token = self._auth.refresh(self._token.refresh_token)
            self._persist()

    # -- request plumbing -----------------------------------------------------
    def _request(self, method: str, host_key: str, path: str, **kw) -> httpx.Response:
        self._ensure_token()
        url = f"{self.hosts[host_key].rstrip('/')}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token.access_token}"}
        headers.update(kw.pop("headers", {}))
        resp = self._http.request(method, url, headers=headers, **kw)

        # One retry on 401 (token may have just expired server-side).
        if resp.status_code == 401 and self._token.refresh_token:
            self._token = self._auth.refresh(self._token.refresh_token)
            self._persist()
            headers["Authorization"] = f"Bearer {self._token.access_token}"
            resp = self._http.request(method, url, headers=headers, **kw)

        if resp.status_code >= 400:
            raise MyQRequestError(
                f"{method} {url} -> {resp.status_code}",
                status=resp.status_code,
                body=resp.text[:1000],
            )
        return resp

    # -- resources ------------------------------------------------------------
    def communities(self) -> list[Community]:
        """GET /api/v6/communities — the properties this account belongs to."""
        resp = self._request("GET", "facilities", f"{const.API_VERSION}/communities")
        return [Community.from_json(c) for c in _items(resp.json())]

    def devices(self, account_id: str) -> list[Device]:
        """GET /api/v6/communities/{accountId}/devices — doors, locks, panels.

        Follows pagination when the response reports multiple pages, so accounts
        with many devices return everything, not just the first page.
        """
        out: list[Device] = []
        page = 1
        while True:
            params = {} if page == 1 else {"page": page}
            resp = self._request(
                "GET", "devices",
                f"{const.API_VERSION}/communities/{account_id}/devices",
                params=params or None,
            )
            data = resp.json()
            items = _items(data)
            out.extend(Device.from_json(d) for d in items)
            meta = (data.get("metadata") if isinstance(data, dict) else None) or {}
            total_pages = meta.get("total_pages")
            if not total_pages or page >= total_pages or not items:
                break
            page += 1
        return out

    def locks(self, account_id: str) -> list[Device]:
        """Convenience: only the unlockable devices in a community."""
        return [d for d in self.devices(account_id) if d.is_lock]

    def device_action(self, account_id: str, serial_number: str, action: str) -> bool:
        """POST /api/v6/communities/{id}/devices/{serial}/actions/{action}.

        This is the generic device-action endpoint the app uses to actuate access
        panels and locks. ``action`` is ``"unlock"`` or ``"lock"``.
        The body is ``{"action": action}`` (Content-Type JSON).
        """
        resp = self._request(
            "POST",
            "devices",
            f"{const.API_VERSION}/communities/{account_id}/devices/{serial_number}/actions/{action}",
            json={"action": action},
        )
        return 200 <= resp.status_code < 300

    def _unlock_panel(self, account_id: str, serial: str) -> bool:
        """Access-panel / gate open.

        POST {panels}/api/v6/communities/{id}/doors/{serial}/unlock, empty JSON body.
        """
        resp = self._request(
            "POST", "panels",
            f"{const.API_VERSION}/communities/{account_id}/doors/{serial}/unlock",
            headers={"Content-Type": "application/json"}, content=b"",
        )
        return 200 <= resp.status_code < 300

    def _unlock_lock(self, account_id: str, serial: str) -> bool:
        """Smart-lock unlock — device-action endpoint on the devices host."""
        return self.device_action(account_id, serial, "unlock")

    def unlock(self, account_id: str, device_or_serial) -> bool:
        """Unlock / open a door, gate, lock, or access panel. Returns True on success.

        Pass a :class:`Device` (preferred — routes by its family) or a serial string.
        myQ Community has two unlock mechanisms depending on device family:

        * ``access_panel`` (gates/community doors) → ``/doors/{serial}/unlock`` on the
          **panels** host
        * ``remote_lock`` (smart locks) → ``/devices/{serial}/actions/unlock`` on the
          **devices** host

        When the family is unknown, both are tried in turn, so this works across
        setups without the caller needing to know the device type. Only a
        route-mismatch status (400/404/405) triggers a fallback; auth/permission
        errors are raised.
        """
        if isinstance(device_or_serial, Device):
            serial = device_or_serial.serial_number
            family = device_or_serial.family
        else:
            serial, family = device_or_serial, None

        if family == "remote_lock":
            order = (self._unlock_lock, self._unlock_panel)
        elif family == "access_panel":
            order = (self._unlock_panel, self._unlock_lock)
        else:  # unknown — panel path is the verified default, then smart-lock path
            order = (self._unlock_panel, self._unlock_lock)

        last_err: MyQRequestError | None = None
        for strategy in order:
            try:
                if strategy(account_id, serial):
                    return True
            except MyQRequestError as e:
                last_err = e
                if e.status not in (400, 404, 405):
                    raise  # real failure (auth/permission/etc.), don't mask it
        if last_err:
            raise last_err
        return False

    def lock(self, account_id: str, serial_number: str) -> bool:
        """Lock a device via the device-action endpoint (``actions/lock``)."""
        return self.device_action(account_id, serial_number, "lock")

    def history(self, account_id: str, limit: int | None = 100) -> list[HistoryEvent]:
        """GET /api/v6/communities/{accountId}/history — access/unlock events.

        Newest first. Follows the cursor (`continue`) up to ``limit`` events
        (``limit=None`` for all). Each event has ``event`` (e.g. "unlocked"),
        ``device_name``, ``serial_number`` and an ISO-8601 ``timestamp``.
        Source: ``D7/a.java`` `@f("api/v6/communities/{accountId}/history")`,
        `@t("continue")`. On the history host. Verified live.
        """
        out: list[HistoryEvent] = []
        cursor: str | None = None
        while True:
            params = {"continue": cursor} if cursor else None
            resp = self._request(
                "GET", "history",
                f"{const.API_VERSION}/communities/{account_id}/history",
                params=params,
            )
            data = resp.json()
            items = _items(data)
            out.extend(HistoryEvent.from_json(e) for e in items)
            cursor = data.get("continue") if isinstance(data, dict) else None
            if not cursor or not items or (limit is not None and len(out) >= limit):
                break
        return out[:limit] if limit is not None else out

    def close(self) -> None:
        self._http.close()
        self._auth.close()

    def __enter__(self) -> "MyQCommunity":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
