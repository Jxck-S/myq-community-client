"""myqcommunity — an unofficial Python client for the Chamberlain myQ Community API.

For personal interoperability (door/gate unlocks). Not affiliated with or
endorsed by Chamberlain Group.
"""

from .api import MyQCommunity
from .auth import AuthSession, MfaChallenge, PendingMfa, Token, generate_pkce
from .errors import MyQAuthError, MyQError, MyQOtpError, MyQRequestError
from .models import Community, Device, HistoryEvent

__version__ = "0.2.1"

__all__ = [
    "MyQCommunity",
    "AuthSession",
    "MfaChallenge",
    "PendingMfa",
    "Token",
    "generate_pkce",
    "Community",
    "Device",
    "HistoryEvent",
    "MyQError",
    "MyQAuthError",
    "MyQOtpError",
    "MyQRequestError",
]
