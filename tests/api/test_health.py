from fastapi.testclient import TestClient

from geoassist.api.app import create_app
from geoassist.api.readiness import ReadinessProbe
from geoassist.config import Settings


def _settings_with_models(tmp_path):
    data_dir = tmp_path / "data"
    embedding_dir = tmp_path / "embedding"
    rerank_dir = tmp_path / "rerank"
    for directory in (data_dir, embedding_dir, rerank_dir):
        directory.mkdir()
    (embedding_dir / "model.onnx").write_bytes(b"embedding")
    (embedding_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (rerank_dir / "model_int8.onnx").write_bytes(b"rerank")

    return Settings(
        _env_file=None,
        app_env="development",
        data_dir=data_dir,
        embedding_model_path=str(embedding_dir),
        rerank_model_path=str(rerank_dir),
        llm_base_url="https://llm.example/v1",
        llm_api_key="llm-key",
    )


def test_liveness_is_independent_of_dependencies():
    app = create_app(probe=ReadinessProbe({"corpus": lambda: False}))
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_each_check():
    probe = ReadinessProbe({"corpus": lambda: False, "models": lambda: True})
    response = TestClient(create_app(probe=probe)).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "checks": {"corpus": False, "models": True}}


def test_readiness_returns_200_when_all_checks_pass():
    probe = ReadinessProbe({"corpus": lambda: True, "models": lambda: True})
    response = TestClient(create_app(probe=probe)).get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_default_probe_is_ready_with_data_models_and_llm(tmp_path):
    app = create_app(settings=_settings_with_models(tmp_path))
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "checks": {"data": True, "llm": True, "models": True},
    }


def test_default_probe_fails_when_a_model_file_is_missing(tmp_path):
    settings = _settings_with_models(tmp_path)
    (tmp_path / "rerank" / "model_int8.onnx").unlink()

    app = create_app(settings=settings)
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "checks": {"data": True, "llm": True, "models": False},
    }
