import pytest

from geoassist.sync.lock import FileLock, LockBusy


def test_second_lock_fails_while_first_is_held(tmp_path):
    path = tmp_path / "sync.lock"
    with FileLock(path), pytest.raises(LockBusy), FileLock(path):
        pass
