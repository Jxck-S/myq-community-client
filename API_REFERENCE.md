# myQ Community API — reverse-engineered reference

Everything here was recovered by **static analysis** of the official Android app
`com.chamberlain.myq.community` (app version **7.0.6.483631**). No live traffic
was captured (the app does not pin certs, so MITM is possible — see the note
below — it just wasn't needed to produce this). Each fact cites where it came
from in the decompiled app so you can re-verify. Base APK SHA-256:
`4aef2f0a5c531b21d441aa5dbf03d6c57e11a406ee57b8470ee42a78b5420e97`.

> ⚠️ Unofficial. For personal interoperability with your own account. This
> reference comes from the decompiled code, not from intercepted traffic. Field
> shapes come from the app's own bundled demo fixtures (`assets/premium-resident/*.json`,
> `assets/premium-admin/*.json`) and are representative, not exhaustive.
>
> **TLS interception:** the app does **not** pin certificates (OkHttp's
> `CertificatePinner` class is bundled but never instantiated; the network security
> config has only a `<debug-overrides>` block, no pin-set). To MITM, you only need
> to defeat Android's default "don't trust user CAs": use an emulator/rooted device
> with the proxy CA in the **system** store, or repackage the APK with a
> `network_security_config` trusting `src="user"` and re-sign. No unpinning needed.

## App at a glance

- Native Android, Kotlin + Jetpack Compose. Own code under `com.chamberlain.myq.community.*`.
- Networking: Retrofit + OkHttp, form/JSON over HTTPS. Microservice backend on `*.myq-cloud.com`.
- Identity: IdentityServer (OIDC) at `https://id.myq.com`.
- A built-in **demo mode** maps 70 endpoints to canned JSON (`assets/config-demo-endpoints.json`) — a free, offline spec of the API shape.

## Auth — OAuth2 Authorization Code + PKCE

There is **no password (ROPC) grant** in the app. Login is interactive against
IdentityServer, then the app lives off a refresh token.

**Evidence:** `p316ta/o.java` (authorize URL builder), `p360w7/a.java` (token
service interface), `AuthRepositoryImpl$requestAuthToken$2.java`,
`com/chamberlain/myq/community/core/environment/Environment.java` (client ids).

### Config (production)

| Value | Source |
|---|---|
| Identity host | `https://id.myq.com` | `prod_api_hosts.json → api_identity_host` |
| `client_id` | `EF6D53C86E2C4BD1AACE4F3A52C8D8C8.CommunityApp.Android.PKCE.PR` | `Environment.PROD` field `f29088Z` |
| `redirect_uri` | `com.myqops://mobile` | authorize builder + token call |
| authorize `scope` | `MyQBusiness:Community offline_access` | authorize builder |
| token `scope` | `MyQ_Community offline_access` | `requestAuthToken` |
| `acr_values` | `unified_flow:v1` | authorize builder (feature-flag on in prod) |
| PKCE | `S256`, verifier = base64url(32 random bytes) | authorize builder |

Other embedded client ids (not needed for unlock): DEV
`…CommunityApp.Android.PKCE.Dev`, PREPROD `…CommunityApp.Android.PKCE.PP`.

### 1. Authorize (user-interactive, in a browser)

```
GET https://id.myq.com/connect/authorize
  ?response_type=code
  &client_id=EF6D53C86E2C4BD1AACE4F3A52C8D8C8.CommunityApp.Android.PKCE.PR
  &scope=MyQBusiness:Community offline_access
  &redirect_uri=com.myqops://mobile
  &code_challenge=<base64url(sha256(verifier))>
  &code_challenge_method=S256
  &acr_values=unified_flow:v1
```

User signs in; IdentityServer redirects to `com.myqops://mobile?code=<AUTH_CODE>`.

### 2. Token exchange

```
POST https://id.myq.com/connect/token
Content-Type: application/x-www-form-urlencoded

response_type=code
scope=MyQ_Community offline_access
redirect_uri=com.myqops://mobile
client_id=EF6D53C86E2C4BD1AACE4F3A52C8D8C8.CommunityApp.Android.PKCE.PR
grant_type=authorization_code
code=<AUTH_CODE>
code_verifier=<PKCE_VERIFIER>
```

→ `{ access_token, refresh_token, expires_in, token_type }` (`TokenResponse`).

### 3. Refresh

```
POST https://id.myq.com/connect/token
client_id=<client_id>&grant_type=refresh_token&scope=MyQ_Community offline_access&refresh_token=<RT>
```

(Revocation exists too: `POST /connect/revocation`.)

## Authenticated REST requests

The main OkHttp interceptor (`R7/g.java`) is minimal:

- **`Authorization: Bearer <access_token>`** on every request (when a token exists).
- `MyQApplicationId: a3RcS5Iy…53mu` only on terms-accept / video-licence paths (not unlock). Source: `Environment.PROD` field `f29087Y`.
- The `X-CG-Client-Id` / `X-CG-Tenant-Id` headers are used **only** by the video-calling service (`com.chamberlaingroup.video.webrtc.*`), which this project skips.

So for unlocks you only need the bearer token.

## Service hosts

The backend is split by service (`prod_api_hosts.json`). Mapping of the endpoints
this project uses:

| Endpoint group | Host |
|---|---|
| `/api/v6/communities`, `/my/profile` | `https://myqb-accounts.myq-cloud.com` (facilities/accounts) |
| devices / doors / unit actions | `https://myqb-devices-netcore.myq-cloud.com` |
| history / events | `https://myqb-history-netcore.myq-cloud.com` |
| guest/visitor passes | `https://myqb-guestaccess.myq-cloud.com` |

