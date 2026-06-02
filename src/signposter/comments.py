"""Compact, human-friendly GitHub comment formatters for Signposter.

These produce short, deterministic comments for state transitions.
They are intended for use in real GitHub mutations (--apply).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MAX_COMMENT_CHARS = 6000
TRANSITION_COMMENT_MAX_CHARS = 240
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_AUTO_CLOSE_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*(?:issue\s+)?#\d+\b",
    re.IGNORECASE,
)
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "reviewer token assignment",
        re.compile(r"\bSIGNPOSTER_REVIEWER_GH_TOKEN\s*=\s*\S+", re.IGNORECASE),
    ),
    ("GitHub token", re.compile(r"\b(?:github_pat|gh[pousr])_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "private key block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
)


@dataclass(frozen=True)
class CommentAuditResult:
    """Structured audit result for a GitHub comment body."""

    valid: bool
    errors: tuple[str, ...]
    notes: tuple[str, ...]
    char_count: int


def audit_github_comment_body(
    body: str,
    *,
    max_chars: int = DEFAULT_MAX_COMMENT_CHARS,
    require_signposter_marker: bool = True,
    allow_auto_close_keywords: bool = False,
) -> CommentAuditResult:
    """Return a compact safety/format audit for a Signposter GitHub comment."""
    text = body or ""
    errors: list[str] = []
    notes: list[str] = []

    if not text.strip():
        errors.append("comment body is empty")

    if len(text) > max_chars:
        errors.append(f"comment body exceeds {max_chars} chars")

    if require_signposter_marker and "signposter" not in text.lower():
        errors.append("comment body does not identify Signposter")

    if _ANSI_ESCAPE_RE.search(text):
        errors.append("comment body contains ANSI escape sequences")

    if not allow_auto_close_keywords and _AUTO_CLOSE_KEYWORD_RE.search(text):
        errors.append("comment body contains an auto-close keyword")

    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            errors.append(f"comment body contains possible {label}")

    if not errors:
        notes.append("comment body is bounded and Signposter-owned")

    return CommentAuditResult(
        valid=not errors,
        errors=tuple(errors),
        notes=tuple(notes),
        char_count=len(text),
    )


def _redaction_marker(label: str) -> str:
    return "[REDACTED:" + label.lower().replace(" ", "-") + "]"


def redact_github_comment_body(body: str) -> str:
    """Redact obvious secret-like material before a body can reach GitHub."""
    redacted = body or ""
    for label, pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_redaction_marker(label), redacted)
    return redacted


def ensure_github_comment_body(body: str, **kwargs: object) -> str:
    """Return body when safe enough for GitHub, otherwise raise ValueError."""
    redacted = redact_github_comment_body(body)
    audit = audit_github_comment_body(redacted, **kwargs)
    if not audit.valid:
        raise ValueError("unsafe GitHub comment body: " + "; ".join(audit.errors))
    return redacted


def ensure_transition_comment_body(body: str) -> str:
    """Return body when it fits the compact state-transition comment budget."""
    return ensure_github_comment_body(body, max_chars=TRANSITION_COMMENT_MAX_CHARS)


def format_claim_comment(
    *,
    route: str | None,
    gate: str | None,
    lease_owner: str | None = None,
) -> str:
    """Return a compact claim comment.

    Example:
        **Signposter:** claimed task.

        `state:ready → state:active` · `route:worker` · `gate:ci`
    """
    route = route or "unknown"
    gate = gate or "none"

    # Route and gate chips carry the operational detail; keep the prose short.
    header = "**Signposter:** claimed task."

    parts = [
        "`state:ready → state:active`",
        f"`route:{route}`",
    ]
    if gate and gate != "none":
        parts.append(f"`gate:{gate}`")

    body = " · ".join(parts)
    return ensure_transition_comment_body(f"{header}\n\n{body}")


def format_release_comment() -> str:
    """Return a compact release comment."""
    return ensure_transition_comment_body(
        "**Signposter:** released task.\n\n"
        "`state:active → state:ready` · removed `gate:*`"
    )


def format_complete_comment() -> str:
    """Return a compact complete comment.

    Always notes that the issue remains open (no auto-close).
    """
    return ensure_transition_comment_body(
        "**Signposter:** completed task.\n\n"
        "`state:active → state:done` · issue remains open"
    )


def format_fail_comment(*, removed_gates: bool = False) -> str:
    """Return a compact fail comment.

    Shows removed gate info only when the transition actually removed gate labels.
    Uses compact form `removed gate:*` per HARDENING-001 adjustment.
    """
    gate_part = " · removed gate:*" if removed_gates else ""
    return ensure_transition_comment_body(
        "**Signposter:** marked task failed.\n\n"
        f"`state:active → state:failed`{gate_part}"
    )
