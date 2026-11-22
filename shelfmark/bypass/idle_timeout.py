"""How long the bypass helper may sit unused, shared by the helper and the page-open warm-up."""

from shelfmark.core.config import config

DEFAULT_IDLE_TIMEOUT_SECONDS = 180.0


def helper_idle_timeout() -> float:
    """Return the configured idle timeout, or the default for an unusable value."""
    value = config.get("BYPASS_BROWSER_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT_SECONDS)
    if isinstance(value, bool):
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return DEFAULT_IDLE_TIMEOUT_SECONDS