> The devices-host assignment is inferred from host names + path prefixes; confirm
> against your account and adjust `MyQCommunity(hosts=...)` if a call 404s.

## Endpoints

### List communities
`GET {facilities}/api/v6/communities`
```jsonc
{ "count": 1, "items": [
  { "id": "d68c16b6-9ff3-4295-93f7-c6eb6530a7dc",   // a GUID on real accounts
    "name": "AVE Las Colinas", "active": true, "timezone": "America/Chicago",
    "address": {...}, "feature_flags": ["myqb_comm_remoteunlock", ...],
    "features": {...}, "directory_code_length": 4 } ] }
```
`id` is the **`{accountId}`** used in every endpoint below. On real accounts it's
a GUID (the demo fixtures use `COMM-…` placeholders). The `myqb_comm_remoteunlock`
feature flag indicates remote unlock is enabled for that community.
Source interface: `@f("api/v6/communities")`.

### List devices
`GET {devices}/api/v6/communities/{accountId}/devices`

**Live response (verified against a real account):**
```jsonc
{ "count": 1, "items": [
  { "serial_number": "MDTE2-5Q-YY5HB_Door1",
    "device_family": "access_panel",       // NOT "family"
    "device_type":   "access_panel_door",  // NOT "type"
    "name":  "Gate",                        // top-level, NOT configuration.name
    "zone":  "Default Zone",
    "pass_through_interval_seconds": 10,
    "online": true,                         // top-level, NOT state.online
    "kind":  "door" } ] }                    // "door" => unlockable
```
Source: `L7/a.java` `@f("/api/v6/communities/{accountId}/devices")`.
Unlockable = `kind == "door"` (families seen: `access_panel`; `remote_lock` for smart locks).

> The app's bundled **demo fixtures** use a different, nested shape
> (`family`/`type`/`configuration.name`/`state.online`). The live API is the flat
> shape above. `Device.from_json` accepts **both**.

### 🔓 Unlock / open — ✅ VERIFIED by live capture

Captured the official app opening an access-panel **gate** via emulator + mitmproxy
(the app does not pin certs). The real call:

```
POST https://myqb-panels-netcore.myq-cloud.com/api/v6/communities/{accountId}/doors/{serialNumber}/unlock
Authorization: Bearer <token>
Content-Type: application/json
(empty body)
→ 200
```

**Key correction:** the path `/doors/{serialNumber}/unlock` is on the **panels**
host (`myqb-panels-netcore.myq-cloud.com`), *not* the devices host — hitting the
devices host returns 404. Source interface `z7/e.java`; the DI binds it to the
panels base URL. Confirmed end-to-end: the library's `unlock()` returns 200 and
the app UI flips the gate to "Unlocked".

**Smart locks (`device_family: remote_lock`)** use a different call —
`POST {devices}/api/v6/communities/{id}/devices/{serial}/actions/unlock` body
`{"action":"unlock"}` (`DeviceActionTypeDto` = lock/unlock; `z7/a.java`,
`z7/d.java:49`). Exposed as `device_action(...)`. Access panels reject that
(`"Action type not applicable"`) — they use the `/doors/.../unlock` panels-host
call above, which is what `unlock()` does.

### Unit action (alternate actuation for in-unit devices)
`POST {devices}/api/v6/communities/{accountId}/unit_groups/{unitGroupId}/units/{unitId}/actions/{actionKey}`
with a JSON `UnitActionBody`. Source: `p263q7/a.java`.

### Access history — ✅ VERIFIED live
`GET {history}/api/v6/communities/{accountId}/history?continue={cursor}`
```jsonc
{ "count": N, "continue": "<cursor-or-null>", "items": [
  { "id": "...", "event": "unlocked", "device_name": "Gate",
    "serial_number": "M2TE2-5Q-YY5HB", "timestamp": "2026-10-13T04:21:07Z" } ] }
```
Newest first; cursor pagination via the `continue` query param (the previous
response's `continue` value). On the **history** host (`myqb-history-netcore`).
Source: `D7/a.java` `@f("api/v6/communities/{accountId}/history")`, `@t("continue")`.
Library: `history(account_id, limit=...)`.

### Other endpoints seen (not implemented; for reference)
`my/profile`, `my/features`, `countries`; `communities/{id}/users`, `/unit_groups`,
`/zones`, `/entry_code`, `/announcements`, `/history`, `/events`,
`/events/{id}/actions/{actionKey}` (visitor passes), `/devices/override`,
`/devices/manager`, `/mobilecredentials`, `/accounts/{id}/sip/credentials`,
`notifications/phones/{id}`. Full list in `assets/config-demo-endpoints.json`.

## Verification status

| Item | Confidence | Notes |
|---|---|---|
| OAuth URLs, client_id, PKCE, scopes | **Verified live** | token + refresh exchange succeed |
| Bearer-only auth for REST | **Verified live** | authenticated GETs succeed |
| Headless login + MFA (SMS/email OTP) | **Verified live** | server-rendered form; `MyQCommunity.login()` |
| Refresh-token rotation + expiry refresh | **Verified live** | myQ rotates RT on every refresh — must persist |
| `communities` host = `myqb-accounts` | **Verified live** | — |
| `devices`/unlock host = `myqb-devices-netcore` | **Verified live** (list) | unlock path built from code, not yet fired |
| `devices` response field names | **Verified live** | flat shape (see above), not the demo nesting |
| Unlock (`/doors/{serial}/unlock` on **panels** host) | **Verified live** | app capture + library returns 200 |
| Other endpoints (passes, history, etc.) | Medium (Retrofit annotations) | not exercised here |
