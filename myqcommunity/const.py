"""Constants for the myQ Community API.

See ``API_REFERENCE.md`` for details on each value.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# OAuth2 / IdentityServer (id.myq.com)
# ---------------------------------------------------------------------------
# Public OAuth client identifier (PKCE, no client secret).
CLIENT_ID = "EF6D53C86E2C4BD1AACE4F3A52C8D8C8.CommunityApp.Android.PKCE.PR"

# Custom URL scheme the authorize endpoint redirects back to. The app registers
# this scheme; we intercept it ourselves to read the ?code= parameter.
REDIRECT_URI = "com.myqops://mobile"

# Scope sent to the /connect/authorize endpoint (note: differs from the token
# call, which the app sends as "MyQ_Community offline_access"; the authorize
# scope is what actually governs the grant).
AUTHORIZE_SCOPE = "MyQBusiness:Community offline_access"
TOKEN_SCOPE = "MyQ_Community offline_access"

# acr_values appended when the "unified_flow" feature flag is on (it is, in prod).
ACR_VALUES = "unified_flow:v1"

# App identity. Sent as the MyQApplicationId header only on terms-accept and
# video-licence endpoints; not needed for unlock.
APP_VERSION = "7.0.6.483631"
MYQ_APPLICATION_ID = "a3RcS5IyIDju7PGpjNOJ6N2lw7TDHA6ecUNAMMvnUtLIY75iGnbOSBWw53mu"

# ---------------------------------------------------------------------------
# Service hosts (production).
#
# The app uses a microservice backend; each endpoint group lives on its own
# host. Override any of these via MyQCommunity(hosts=). The "identity" entry is
# informational; pass identity_host= to the constructors to change auth.
# ---------------------------------------------------------------------------
IDENTITY_HOST = "https://id.myq.com"

HOSTS = {
    "identity": IDENTITY_HOST,
    "facilities": "https://myqb-accounts.myq-cloud.com",
    "accounts_netcore": "https://myqb-accounts-netcore.myq-cloud.com",
    "profile": "https://profiles.myq-cloud.com",
    "devices": "https://myqb-devices-netcore.myq-cloud.com",
    "panels": "https://myqb-panels-netcore.myq-cloud.com",
    "history": "https://myqb-history-netcore.myq-cloud.com",
    "invitations": "https://myqb-invitations.myq-cloud.com",
    "guest_access": "https://myqb-guestaccess.myq-cloud.com",
    "announcements": "https://myqb-pas-alerts.myq-cloud.com",
    "capac": "https://myqb-capac-accountdevice.myq-cloud.com",
    "partners": "https://myqb-accounts-community-partners.myq-cloud.com",
    "general": "https://myqb-general.myq-cloud.com",
    "push_notifications": "https://myqb-notifications.myq-cloud.com",
}

# Endpoint groups used by this library: communities -> "facilities", device
# list + device actions -> "devices", door/gate unlock -> "panels",
# access history -> "history".
API_VERSION = "api/v6"

USER_AGENT = f"myq-community-client/{APP_VERSION}"

# Device families (field `family` in the devices response) that this library
# treats as unlockable. remote_lock = smart locks; access_panel = gates/doors.
LOCK_FAMILIES = {"remote_lock", "access_panel"}
