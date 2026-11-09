from geoassist.logging import configure_logging, get_logger


def test_secret_fields_are_redacted(capsys):
    configure_logging("INFO")
    log = get_logger("test")
    log.info("request", authorization="Bearer super-secret", api_key="sk-secret", question="hola")
    output = capsys.readouterr().out
    assert "super-secret" not in output
    assert "sk-secret" not in output
    assert "hola" in output


def test_logger_writes_to_current_sys_stdout_not_stale_stream(capsys):
    import contextlib
    import io

    configure_logging("INFO")
    log = get_logger("test-stdout")
    log.info("primero")
    assert "primero" in capsys.readouterr().out

    destino = io.StringIO()
    with contextlib.redirect_stdout(destino):
        log.info("segundo")
    assert "segundo" in destino.getvalue()

    log.info("tercero")
    assert "tercero" in capsys.readouterr().out
