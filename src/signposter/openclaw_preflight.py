"""OpenClaw execution preflight checks.

Conservative local checks only. Never prints token values.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

DEFAULT_PROVIDER_TOKEN_ENVS = (
    "SIGNPOSTER_OPENCLAW_PROVIDER_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
)


@dataclass(frozen=True)
class OpenClawPreflightResult:
    ok: bool
    reason: str
    checked_token_envs: tuple[str, ...]
    openclaw_path: str | None
    manual_fallback: str


def _configured_token_envs(env: dict[str, str] | None = None) -> tuple[str, ...]:
    env = env or os.environ
    configured = env.get("SIGNPOSTER_OPENCLAW_PROVIDER_TOKEN_ENV", "")
    extra = tuple(item.strip() for item in configured.split(",") if item.strip())
    return (*DEFAULT_PROVIDER_TOKEN_ENVS, *extra)


def check_openclaw_preflight(
    *,
    artifact_kind: str,
    target: int,
    env: dict[str, str] | None = None,
) -> OpenClawPreflightResult:
    """Check that OpenClaw execution is likely available before running it."""
    env = env or os.environ
    token_envs = _configured_token_envs(env)
    fallback = _manual_fallback_command(artifact_kind, target)
    openclaw_path = shutil.which("openclaw")

    if not openclaw_path:
        return OpenClawPreflightResult(
            ok=False,
            reason="OpenClaw CLI not found on PATH",
            checked_token_envs=token_envs,
            openclaw_path=None,
            manual_fallback=fallback,
        )

    if not any(env.get(name) for name in token_envs):
        return OpenClawPreflightResult(
            ok=False,
            reason="no provider token environment variable is configured",
            checked_token_envs=token_envs,
            openclaw_path=openclaw_path,
            manual_fallback=fallback,
        )

    return OpenClawPreflightResult(
        ok=True,
        reason="OpenClaw CLI and provider token environment are present",
        checked_token_envs=token_envs,
        openclaw_path=openclaw_path,
        manual_fallback=fallback,
    )


def format_openclaw_preflight_block(result: OpenClawPreflightResult) -> str:
    """Format a non-secret preflight failure for CLI output."""
    lines = [
        "OpenClaw preflight blocked execution.",
        f"Reason: {result.reason}",
        f"OpenClaw CLI: {'present' if result.openclaw_path else 'missing'}",
        "Provider token env checked:",
    ]
    for name in result.checked_token_envs:
        status = "present" if os.environ.get(name) else "missing"
        lines.append(f"- {name}: {status}")
    lines.extend(
        [
            "No OpenClaw execution was started.",
            "No run artifacts were written.",
            f"Manual fallback: {result.manual_fallback}",
        ]
    )
    return "\n".join(lines)


def _manual_fallback_command(artifact_kind: str, target: int) -> str:
    if artifact_kind == "review":
        return f"signposter artifact write-review-summary --pr {target} --apply"
    return f"signposter artifact write-worker-summary --issue {target} --apply"
