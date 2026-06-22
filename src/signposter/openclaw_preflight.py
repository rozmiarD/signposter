"""OpenClaw execution preflight checks.

Conservative local checks only. Never prints token values.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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
    auth_config_path: str | None
    auth_profile_count: int
    manual_fallback: str


def _configured_token_envs(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    effective: Mapping[str, str] = os.environ if env is None else env
    configured = effective.get("SIGNPOSTER_OPENCLAW_PROVIDER_TOKEN_ENV", "")
    extra = tuple(item.strip() for item in configured.split(",") if item.strip())
    return (*DEFAULT_PROVIDER_TOKEN_ENVS, *extra)


def _openclaw_config_path(env: Mapping[str, str]) -> Path:
    configured = env.get("OPENCLAW_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()

    openclaw_home = env.get("OPENCLAW_HOME", "").strip()
    if openclaw_home:
        return Path(openclaw_home).expanduser() / "openclaw.json"

    return Path.home() / ".openclaw" / "openclaw.json"


def _count_usable_auth_profiles(env: Mapping[str, str]) -> tuple[str, int, bool]:
    config_path = _openclaw_config_path(env)

    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return str(config_path), 0, False
    except OSError:
        return str(config_path), 0, True

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return str(config_path), 0, True

    auth = payload.get("auth")
    if not isinstance(auth, dict):
        return str(config_path), 0, False

    profiles = auth.get("profiles")
    if not isinstance(profiles, dict):
        return str(config_path), 0, False

    usable_profiles = 0
    for profile in profiles.values():
        if not isinstance(profile, dict):
            continue
        provider = str(profile.get("provider") or "").strip()
        mode = str(profile.get("mode") or "").strip()
        if provider and mode:
            usable_profiles += 1

    return str(config_path), usable_profiles, False


def check_openclaw_preflight(
    *,
    artifact_kind: str,
    target: int,
    env: Mapping[str, str] | None = None,
) -> OpenClawPreflightResult:
    """Check that OpenClaw execution is likely available before running it."""
    effective: Mapping[str, str] = os.environ if env is None else env
    token_envs = _configured_token_envs(effective)
    fallback = _manual_fallback_command(artifact_kind, target)
    openclaw_path = shutil.which("openclaw")

    if not openclaw_path:
        return OpenClawPreflightResult(
            ok=False,
            reason="OpenClaw CLI not found on PATH",
            checked_token_envs=token_envs,
            openclaw_path=None,
            auth_config_path=None,
            auth_profile_count=0,
            manual_fallback=fallback,
        )

    if any(effective.get(name) for name in token_envs):
        return OpenClawPreflightResult(
            ok=True,
            reason="OpenClaw CLI and provider token environment are present",
            checked_token_envs=token_envs,
            openclaw_path=openclaw_path,
            auth_config_path=None,
            auth_profile_count=0,
            manual_fallback=fallback,
        )

    auth_config_path, auth_profile_count, auth_config_unreadable = _count_usable_auth_profiles(
        effective
    )
    if auth_profile_count > 0:
        return OpenClawPreflightResult(
            ok=True,
            reason="OpenClaw CLI and auth profile config are present",
            checked_token_envs=token_envs,
            openclaw_path=openclaw_path,
            auth_config_path=auth_config_path,
            auth_profile_count=auth_profile_count,
            manual_fallback=fallback,
        )

    if auth_config_unreadable:
        return OpenClawPreflightResult(
            ok=False,
            reason=(
                "no provider token environment variable is configured and "
                "OpenClaw auth profile config could not be read"
            ),
            checked_token_envs=token_envs,
            openclaw_path=openclaw_path,
            auth_config_path=auth_config_path,
            auth_profile_count=0,
            manual_fallback=fallback,
        )

    return OpenClawPreflightResult(
        ok=False,
        reason=(
            "no provider token environment variable is configured and "
            "no usable OpenClaw auth profile was found"
        ),
        checked_token_envs=token_envs,
        openclaw_path=openclaw_path,
        auth_config_path=auth_config_path,
        auth_profile_count=0,
        manual_fallback=fallback,
    )


def format_openclaw_preflight_block(result: OpenClawPreflightResult) -> str:
    """Format a non-secret preflight failure for CLI output."""
    lines = [
        "OpenClaw preflight blocked execution.",
        f"Reason: {result.reason}",
        f"OpenClaw CLI: {'present' if result.openclaw_path else 'missing'}",
        (
            "OpenClaw auth profile config: "
            f"{result.auth_profile_count} usable profile(s) at {result.auth_config_path}"
            if result.auth_config_path
            else "OpenClaw auth profile config: not checked"
        ),
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
