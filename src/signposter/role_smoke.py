"""Local smoke planning and execution for role-aware OpenClaw roles."""

from __future__ import annotations

import datetime
import subprocess
from dataclasses import dataclass
from pathlib import Path

from signposter.openclaw_preflight import (
    check_openclaw_preflight,
    format_openclaw_preflight_block,
)
from signposter.role_policy import RolePolicy, get_role_policy


@dataclass(frozen=True)
class RoleSmokePlan:
    role_name: str
    policy: RolePolicy
    session_key: str
    message: str
    command_shape: str


def build_role_smoke_plan(role_name: str) -> RoleSmokePlan:
    """Build a deterministic local smoke plan for a role policy."""
    policy = get_role_policy(role_name)
    session_key = f"signposter-smoke-{role_name.lower()}"
    message = f"Reply with exactly {role_name}_SMOKE_OK and nothing else."
    command_shape = (
        f"openclaw agent --agent {policy.openclaw_agent} "
        f"--session-key {session_key} "
        f"--model {policy.model} "
        f"--thinking {policy.reasoning_effort} "
        f'--message "{message}" --local --json --timeout 45'
    )
    return RoleSmokePlan(
        role_name=role_name,
        policy=policy,
        session_key=session_key,
        message=message,
        command_shape=command_shape,
    )


def format_role_smoke_plan(plan: RoleSmokePlan) -> str:
    lines = [f"Signposter Role Smoke — {plan.role_name}", ""]
    lines.append(f"Agent: {plan.policy.openclaw_agent}")
    lines.append(f"Model: {plan.policy.model}")
    lines.append(f"Reasoning: {plan.policy.reasoning_effort}")
    lines.append(f"Session key: {plan.session_key}")
    lines.append(f"Command shape: {plan.command_shape}")
    lines.append("")
    lines.append("Notes:")
    lines.append("  No GitHub mutation was performed.")
    lines.append("  No OpenClaw execution was performed.")
    return "\n".join(lines)


def execute_role_smoke(role_name: str, *, runs_dir: Path | str = "artifacts/runs") -> dict:
    """Run a local OpenClaw smoke turn for one role policy."""
    plan = build_role_smoke_plan(role_name)
    preflight = check_openclaw_preflight(artifact_kind="worker", target=0)
    if not preflight.ok:
        print(format_openclaw_preflight_block(preflight))
        return {"success": False, "error": preflight.reason, "raw_path": None}

    runs_path = Path(runs_dir)
    runs_path.mkdir(parents=True, exist_ok=True)
    raw_path = runs_path / f"role-smoke-{role_name.lower()}.raw.txt"

    exec_cmd = [
        "openclaw",
        "agent",
        "--agent",
        plan.policy.openclaw_agent,
        "--session-key",
        plan.session_key,
        "--model",
        plan.policy.model,
        "--thinking",
        plan.policy.reasoning_effort,
        "--message",
        plan.message,
        "--local",
        "--json",
        "--timeout",
        "45",
    ]

    try:
        proc = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raw_path.write_text(f"[TIMEOUT]\n{exc}", encoding="utf-8")
        return {"success": False, "error": "timeout", "raw_path": str(raw_path)}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout
    if stderr:
        combined += "\n\n=== STDERR ===\n" + stderr

    raw_path.write_text(combined, encoding="utf-8")

    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "raw_path": str(raw_path),
        "started_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "command_shape": plan.command_shape,
    }
