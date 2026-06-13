from __future__ import annotations

from signposter.delegation import (
    consecutive_delegation_failures,
    evaluate_delegation_policy,
    load_delegation_attempts,
    record_delegation_attempt,
)


def test_delegation_policy_opens_after_consecutive_failures(tmp_path) -> None:
    ledger = tmp_path / "delegation.json"
    for status in ("timeout", "runtime-stall", "unsupported-model"):
        record_delegation_attempt(
            target_kind="issue",
            target_number=42,
            role="WORKER_CODE",
            backend="codex-cli",
            model="openai/gpt-5.4",
            status=status,
            reason=f"{status} during bounded execution",
            raw_path=f"artifacts/runs/{status}.raw.txt",
            summary_path=f"artifacts/runs/{status}.summary.md",
            ledger_path=ledger,
        )

    attempts = load_delegation_attempts(ledger)
    decision = evaluate_delegation_policy(
        target_kind="issue",
        target_number=42,
        role="WORKER_CODE",
        backend="codex-cli",
        model="openai/gpt-5.4",
        ledger_path=ledger,
        failure_threshold=3,
    )

    assert len(attempts) == 3
    assert decision.status == "takeover-required"
    assert decision.failure_count == 3
    assert "pilot takeover is required" in decision.reason


def test_delegation_policy_resets_after_success(tmp_path) -> None:
    ledger = tmp_path / "delegation.json"
    for status in ("timeout", "runtime-stall", "success"):
        record_delegation_attempt(
            target_kind="issue",
            target_number=42,
            role="WORKER_CODE",
            backend="codex-cli",
            model="openai/gpt-5.4",
            status=status,
            reason=status,
            ledger_path=ledger,
        )

    failures = consecutive_delegation_failures(
        target_kind="issue",
        target_number=42,
        role="WORKER_CODE",
        backend="codex-cli",
        model="openai/gpt-5.4",
        ledger_path=ledger,
    )
    decision = evaluate_delegation_policy(
        target_kind="issue",
        target_number=42,
        role="WORKER_CODE",
        backend="codex-cli",
        model="openai/gpt-5.4",
        ledger_path=ledger,
        failure_threshold=3,
    )

    assert failures == 0
    assert decision.status == "delegation-allowed"
