from __future__ import annotations

from signposter.codex_stage_contract import (
    DESIRED_DEFAULT_BACKEND,
    LEGACY_BACKEND,
    MANDATORY_CODEX_STAGE_RULES,
    codex_stage_contract,
)


def test_codex_stage_contract_records_backend_policy() -> None:
    contract = codex_stage_contract()

    assert contract["stage"] == "H048"
    assert contract["desired_default_backend"] == "codex-cli"
    assert contract["legacy_backend"] == "openclaw"
    assert DESIRED_DEFAULT_BACKEND == "codex-cli"
    assert LEGACY_BACKEND == "openclaw"


def test_codex_stage_contract_contains_mandatory_planner_rules() -> None:
    rules = {rule.key: rule.text for rule in MANDATORY_CODEX_STAGE_RULES}

    assert "Codex CLI is the desired default backend" in rules["default-backend"]
    assert "legacy compatibility or explicit fallback only" in rules["legacy-openclaw"]
    assert "Dry-run planning must precede apply" in rules["dry-run-first"]
    assert "gate" in rules["gates-preserved"].lower()
    assert "Raw execution output remains local" in rules["bounded-artifacts"]


def test_codex_stage_contract_notes_are_local_only() -> None:
    contract = codex_stage_contract()
    notes = "\n".join(contract["notes"])

    assert "No GitHub mutation was performed." in notes
    assert "No OpenClaw execution was performed." in notes
    assert "No Codex CLI execution was performed." in notes
