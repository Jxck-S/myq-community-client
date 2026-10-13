"""myqcommunity — an unofficial Python client for the Chamberlain myQ Community API.

Reverse-engineered from the official Android app for personal interoperability
(door/gate unlocks). Not affiliated with or endorsed by Chamberlain Group.
"""

from .api import MyQCommunity
from .auth import AuthSession, MfaChallenge, Token, generate_pkce
from .errors import MyQAuthError, MyQError, MyQRequestError
from .models import Community, Device, HistoryEvent

__version__ = "0.1.0"

__all__ = [
    "MyQCommunity",
    "AuthSession",
    "MfaChallenge",
    "Token",
    "generate_pkce",
    "Community",
    "Device",
    "HistoryEvent",
    "MyQError",
    "MyQAuthError",
    "MyQRequestError",
]
