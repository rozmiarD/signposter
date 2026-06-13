"""Delegation attempt ledger and pilot-takeover policy."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DELEGATION_LEDGER_PATH = Path("artifacts/automation/delegation-attempts.json")
DEFAULT_FAILURE_THRESHOLD = 3
DELEGATION_FAILURE_THRESHOLD_ENV = "SIGNPOSTER_DELEGATION_FAILURE_THRESHOLD"
FAILURE_STATUSES = frozenset(
    {
        "auth-provider-failure",
        "auth-runtime-failure",
        "config-drift",
        "config-error",
        "failover-or-stale-runtime",
        "malformed-output",
        "missing-binary",
        "missing-prompt",
        "runtime-error",
        "runtime-stall",
        "timeout",
        "unsupported-model",
    }
)


@dataclass(frozen=True)
class DelegationAttempt:
    """One bounded backend/model delegation attempt."""

    target_kind: str
    target_number: int
    role: str
    backend: str
    model: str
    status: str
    reason: str
    raw_path: str = ""
    summary_path: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class DelegationPolicyDecision:
    """Read-only decision for whether backend delegation may continue."""

    status: str
    failure_count: int
    failure_threshold: int
    reason: str
    recovery: tuple[str, ...]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def delegation_failure_threshold(env: dict[str, str] | None = None) -> int:
    source = env if env is not None else os.environ
    raw = source.get(DELEGATION_FAILURE_THRESHOLD_ENV, "").strip()
    if not raw:
        return DEFAULT_FAILURE_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_FAILURE_THRESHOLD
    return value if value > 0 else DEFAULT_FAILURE_THRESHOLD


def load_delegation_attempts(
    path: str | Path = DEFAULT_DELEGATION_LEDGER_PATH,
) -> tuple[DelegationAttempt, ...]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return ()
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("delegation ledger must contain a JSON object")
    attempts = data.get("attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("delegation ledger attempts must be a JSON list")
    return tuple(
        DelegationAttempt(
            target_kind=str(item.get("target_kind") or ""),
            target_number=int(item.get("target_number") or 0),
            role=str(item.get("role") or ""),
            backend=str(item.get("backend") or ""),
            model=str(item.get("model") or ""),
            status=str(item.get("status") or ""),
            reason=str(item.get("reason") or ""),
            raw_path=str(item.get("raw_path") or ""),
            summary_path=str(item.get("summary_path") or ""),
            created_at=str(item.get("created_at") or ""),
        )
        for item in attempts
        if isinstance(item, dict)
    )


def write_delegation_attempts(
    attempts: tuple[DelegationAttempt, ...],
    path: str | Path = DEFAULT_DELEGATION_LEDGER_PATH,
) -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "delegation-attempts.v0.1",
        "attempts": [
            {
                "target_kind": attempt.target_kind,
                "target_number": attempt.target_number,
                "role": attempt.role,
                "backend": attempt.backend,
                "model": attempt.model,
                "status": attempt.status,
                "reason": attempt.reason,
                "raw_path": attempt.raw_path,
                "summary_path": attempt.summary_path,
                "created_at": attempt.created_at,
            }
            for attempt in attempts
        ],
    }
    ledger_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def record_delegation_attempt(
    *,
    target_kind: str,
    target_number: int,
    role: str,
    backend: str,
    model: str,
    status: str,
    reason: str,
    raw_path: str = "",
    summary_path: str = "",
    ledger_path: str | Path = DEFAULT_DELEGATION_LEDGER_PATH,
) -> DelegationAttempt:
    """Append one local delegation attempt without mutating lifecycle state."""
    attempt = DelegationAttempt(
        target_kind=target_kind,
        target_number=target_number,
        role=role,
        backend=backend,
        model=model,
        status=status,
        reason=reason[:500],
        raw_path=raw_path,
        summary_path=summary_path,
        created_at=_now(),
    )
    attempts = load_delegation_attempts(ledger_path)
    write_delegation_attempts(attempts + (attempt,), ledger_path)
    return attempt


def consecutive_delegation_failures(
    *,
    target_kind: str,
    target_number: int,
    role: str,
    backend: str,
    model: str,
    ledger_path: str | Path = DEFAULT_DELEGATION_LEDGER_PATH,
) -> int:
    """Count recent consecutive failures for the exact target/role/backend/model."""
    count = 0
    for attempt in reversed(load_delegation_attempts(ledger_path)):
        if (
            attempt.target_kind != target_kind
            or attempt.target_number != target_number
            or attempt.role != role
            or attempt.backend != backend
            or attempt.model != model
        ):
            continue
        if attempt.status in FAILURE_STATUSES:
            count += 1
            continue
        break
    return count


def evaluate_delegation_policy(
    *,
    target_kind: str,
    target_number: int,
    role: str,
    backend: str,
    model: str,
    ledger_path: str | Path = DEFAULT_DELEGATION_LEDGER_PATH,
    failure_threshold: int | None = None,
) -> DelegationPolicyDecision:
    """Return whether another backend delegation is allowed for this target."""
    threshold = failure_threshold or delegation_failure_threshold()
    failures = consecutive_delegation_failures(
        target_kind=target_kind,
        target_number=target_number,
        role=role,
        backend=backend,
        model=model,
        ledger_path=ledger_path,
    )
    if failures >= threshold:
        return DelegationPolicyDecision(
            status="takeover-required",
            failure_count=failures,
            failure_threshold=threshold,
            reason=(
                f"{failures} consecutive delegation failures for "
                f"{target_kind} #{target_number} {role}/{backend}/{model}; "
                "pilot takeover is required before another backend delegation"
            ),
            recovery=(
                "inspect preserved raw and summary artifacts",
                "write a bounded manual Signposter artifact",
                "continue through report/gate/review/merge lifecycle surfaces",
            ),
        )
    return DelegationPolicyDecision(
        status="delegation-allowed",
        failure_count=failures,
        failure_threshold=threshold,
        reason=(
            f"{failures}/{threshold} consecutive delegation failures for "
            f"{target_kind} #{target_number} {role}/{backend}/{model}"
        ),
        recovery=(),
    )
