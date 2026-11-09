from pathlib import Path

import pytest
from pydantic import ValidationError
from geoassist.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def make_settings(**overrides):
    values = dict(
        app_env="development",
        teams_security_token="teams-token",
        llm_api_key="llm-key",
        llm_data_policy="external_approved",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_requires_teams_token():
    with pytest.raises(ValidationError):
        make_settings(app_env="production", teams_security_token="")


def test_production_requires_defined_llm_policy():
    with pytest.raises(ValidationError):
        make_settings(app_env="production", llm_data_policy=None)


def test_on_prem_only_rejects_public_host():
    with pytest.raises(ValidationError):
        make_settings(llm_data_policy="on_prem_only", llm_base_url="https://api.deepseek.com")


def test_on_prem_only_accepts_allowed_host():
    settings = make_settings(
        llm_data_policy="on_prem_only",
        llm_base_url="http://llm.internal:8080",
        llm_allowed_hosts=("llm.internal",),
    )
    assert settings.llm_data_policy == "on_prem_only"


def test_defaults_match_spec():
    settings = make_settings()
    assert settings.session_ttl_minutes == 120
    assert settings.rate_limit_per_minute == 6
    assert settings.manuals_root_rel == "data_source/manuales"
    assert settings.assets_root_rel == "data_source/assets"


def test_env_example_loads_with_compose_only_variables(monkeypatch):
    for name in (
        "APP_ENV",
        "TEAMS_BOT_NAME",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT_MS",
        "DATA_DIR",
        "MANUALS_DIR",
        "SESSION_TTL_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=ROOT / ".env.example")

    assert settings.app_env == "development"
    assert settings.teams_bot_name == "GeoAssist"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_timeout_ms == 2500
    assert settings.data_dir == Path("/data")
    assert settings.manuals_dir == Path("/manuals")
    assert settings.session_ttl_minutes == 120


def test_query_rewrite_and_budget_defaults():
    settings = make_settings()
    assert settings.query_rewrite_enabled is False
    assert settings.query_rewrite_cache == 128
    assert settings.query_rewrite_timeout_ms == 1200
    assert settings.total_budget_ms == 8000


def test_budget_and_timeouts_must_be_positive():
    with pytest.raises(ValidationError):
        make_settings(total_budget_ms=0)
    with pytest.raises(ValidationError):
        make_settings(query_rewrite_timeout_ms=0)
