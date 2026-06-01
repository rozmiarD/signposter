from __future__ import annotations

from signposter.openclaw_runtime import (
    classify_openclaw_execution,
    openclaw_timeout_settings,
)


def test_classify_openclaw_execution_does_not_treat_plain_401_as_auth_failure():
    diagnosis = classify_openclaw_execution(
        exit_code=1,
        combined_output="worker mentioned issue #401 in normal text",
        timed_out=False,
    )

    assert diagnosis.status == "runtime-error"


def test_classify_openclaw_execution_recognizes_specific_auth_signal():
    diagnosis = classify_openclaw_execution(
        exit_code=1,
        combined_output='error provider=openai-codex status=401 code=token_invalidated',
        timed_out=False,
    )

    assert diagnosis.status == "auth-provider-failure"


def test_openclaw_timeout_settings_surface_invalid_values():
    settings = openclaw_timeout_settings(
        {
            "SIGNPOSTER_OPENCLAW_EXECUTE_TIMEOUT_SECONDS": "abc",
            "SIGNPOSTER_OPENCLAW_SUBPROCESS_TIMEOUT_SECONDS": "0",
        }
    )

    assert settings.execute_timeout == 120
    assert settings.subprocess_timeout == 135
    assert len(settings.warnings) == 2
    assert settings.config_error is None


def test_openclaw_timeout_settings_reject_invalid_timeout_relationship():
    settings = openclaw_timeout_settings(
        {
            "SIGNPOSTER_OPENCLAW_EXECUTE_TIMEOUT_SECONDS": "40",
            "SIGNPOSTER_OPENCLAW_SUBPROCESS_TIMEOUT_SECONDS": "30",
        }
    )

    assert settings.execute_timeout == 40
    assert settings.subprocess_timeout == 30
    assert settings.config_error is not None
    assert "must exceed" in settings.config_error
