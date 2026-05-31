from __future__ import annotations

import json
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
    assert result.auth_config_path is None
    assert result.auth_profile_count == 0
    assert "write-worker-summary --issue 34 --apply" in result.manual_fallback


def test_preflight_blocks_missing_provider_token(tmp_path):
    missing_config = tmp_path / "missing-openclaw.json"

    with patch("shutil.which", return_value="/usr/bin/openclaw"):
        result = check_openclaw_preflight(
            artifact_kind="review",
            target=33,
            env={"OPENCLAW_CONFIG_PATH": str(missing_config)},
        )

    output = format_openclaw_preflight_block(result)

    assert result.ok is False
    assert (
        result.reason
        == "no provider token environment variable is configured and "
        "no usable OpenClaw auth profile was found"
    )
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


def test_preflight_passes_with_openclaw_auth_profile_config(tmp_path):
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "auth": {
                    "profiles": {
                        "worker": {
                            "provider": "openai-codex",
                            "mode": "oauth",
                            "token": "never-print-this",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with patch("shutil.which", return_value="/usr/bin/openclaw"):
        result = check_openclaw_preflight(
            artifact_kind="worker",
            target=34,
            env={"OPENCLAW_CONFIG_PATH": str(config_path)},
        )

    assert result.ok is True
    assert result.reason == "OpenClaw CLI and auth profile config are present"
    assert result.auth_config_path == str(config_path)
    assert result.auth_profile_count == 1


def test_preflight_blocks_when_auth_profile_config_is_unreadable(tmp_path):
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{not-json", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/openclaw"):
        result = check_openclaw_preflight(
            artifact_kind="worker",
            target=34,
            env={"OPENCLAW_CONFIG_PATH": str(config_path)},
        )

    output = format_openclaw_preflight_block(result)

    assert result.ok is False
    assert (
        result.reason
        == "no provider token environment variable is configured and "
        "OpenClaw auth profile config could not be read"
    )
    assert "never-print-this" not in output


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
