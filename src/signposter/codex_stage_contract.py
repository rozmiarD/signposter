"""Codex-native stage contract for H048 roadmap work."""

from __future__ import annotations

from dataclasses import dataclass

STAGE_ID = "H048"
DESIRED_DEFAULT_BACKEND = "codex-cli"
LEGACY_BACKEND = "openclaw"


@dataclass(frozen=True)
class CodexStageRule:
    key: str
    text: str


MANDATORY_CODEX_STAGE_RULES: tuple[CodexStageRule, ...] = (
    CodexStageRule(
        key="default-backend",
        text="Codex CLI is the desired default backend for new execution work.",
    ),
    CodexStageRule(
        key="legacy-openclaw",
        text="OpenClaw is legacy compatibility or explicit fallback only.",
    ),
    CodexStageRule(
        key="dry-run-first",
        text="Dry-run planning must precede apply or execution mutations.",
    ),
    CodexStageRule(
        key="gates-preserved",
        text=(
            "Existing lifecycle, gate, review, merge, integration, and cleanup "
            "semantics remain authoritative."
        ),
    ),
    CodexStageRule(
        key="bounded-artifacts",
        text="Raw execution output remains local and GitHub reports use bounded summaries.",
    ),
)


def codex_stage_contract() -> dict[str, object]:
    """Return a compact machine-readable contract for Codex-native roadmap tasks."""
    return {
        "stage": STAGE_ID,
        "desired_default_backend": DESIRED_DEFAULT_BACKEND,
        "legacy_backend": LEGACY_BACKEND,
        "rules": [{"key": rule.key, "text": rule.text} for rule in MANDATORY_CODEX_STAGE_RULES],
        "notes": [
            "Local contract only.",
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            "No Codex CLI execution was performed.",
        ],
    }
