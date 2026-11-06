import pytest
from pydantic import ValidationError
from geoassist.config import Settings


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
