from __future__ import annotations

from signposter.artifact import plan_review_summary, plan_worker_summary
from signposter.artifact_safety import find_stale_or_failover_signal
from signposter.comments import (
    audit_github_comment_body,
    redact_github_comment_body,
)
from signposter.gate import evaluate_ci_gate
from signposter.review import evaluate_review_gate


def test_find_stale_or_failover_signal_detects_provider_noise():
    signal = find_stale_or_failover_signal("Provider unavailable; failover attempted.")
    assert signal == "provider unavailable"


def test_worker_gate_blocks_provider_failover_artifact():
    summary = plan_worker_summary(
        repo="test/repo",
        issue=36,
        changed_files=["src/signposter/gate.py", "tests/test_artifact_safety.py"],
    ).content
    noisy = summary + "\nProvider unavailable during model failover.\n"

    decision = evaluate_ci_gate(0, noisy)

    assert decision.decision == "needs-work"
    assert "stale/failover signal" in decision.reason


def test_worker_gate_accepts_formal_manual_artifact():
    summary = plan_worker_summary(
        repo="test/repo",
        issue=36,
        changed_files=["src/signposter/gate.py", "tests/test_artifact_safety.py"],
        targeted_validation=[
            "ruff check src/signposter/gate.py tests/test_artifact_safety.py",
            "python -m pytest tests/test_artifact_safety.py -q",
        ],
    ).content

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"


def test_review_gate_blocks_provider_failover_artifact(tmp_path):
    summary = plan_review_summary(
        pr=35,
        risk="medium",
        findings=["Looks scoped."],
        runs_dir=tmp_path,
    ).content
    path = tmp_path / "pr-35-reviewer.summary.md"
    path.write_text(summary + "\nModel unavailable; fallback provider failed.\n", encoding="utf-8")

    gate = evaluate_review_gate(
        "test/repo",
        35,
        summary_path=str(path),
        allow_medium_risk=True,
    )

    assert gate.gate_pass is False
    assert "stale/failover signal" in gate.reason


def test_review_gate_accepts_formal_manual_artifact(tmp_path):
    summary = plan_review_summary(
        pr=35,
        risk="medium",
        findings=["Looks scoped."],
        runs_dir=tmp_path,
    ).content
    path = tmp_path / "pr-35-reviewer.summary.md"
    path.write_text(summary, encoding="utf-8")

    gate = evaluate_review_gate(
        "test/repo",
        35,
        summary_path=str(path),
        allow_medium_risk=True,
    )

    assert gate.gate_pass is True


def test_comment_redaction_covers_reviewer_token_assignment():
    body = "Signposter report\n\nSIGNPOSTER_REVIEWER_GH_TOKEN=ghp_" + ("A" * 24)

    redacted = redact_github_comment_body(body)
    audit = audit_github_comment_body(redacted)

    assert "SIGNPOSTER_REVIEWER_GH_TOKEN=" not in redacted
    assert "[REDACTED:reviewer-token-assignment]" in redacted
    assert audit.valid is True


def test_comment_audit_rejects_unredacted_private_key_block():
    body = (
        "Signposter report\n\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "abc123\n"
        "-----END PRIVATE KEY-----"
    )

    audit = audit_github_comment_body(body)

    assert audit.valid is False
    assert "comment body contains possible private key block" in audit.errors
