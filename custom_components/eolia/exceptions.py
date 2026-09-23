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
    """Raised for E-21291-01718 -- a ~2 minute write lockout, cause not fully confirmed.

    The literal message is "controlled by another device, cannot change for 2 minutes",
    and it was first observed right after the official app wrote to the device. But it
    was later reproduced through the real HA integration alone, with no other
    app/client connected at all -- so "another device" doesn't reliably describe the
    actual trigger. Every successful write returns a fresh `operation_token` this
    integration never echoes back; the leading (unconfirmed) theory is a fixed
    hardware/compressor cooldown between any two writes, or something tied to that
    token. Not something the integration can fix itself either way, but distinct from a
    real failure -- a retry after waiting should succeed.
    """


class EoliaNetworkError(EoliaApiError):
    """Raised for a transport-level failure (timeout, connection error) with no HTTP response.

    Distinguished from EoliaApiError so callers can retry this specific case once --
    unlike an application-level E-21291-* error, a transient network failure may well
    succeed on retry.
    """

    def __init__(self, message: str) -> None:
        super().__init__(0, None, message)
