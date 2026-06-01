from __future__ import annotations

from unittest.mock import patch

from signposter.openclaw_diagnostics import (
    gather_openclaw_runtime_diagnostics,
    parse_openclaw_models_status,
)


def test_parse_openclaw_models_status_detects_policy_drift():
    diagnostics = parse_openclaw_models_status(
        "\n".join(
            [
                "Default       : openai/gpt-5.4",
                "Fallbacks (2) : xai/grok-build-0.1, xai/grok-3",
                "Aliases (2)   : gpt-mini -> openai/gpt-5.4-mini, "
                "gpt-nano -> openai/gpt-5.4-nano",
                "Configured models (6): openai/gpt-5.5, openai/gpt-5.4, "
                "openai/gpt-5.4-mini, openai/gpt-5.3-codex, openai/gpt-5.2, "
                "openai/gpt-5.4-nano",
                "- xai:foo@example.com expired expires in 0m",
            ]
        )
    )

    assert diagnostics.command_ok is True
    assert diagnostics.default_model == "openai/gpt-5.4"
    assert diagnostics.fallbacks == ("xai/grok-build-0.1", "xai/grok-3")
    assert any("fallback models drift" in warning for warning in diagnostics.warnings)
    assert any("aliases resolve" in warning for warning in diagnostics.warnings)
    assert any("expired provider auth entries" in warning for warning in diagnostics.warnings)


def test_gather_openclaw_runtime_diagnostics_handles_missing_binary():
    with patch("signposter.openclaw_diagnostics.subprocess.run", side_effect=FileNotFoundError):
        diagnostics = gather_openclaw_runtime_diagnostics()

    assert diagnostics.available is False
    assert diagnostics.error == "openclaw command not found"
