# myQ Community API reference

Protocol notes for the Chamberlain myQ Community backend, as used by this client.

> ⚠️ Unofficial. For personal interoperability with your own account and devices.
> Field shapes are representative and can vary by account; models keep the full
> raw JSON in `.raw`.

## Auth — OAuth2 Authorization Code + PKCE

Login is interactive against IdentityServer, then the client uses a refresh token.

### Config (production)

| Value | |
|---|---|
| Identity host | `https://id.myq.com` (the app also uses `https://identity.myqbusiness.com`) |
| `client_id` | `EF6D53C86E2C4BD1AACE4F3A52C8D8C8.CommunityApp.Android.PKCE.PR` |
| `redirect_uri` | `com.myqops://mobile` |
| authorize `scope` | `MyQBusiness:Community offline_access` |
| token `scope` | `MyQ_Community offline_access` |
| `acr_values` | `unified_flow:v1` |
| PKCE | `S256`, verifier = base64url(32 random bytes) |

### 1. Authorize (interactive)

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

The hosted login is a standard server-rendered form (`Email`, `Password`,
`__RequestVerificationToken`, `ReturnUrl`). Most accounts then require an MFA
one-time code (SMS or email) on an OTP form (field `Otp`). On success the server
redirects to `com.myqops://mobile?code=<AUTH_CODE>`.

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

→ `{ access_token, refresh_token, expires_in, token_type }`.

### 3. Refresh

```
POST https://id.myq.com/connect/token
client_id=<client_id>&grant_type=refresh_token&scope=MyQ_Community offline_access&refresh_token=<RT>
```

> A new refresh token is issued on every refresh — persist the newest one. The
> previous token was observed to remain valid (no strict one-time use), but
> don't rely on it. (Revocation: `POST /connect/revocation`.)

## Authenticated REST requests

Add `Authorization: Bearer <access_token>` to every request. That's all that's
needed for the endpoints below.

## Service hosts

The backend is split by service (production `*.myq-cloud.com`):

| Endpoint group | Host |
|---|---|
| `/communities`, `/my/profile` | `https://myqb-accounts.myq-cloud.com` |
| devices list / device actions | `https://myqb-devices-netcore.myq-cloud.com` |
| door / gate unlock | `https://myqb-panels-netcore.myq-cloud.com` |
| history | `https://myqb-history-netcore.myq-cloud.com` |

All paths below are prefixed with `api/v6`.

## Endpoints

### List communities
`GET {accounts}/api/v6/communities`
```jsonc
{ "count": 1, "items": [
  { "id": "<guid>", "name": "...", "active": true, "timezone": "...",
    "address": {...}, "feature_flags": [...], "directory_code_length": 4 } ] }
```
`id` is the `{accountId}` used in every endpoint below. `feature_flags` may
include `myqb_comm_remoteunlock`, `myqb_comm_entrycode`, etc.

### List devices
`GET {devices}/api/v6/communities/{accountId}/devices`
```jsonc
{ "count": 1, "items": [
  { "serial_number": "...", "device_family": "access_panel",
    "device_type": "access_panel_door", "name": "Gate", "zone": "...",
    "pass_through_interval_seconds": 10, "online": true, "kind": "door" } ] }
```
Device families: `access_panel` (gates/community doors), `remote_lock` (smart
locks), `capac`. `kind: "door"` marks an unlockable entry point.

### 🔓 Unlock / open

**Access panels / gates (`device_family: access_panel`):**
```
POST {panels}/api/v6/communities/{accountId}/doors/{serialNumber}/unlock
Authorization: Bearer <token>
Content-Type: application/json
(empty body)  → 200
```
Note the **panels** host — the devices host returns 404 for this path.

**Smart locks (`device_family: remote_lock`):**
```
POST {devices}/api/v6/communities/{accountId}/devices/{serialNumber}/actions/unlock
body {"action":"unlock"}   (lock with "lock")
```

### Access history
`GET {history}/api/v6/communities/{accountId}/history?continue={cursor}`
```jsonc
{ "count": N, "continue": "<cursor-or-null>", "items": [
  { "id": "...", "event": "unlocked", "device_name": "Gate",
    "serial_number": "...", "timestamp": "2026-10-13T04:21:07Z" } ] }
```
Newest first; cursor pagination via the `continue` query param.

### Other endpoints (not implemented here)
`my/profile`, `my/features`, `countries`; `communities/{id}/users`,
`/unit_groups`, `/zones`, `/entry_code`, `/announcements`, `/events`,
`/devices/override`, `/mobilecredentials`, `notifications/phones/{id}`.
