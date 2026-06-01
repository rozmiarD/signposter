"""Shared OpenClaw execution timeout and diagnosis helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass

from signposter.artifact_safety import find_stale_or_failover_signal

DEFAULT_OPENCLAW_EXECUTE_TIMEOUT_SECONDS = 120
DEFAULT_OPENCLAW_SUBPROCESS_TIMEOUT_SECONDS = 135
OPENCLAW_EXECUTE_TIMEOUT_ENV = "SIGNPOSTER_OPENCLAW_EXECUTE_TIMEOUT_SECONDS"
OPENCLAW_SUBPROCESS_TIMEOUT_ENV = "SIGNPOSTER_OPENCLAW_SUBPROCESS_TIMEOUT_SECONDS"


@dataclass(frozen=True)
class OpenClawExecutionDiagnosis:
    status: str
    reason: str
    remediation: tuple[str, ...]
    signal: str | None = None


@dataclass(frozen=True)
class OpenClawTimeoutSettings:
    execute_timeout: int
    subprocess_timeout: int
    warnings: tuple[str, ...] = ()


def _parse_timeout_setting(
    env_name: str,
    default: int,
    *,
    env: dict[str, str] | None = None,
) -> tuple[int, str | None]:
    source = env if env is not None else os.environ
    value = source.get(env_name, "").strip()
    if not value:
        return default, None
    try:
        parsed = int(value)
    except ValueError:
        return default, f"{env_name} is invalid ({value!r}); defaulting to {default}s"
    if parsed <= 0:
        return default, f"{env_name} must be > 0; defaulting to {default}s"
    return parsed, None


def openclaw_timeout_settings(env: dict[str, str] | None = None) -> OpenClawTimeoutSettings:
    execute_timeout, execute_warning = _parse_timeout_setting(
        OPENCLAW_EXECUTE_TIMEOUT_ENV,
        DEFAULT_OPENCLAW_EXECUTE_TIMEOUT_SECONDS,
        env=env,
    )
    subprocess_timeout, subprocess_warning = _parse_timeout_setting(
        OPENCLAW_SUBPROCESS_TIMEOUT_ENV,
        DEFAULT_OPENCLAW_SUBPROCESS_TIMEOUT_SECONDS,
        env=env,
    )
    warnings = [warning for warning in (execute_warning, subprocess_warning) if warning]
    if subprocess_timeout <= execute_timeout:
        subprocess_timeout = execute_timeout + 15
        warnings.append(
            f"{OPENCLAW_SUBPROCESS_TIMEOUT_ENV} must exceed "
            f"{OPENCLAW_EXECUTE_TIMEOUT_ENV}; adjusted to {subprocess_timeout}s"
        )
    return OpenClawTimeoutSettings(
        execute_timeout=execute_timeout,
        subprocess_timeout=subprocess_timeout,
        warnings=tuple(warnings),
    )


def classify_openclaw_execution(
    *,
    exit_code: int | None,
    combined_output: str,
    timed_out: bool,
    diagnostics_warnings: tuple[str, ...] = (),
    timeout_seconds: int | None = None,
) -> OpenClawExecutionDiagnosis:
    """Classify bounded OpenClaw execution outcomes for worker/reviewer runs."""
    if timed_out:
        return OpenClawExecutionDiagnosis(
            status="timeout",
            reason=(
                "OpenClaw execution exceeded the bounded subprocess timeout"
                + (f" ({timeout_seconds}s)." if timeout_seconds else ".")
            ),
            remediation=(
                "Inspect the local raw artifact for the exact stall point.",
                "Retry only after validating provider auth and runtime health.",
                "Use the manual Signposter artifact fallback if the task must continue now.",
            ),
        )

    if exit_code == 0:
        return OpenClawExecutionDiagnosis(
            status="success",
            reason="OpenClaw execution completed successfully.",
            remediation=(),
        )

    lowered = combined_output.lower()

    if (
        "token_invalidated" in lowered
        or "authentication failed" in lowered
        or "auth refresh request timed out" in lowered
        or "status=401" in lowered
        or '"status":401' in lowered
        or "incorrect api key" in lowered
        or "no provider token environment variable is configured" in lowered
    ):
        return OpenClawExecutionDiagnosis(
            status="auth-provider-failure",
            reason="OpenClaw reported an auth or provider credential/runtime failure.",
            remediation=(
                "Refresh the active provider auth profile and rerun doctor/models status.",
                "Do not continue the lifecycle automatically until auth health is restored.",
            ),
        )

    if (
        "unknown model:" in lowered
        or "reason=model_not_found" in lowered
        or "unsupported model" in lowered
        or "not supported when using codex with a chatgpt account" in lowered
    ):
        return OpenClawExecutionDiagnosis(
            status="unsupported-model",
            reason=(
                "OpenClaw reported that the selected model is unsupported "
                "for this runtime path."
            ),
            remediation=(
                "Use the deterministic Signposter fallback/escalation model if one exists.",
                "Otherwise treat the run as blocked and continue only with "
                "explicit manual fallback.",
            ),
        )

    stale_signal = find_stale_or_failover_signal(combined_output)
    if stale_signal:
        return OpenClawExecutionDiagnosis(
            status="failover-or-stale-runtime",
            reason=f"OpenClaw output reported a stale/failover runtime signal: {stale_signal}.",
            remediation=(
                "Clear stale sessions or repair unhealthy fallback/provider state before retry.",
                "Keep raw output local and continue only with a bounded summary artifact.",
            ),
            signal=stale_signal,
        )

    if (
        "turn idle timed out waiting for progress" in lowered
        or "client retired after timed-out turn" in lowered
        or "timed-out turn" in lowered
    ):
        return OpenClawExecutionDiagnosis(
            status="runtime-stall",
            reason="OpenClaw runtime stalled without producing a usable bounded result.",
            remediation=(
                "Treat this as a blocked execution and do not keep the orchestrator waiting.",
                "Use the manual Signposter artifact fallback if the lifecycle must continue.",
            ),
        )

    if diagnostics_warnings:
        return OpenClawExecutionDiagnosis(
            status="config-drift",
            reason="OpenClaw runtime/config drift warnings were present during execution failure.",
            remediation=(
                "Align OpenClaw configured models, aliases, fallbacks, and auth ordering.",
                "Re-run signposter doctor and role smoke before retrying "
                "normal lifecycle execution.",
            ),
        )

    return OpenClawExecutionDiagnosis(
        status="runtime-error",
        reason=(
            f"OpenClaw exited with code {exit_code} without a recognized "
            "bounded success signal."
        ),
        remediation=(
            "Inspect the local raw artifact and classify the concrete runtime "
            "failure before retrying.",
        ),
    )
