"""Operaciones Git de actualización del repositorio de manuales."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 120


def update_repo(repo_dir: Path, repo_url: str, branch: str = "main") -> str:
    """Actualiza ``repo_dir`` al HEAD de ``branch`` y devuelve el SHA.

    Si ``repo_dir`` no existe o no contiene un repositorio Git, clona
    ``repo_url`` con ``--depth=1``. Si ya es un clon, hace ``fetch``,
    ``reset --hard`` y ``clean -fd``. ``GIT_TERMINAL_PROMPT`` se desactiva
    para evitar prompts interactivos; ``GIT_SSH_COMMAND`` se respeta si está
    definido en el entorno.
    """

    repo_dir = Path(repo_dir)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def _run_git(*args: str, cwd: Path) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )

    if not (repo_dir / ".git").is_dir():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            "clone",
            "--depth=1",
            "--branch",
            branch,
            repo_url,
            str(repo_dir),
            cwd=repo_dir.parent,
        )
    else:
        _run_git("fetch", "--depth=1", "origin", branch, cwd=repo_dir)
        _run_git("reset", "--hard", f"origin/{branch}", cwd=repo_dir)
        _run_git("clean", "-fd", cwd=repo_dir)

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return result.stdout.strip()
