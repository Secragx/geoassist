"""Lock exclusivo basado en ``fcntl.flock`` para la sincronización."""

from __future__ import annotations

import errno
import fcntl
from pathlib import Path
from typing import TextIO


class LockBusy(RuntimeError):
    """El lock ya está siendo usado por otro proceso."""


class FileLock:
    """Context manager que adquiere un lock exclusivo no bloqueante.

    Levanta :class:`LockBusy` si otro proceso ya tiene el lock.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._file: TextIO | None = None

    def __enter__(self) -> FileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a")  # noqa: SIM115 - se cierra en __exit__
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise LockBusy(self._path) from exc
            raise
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
        return None
