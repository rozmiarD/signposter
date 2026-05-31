from __future__ import annotations

from unittest.mock import patch

from signposter.openclaw_preflight import (
    check_openclaw_preflight,
    format_openclaw_preflight_block,
)


def test_preflight_blocks_missing_openclaw_cli():
    with patch("shutil.which", return_value=None):
        result = check_openclaw_preflight(
            artifact_kind="worker",
            target=34,
            env={"OPENAI_API_KEY": "secret-value"},
        )

    assert result.ok is False
    assert result.reason == "OpenClaw CLI not found on PATH"
    assert result.openclaw_path is None
    assert "write-worker-summary --issue 34 --apply" in result.manual_fallback


def test_preflight_blocks_missing_provider_token():
    with patch("shutil.which", return_value="/usr/bin/openclaw"):
        result = check_openclaw_preflight(artifact_kind="review", target=33, env={})

    output = format_openclaw_preflight_block(result)

    assert result.ok is False
    assert result.reason == "no provider token environment variable is configured"
    assert "write-review-summary --pr 33 --apply" in output
    assert "secret-value" not in output


def test_preflight_passes_with_openclaw_and_provider_token():
    with patch("shutil.which", return_value="/usr/bin/openclaw"):
        result = check_openclaw_preflight(
            artifact_kind="worker",
            target=34,
            env={"ANTHROPIC_API_KEY": "secret-value"},
        )

    assert result.ok is True
    assert result.reason == "OpenClaw CLI and provider token environment are present"


def test_custom_provider_token_env_is_supported():
    with patch("shutil.which", return_value="/usr/bin/openclaw"):
        result = check_openclaw_preflight(
            artifact_kind="worker",
            target=34,
            env={
                "SIGNPOSTER_OPENCLAW_PROVIDER_TOKEN_ENV": "CUSTOM_PROVIDER_TOKEN",
                "CUSTOM_PROVIDER_TOKEN": "secret-value",
            },
        )

    assert result.ok is True
    assert "CUSTOM_PROVIDER_TOKEN" in result.checked_token_envs
