class BrokerError(Exception):
    """Base class for all broker-layer errors."""


class RateLimitedError(BrokerError):
    """Raised when SmartAPI returns a throttle/429-shaped response."""


class AuthenticationError(BrokerError):
    """Raised on login failure. Never retried silently."""


class SessionExpiredError(BrokerError):
    """Raised when a mid-session auth failure is detected (JWT expiry, refresh
    failure, or an explicit auth-error response code). The caller must halt
    trading and surface this — it must never be swallowed and retried forever."""


class InstrumentMasterError(BrokerError):
    """Raised when the instrument master cannot be loaded from network AND no
    usable fallback file exists on disk."""
