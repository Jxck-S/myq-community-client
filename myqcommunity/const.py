"""Constants for the myQ Community API.

All values here were recovered by static analysis of the official
``com.chamberlain.myq.community`` Android app (v7.0.6.483631). See
``API_REFERENCE.md`` in the repo root for the evidence behind each value.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# OAuth2 / IdentityServer (id.myq.com)
# ---------------------------------------------------------------------------
# Public OAuth client identifier embedded in the app (PKCE, no client secret).
# Source: Environment.PROD enum -> field f29088Z.
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
# video-licence endpoints; not needed for unlock. Source: Environment.PROD f29087Y.
APP_VERSION = "7.0.6.483631"
MYQ_APPLICATION_ID = "a3RcS5IyIDju7PGpjNOJ6N2lw7TDHA6ecUNAMMvnUtLIY75iGnbOSBWw53mu"

# ---------------------------------------------------------------------------
# Service hosts (production). Source: assets/urls/prod_api_hosts.json.
#
# The app uses a microservice backend; each endpoint group lives on its own
# host. The mapping of path-prefix -> host below is inferred from the host key
# names and endpoint prefixes and is verified at runtime (the client falls back
# to the accounts host on a 404). Override any of these via MyQCommunity(hosts=).
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

# Which service host serves which endpoint group used by this library.
# Communities list + profile live on the accounts/facilities host; all
# device + door + unit-action endpoints live on the devices host.
API_VERSION = "api/v6"

USER_AGENT = f"myqcommunity-python (reverse-engineered from community-app/{APP_VERSION})"

# Device families (field `family` in the devices response) that this library
# treats as unlockable. remote_lock = smart locks; access_panel = gates/doors.
LOCK_FAMILIES = {"remote_lock", "access_panel"}
