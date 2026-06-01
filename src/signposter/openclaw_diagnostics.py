"""Read-only OpenClaw runtime diagnostics for Signposter."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from signposter.role_policy import ACTIVE_ROLE_POLICIES, ALLOWED_OPENCLAW_MODELS


@dataclass(frozen=True)
class OpenClawRuntimeDiagnostics:
    """Parsed runtime/config status from `openclaw models status`."""

    available: bool
    command_ok: bool
    default_model: str | None
    fallbacks: tuple[str, ...]
    aliases: dict[str, str]
    configured_models: tuple[str, ...]
    expired_auth_entries: tuple[str, ...]
    warnings: tuple[str, ...]
    raw_output: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.available and self.command_ok and not self.warnings


def _split_csv_payload(payload: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in payload.split(",") if item.strip())


def parse_openclaw_models_status(output: str) -> OpenClawRuntimeDiagnostics:
    """Parse `openclaw models status` text into a structured diagnostics snapshot."""
    default_model: str | None = None
    fallbacks: tuple[str, ...] = ()
    aliases: dict[str, str] = {}
    configured_models: tuple[str, ...] = ()
    expired_auth_entries: list[str] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Default"):
            _, _, value = line.partition(":")
            default_model = value.strip() or None
        elif line.startswith("Fallbacks"):
            _, _, value = line.partition(":")
            fallbacks = _split_csv_payload(value)
        elif line.startswith("Aliases"):
            _, _, value = line.partition(":")
            alias_pairs = _split_csv_payload(value)
            parsed: dict[str, str] = {}
            for pair in alias_pairs:
                if "->" not in pair:
                    continue
                alias, target = pair.split("->", 1)
                parsed[alias.strip()] = target.strip()
            aliases = parsed
        elif line.startswith("Configured models"):
            _, _, value = line.partition(":")
            configured_models = _split_csv_payload(value)
        elif "expired expires in" in line.lower():
            expired_auth_entries.append(line)

    warnings: list[str] = []
    active_models = {policy.model for policy in ACTIVE_ROLE_POLICIES.values()}

    if default_model and default_model not in ALLOWED_OPENCLAW_MODELS:
        warnings.append(f"default model is outside Signposter allowed set: {default_model}")

    forbidden_models = [
        model for model in configured_models
        if model not in ALLOWED_OPENCLAW_MODELS
    ]
    if forbidden_models:
        warnings.append(
            "configured models include entries outside the active Signposter policy: "
            + ", ".join(forbidden_models)
        )

    fallback_drift = [model for model in fallbacks if model not in active_models]
    if fallback_drift:
        warnings.append(
            "fallback models drift from the active Signposter role policy: "
            + ", ".join(fallback_drift)
        )

    forbidden_aliases = [
        f"{alias} -> {target}"
        for alias, target in aliases.items()
        if target not in ALLOWED_OPENCLAW_MODELS
    ]
    if forbidden_aliases:
        warnings.append(
            "aliases resolve to unavailable or forbidden models: "
            + ", ".join(forbidden_aliases)
        )

    if expired_auth_entries:
        warnings.append(
            "expired provider auth entries remain configured: "
            + "; ".join(expired_auth_entries)
        )

    return OpenClawRuntimeDiagnostics(
        available=True,
        command_ok=True,
        default_model=default_model,
        fallbacks=fallbacks,
        aliases=aliases,
        configured_models=configured_models,
        expired_auth_entries=tuple(expired_auth_entries),
        warnings=tuple(warnings),
        raw_output=output,
        error=None,
    )


def gather_openclaw_runtime_diagnostics(
    *,
    timeout: int = 15,
) -> OpenClawRuntimeDiagnostics:
    """Run a read-only OpenClaw runtime/config diagnostics snapshot."""
    try:
        proc = subprocess.run(
            ["openclaw", "models", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return OpenClawRuntimeDiagnostics(
            available=False,
            command_ok=False,
            default_model=None,
            fallbacks=(),
            aliases={},
            configured_models=(),
            expired_auth_entries=(),
            warnings=(),
            raw_output="",
            error="openclaw command not found",
        )
    except subprocess.TimeoutExpired:
        return OpenClawRuntimeDiagnostics(
            available=True,
            command_ok=False,
            default_model=None,
            fallbacks=(),
            aliases={},
            configured_models=(),
            expired_auth_entries=(),
            warnings=("openclaw models status timed out",),
            raw_output="",
            error="openclaw models status timed out",
        )

    output = (proc.stdout or "") + (f"\n{proc.stderr}" if proc.stderr else "")
    if proc.returncode != 0:
        return OpenClawRuntimeDiagnostics(
            available=True,
            command_ok=False,
            default_model=None,
            fallbacks=(),
            aliases={},
            configured_models=(),
            expired_auth_entries=(),
            warnings=(f"openclaw models status exited {proc.returncode}",),
            raw_output=output,
            error=f"openclaw models status exited {proc.returncode}",
        )

    return parse_openclaw_models_status(output)
