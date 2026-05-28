"""Compact, human-friendly GitHub comment formatters for Signposter.

These produce short, deterministic comments for state transitions.
They are intended for use in real GitHub mutations (--apply).
"""

from __future__ import annotations


def format_claim_comment(
    *,
    route: str | None,
    gate: str | None,
    lease_owner: str | None = None,
) -> str:
    """Return a compact claim comment.

    Example:
        **Signposter:** claimed task for local worker run.

        `state:ready → state:active` · `route:worker` · `gate:ci`
    """
    route = route or "unknown"
    gate = gate or "none"

    # Keep it very short. "local worker run" is acceptable for the common case.
    # We avoid leaking full lease_owner unless it's clearly useful.
    header = "**Signposter:** claimed task for local worker run."

    parts = [
        "`state:ready → state:active`",
        f"`route:{route}`",
    ]
    if gate and gate != "none":
        parts.append(f"`gate:{gate}`")

    body = " · ".join(parts)
    return f"{header}\n\n{body}"


def format_release_comment() -> str:
    """Return a compact release comment."""
    return (
        "**Signposter:** released task back to queue.\n\n"
        "`state:active → state:ready` · removed `gate:*`"
    )


def format_complete_comment() -> str:
    """Return a compact complete comment.

    Always notes that the issue remains open (no auto-close).
    """
    return (
        "**Signposter:** completed task.\n\n"
        "`state:active → state:done` · issue remains open"
    )


def format_fail_comment(*, removed_gates: bool = False) -> str:
    """Return a compact fail comment.

    Shows removed gate info only when the transition actually removed gate labels.
    Uses compact form `removed gate:*` per HARDENING-001 adjustment.
    """
    gate_part = " · removed gate:*" if removed_gates else ""
    return (
        "**Signposter:** marked task as failed.\n\n"
        f"`state:active → state:failed`{gate_part}"
    )
