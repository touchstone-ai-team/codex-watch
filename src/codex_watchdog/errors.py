"""Exception types for user-facing CLI failures."""


class WatchdogError(RuntimeError):
    """Base class for expected watchdog CLI failures."""


class DiscoveryError(WatchdogError):
    """Raised when no unique safe Codex target can be discovered."""


class CommandError(WatchdogError):
    """Raised when an external command fails."""
