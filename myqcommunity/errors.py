"""Exception types for the myQ Community client."""

from __future__ import annotations


class MyQError(Exception):
    """Base class for all myQ Community errors."""


class MyQAuthError(MyQError):
    """Authentication or token-refresh failed."""


class MyQOtpError(MyQAuthError):
    """The one-time (MFA) code was rejected; the pending login can be retried."""


class MyQRequestError(MyQError):
    """An API request returned an error status."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
