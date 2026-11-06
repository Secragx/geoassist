from geoassist.logging import configure_logging, get_logger


def test_secret_fields_are_redacted(capsys):
    configure_logging("INFO")
    log = get_logger("test")
    log.info("request", authorization="Bearer super-secret", api_key="sk-secret", question="hola")
    output = capsys.readouterr().out
    assert "super-secret" not in output
    assert "sk-secret" not in output
    assert "hola" in output
