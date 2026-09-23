"""Exceptions for the Eolia integration."""

from __future__ import annotations


class EoliaError(Exception):
    """Base class for all Eolia integration errors."""


class EoliaAuthError(EoliaError):
    """Raised when an Auth0 token exchange/refresh fails.

    A refresh returning invalid_grant means the refresh_token itself was revoked or
    expired -- the caller should treat this as needing reauth, not a transient failure.
    """

    def __init__(self, message: str, *, invalid_grant: bool = False) -> None:
        super().__init__(message)
        self.invalid_grant = invalid_grant


class EoliaApiError(EoliaError):
    """Raised for a non-2xx response from the Eolia API with a parsed error body."""

    def __init__(self, status: int, code: str | None, message: str) -> None:
        super().__init__(f"{code or status}: {message}")
        self.status = status
        self.code = code
        self.message = message


class EoliaClockSkewError(EoliaApiError):
    """Raised for E-21291-00002 -- the server rejected X-Eolia-Date as too far off.

    This is not something the integration can fix itself; it means the HA host's system
    clock is wrong (the header is always computed fresh in JST, independent of host
    timezone -- see const.EOLIA_DATE_TIMEZONE).
    """


class EoliaDeviceLockedError(EoliaApiError):
    """Raised for E-21291-01718 -- a ~2 minute write lockout.

    The literal message is "controlled by another device, cannot change for 2 minutes",
    but RESOLVED 2026-09-23: not really about a second client at all. A controlled A/B
    test confirmed the real mechanism is operation_token continuity -- every successful
    write returns a fresh operation_token, and echoing it back on the very next write
    avoids this lockout even seconds later, while omitting it reliably triggers it.
    coordinator.py now caches and echoes this automatically, so this exception should be
    rare going forward; if it still fires, it likely means the cached token was lost
    (e.g. HA restarted) or a genuinely different client (the official app, physical
    remote) wrote in between. See tests/fixtures/live_captures/38 for the A/B test.
    """


class EoliaNetworkError(EoliaApiError):
    """Raised for a transport-level failure (timeout, connection error) with no HTTP response.

    Distinguished from EoliaApiError so callers can retry this specific case once --
    unlike an application-level E-21291-* error, a transient network failure may well
    succeed on retry.
    """

    def __init__(self, message: str) -> None:
        super().__init__(0, None, message)
