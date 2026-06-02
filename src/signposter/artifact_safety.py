"""Safety checks for local execution artifacts."""

from __future__ import annotations

STALE_OR_FAILOVER_SIGNALS = (
    "provider unavailable",
    "provider error",
    "model unavailable",
    "unsupported model",
    "model is not supported",
    "no model available",
    "failover",
    "fallback provider failed",
    "stale session",
    "session is stale",
    "authentication failed",
    "invalid api key",
    "missing provider token",
    "no provider token environment variable is configured",
)


def find_stale_or_failover_signal(text: str | None) -> str | None:
    """Return the first stale/failover/provider signal found in artifact text."""
    lowered = (text or "").lower()
    for signal in STALE_OR_FAILOVER_SIGNALS:
        if signal in lowered:
            return signal
    return None
