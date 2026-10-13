"""High-level myQ Community client: auth, list communities/devices, unlock.

All network methods are async.

Example
-------
>>> from myqcommunity import MyQCommunity
>>> async with await MyQCommunity.from_refresh_token("<refresh_token>") as myq:
...     for comm in await myq.communities():
...         for dev in await myq.locks(comm.id):
...             if dev.name == "Front Door":
...                 await myq.unlock(comm.id, dev)
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable, Union

import httpx

from . import const
from .auth import AuthSession, Token
from .errors import MyQAuthError, MyQRequestError
from .models import Community, Device, HistoryEvent

#: Called with the new :class:`Token` whenever it changes; may be sync or async.
TokenCallback = Callable[[Token], Union[None, Awaitable[None]]]


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
        on_token_refresh: TokenCallback | None = None,
        http: httpx.AsyncClient | None = None,
    ):
        """``http`` may be a shared ``httpx.AsyncClient`` (e.g. Home Assistant's);
        it is used for API and token calls and is not closed by :meth:`aclose`.
        """
        self._token = token
        self._auth = auth or AuthSession(http=http)
        self.hosts = {**const.HOSTS, **(hosts or {})}
        self._on_token_refresh = on_token_refresh
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._headers = {"User-Agent": const.USER_AGENT, "Accept": "application/json"}
        # Refresh tokens rotate: two concurrent refreshes would burn one of them.
        self._refresh_lock = asyncio.Lock()

    # -- constructors ---------------------------------------------------------
    @classmethod
    async def login(cls, email, password, otp_callback=None, *, identity_host=None,
                    http: httpx.AsyncClient | None = None, **kw) -> "MyQCommunity":
        """One-call headless login (email/password + optional MFA callback).

        ``otp_callback(challenge)`` (sync or async) returns the one-time code;
        see ``AuthSession.login``. The resulting refresh token is handed to
        ``on_token_refresh`` right away so it can be persisted.
        ``identity_host`` overrides the IdentityServer base for other deployments
        (default ``https://id.myq.com``; the app also uses
        ``https://identity.myqbusiness.com``). For a login whose code is
        collected in a separate step, use ``AuthSession.start_login`` /
        ``finish_login`` and pass the token to the constructor.
        """
        auth = AuthSession(identity_host=identity_host or const.IDENTITY_HOST, http=http)
        try:
            token = await auth.login(email, password, otp_callback=otp_callback)
        except BaseException:
            await auth.aclose()
            raise
        return await cls._build(token, auth, http, kw)

    @classmethod
    async def from_refresh_token(cls, refresh_token: str, *, identity_host=None,
                                 http: httpx.AsyncClient | None = None, **kw) -> "MyQCommunity":
        """Recommended entry point: refresh a stored token and build a client.

        Refreshing may rotate the refresh token, so the new one is persisted via
        ``on_token_refresh`` immediately. ``identity_host`` overrides the token
        endpoint base for other deployments.
        """
        auth = AuthSession(identity_host=identity_host or const.IDENTITY_HOST, http=http)
        try:
            token = await auth.refresh(refresh_token)
        except BaseException:
            await auth.aclose()
            raise
        return await cls._build(token, auth, http, kw)

    @classmethod
    async def from_redirect_url(cls, redirect_url: str, verifier: str, auth: AuthSession,
                                **kw) -> "MyQCommunity":
        """Finish the interactive flow from the com.myqops://… redirect URL."""
        code = AuthSession.extract_code(redirect_url)
        token = await auth.exchange_code(code, verifier)
        return await cls._build(token, auth, kw.pop("http", None), kw)

    @classmethod
    async def _build(cls, token, auth, http, kw) -> "MyQCommunity":
        client = cls(token, auth=auth, http=http, **kw)
        await client._persist()
        return client

    async def _persist(self) -> None:
        if self._on_token_refresh:
            result = self._on_token_refresh(self._token)
            if inspect.isawaitable(result):
                await result

    # -- token handling -------------------------------------------------------
    @property
    def token(self) -> Token:
        return self._token

    async def _refresh(self, stale_access_token: str) -> None:
        """Refresh once, even if several requests hit an expired token together."""
        async with self._refresh_lock:
            if self._token.access_token != stale_access_token:
                return  # another request already refreshed
            if not self._token.refresh_token:
                raise MyQAuthError("access token expired and no refresh token available")
            self._token = await self._auth.refresh(self._token.refresh_token)
            await self._persist()

    # -- request plumbing -----------------------------------------------------
    async def _send(self, method: str, url: str, headers: dict, **kw) -> httpx.Response:
        try:
            return await self._http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise MyQRequestError(f"{method} {url} failed: {e}") from e

    async def _request(self, method: str, host_key: str, path: str, **kw) -> httpx.Response:
        if self._token.is_expired:
            await self._refresh(self._token.access_token)
        url = f"{self.hosts[host_key].rstrip('/')}/{path.lstrip('/')}"
        headers = {**self._headers, **kw.pop("headers", {})}
        used = self._token.access_token
        headers["Authorization"] = f"Bearer {used}"
        resp = await self._send(method, url, headers, **kw)

        # One retry on 401 (token may have just expired server-side).
        if resp.status_code == 401 and self._token.refresh_token:
            await self._refresh(used)
            headers["Authorization"] = f"Bearer {self._token.access_token}"
            resp = await self._send(method, url, headers, **kw)

        if resp.status_code >= 400:
            raise MyQRequestError(
                f"{method} {url} -> {resp.status_code}",
                status=resp.status_code,
                body=resp.text[:1000],
            )
        return resp

    # -- resources ------------------------------------------------------------
    async def communities(self) -> list[Community]:
        """GET /api/v6/communities — the properties this account belongs to."""
        resp = await self._request("GET", "facilities", f"{const.API_VERSION}/communities")
        return [Community.from_json(c) for c in _items(resp.json())]

    async def devices(self, account_id: str) -> list[Device]:
        """GET /api/v6/communities/{accountId}/devices — doors, locks, panels.

        Follows pagination when the response reports multiple pages, so accounts
        with many devices return everything, not just the first page.
        """
        out: list[Device] = []
        page = 1
        while True:
            params = {} if page == 1 else {"page": page}
            resp = await self._request(
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

    async def locks(self, account_id: str) -> list[Device]:
        """Convenience: only the unlockable devices in a community."""
        return [d for d in await self.devices(account_id) if d.is_lock]

    async def device_action(self, account_id: str, serial_number: str, action: str) -> bool:
        """POST /api/v6/communities/{id}/devices/{serial}/actions/{action}.

        This is the generic device-action endpoint the app uses to actuate access
        panels and locks. ``action`` is ``"unlock"`` or ``"lock"``.
        The body is ``{"action": action}`` (Content-Type JSON).
        """
        resp = await self._request(
            "POST",
            "devices",
            f"{const.API_VERSION}/communities/{account_id}/devices/{serial_number}/actions/{action}",
            json={"action": action},
        )
        return 200 <= resp.status_code < 300

    async def _unlock_panel(self, account_id: str, serial: str) -> bool:
        """Access-panel / gate open.

        POST {panels}/api/v6/communities/{id}/doors/{serial}/unlock, empty JSON body.
        """
        resp = await self._request(
            "POST", "panels",
            f"{const.API_VERSION}/communities/{account_id}/doors/{serial}/unlock",
            headers={"Content-Type": "application/json"}, content=b"",
        )
        return 200 <= resp.status_code < 300

    async def _unlock_lock(self, account_id: str, serial: str) -> bool:
        """Smart-lock unlock — device-action endpoint on the devices host."""
        return await self.device_action(account_id, serial, "unlock")

    async def unlock(self, account_id: str, device_or_serial) -> bool:
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
                if await strategy(account_id, serial):
                    return True
            except MyQRequestError as e:
                last_err = e
                if e.status not in (400, 404, 405):
                    raise  # real failure (auth/permission/etc.), don't mask it
        if last_err:
            raise last_err
        return False

    async def lock(self, account_id: str, serial_number: str) -> bool:
        """Lock a device via the device-action endpoint (``actions/lock``)."""
        return await self.device_action(account_id, serial_number, "lock")

    async def history(self, account_id: str, limit: int | None = 100) -> list[HistoryEvent]:
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
            resp = await self._request(
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

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
        await self._auth.aclose()

    async def __aenter__(self) -> "MyQCommunity":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()
