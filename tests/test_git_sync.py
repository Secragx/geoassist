import subprocess

from geoassist.sync.git import update_repo


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def test_clones_and_updates_to_remote_main(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)], check=True)
    (work / "a.md").write_text("# A\n")
    git("add", ".", cwd=work)
    git("-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-m", "a", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)

    checkout = tmp_path / "checkout"
    sha = update_repo(checkout, str(remote))
    assert sha == git("rev-parse", "HEAD", cwd=checkout)
    assert (checkout / "a.md").exists()

    (work / "a.md").write_text("# A actualizado\n")
    git("add", ".", cwd=work)
    git("-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-m", "b", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)
    assert update_repo(checkout, str(remote)) != sha
    assert "actualizado" in (checkout / "a.md").read_text()
