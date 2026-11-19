import json
import os
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

STATE_BRANCH = "state"
STATE_FILE = "state.enc"


def empty_state() -> dict:
    return {
        "version": 1,
        "relief_total": None,
        "last_full": {"relief": None, "makeup": None},
        "baseline_done": {"relief": False, "makeup": False},
        "lessons": {},
        "classes": {},
        "alerts": {},
    }


def encrypt(state: dict, key: str) -> bytes:
    return Fernet(key.encode()).encrypt(json.dumps(state, sort_keys=True).encode())


def decrypt(blob: bytes, key: str) -> dict:
    return json.loads(Fernet(key.encode()).decrypt(blob))


class FileStore:
    """Plain local file, for offline testing. Not encrypted."""

    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return empty_state()

    def save(self, state: dict) -> None:
        self.path.write_text(json.dumps(state, indent=1, sort_keys=True))


class GitBranchStore:
    """Encrypted state kept as the only file on a separate branch, force-pushed as one commit."""

    def __init__(self, key: str, repo_dir: str = "."):
        self.key = key
        self.repo_dir = repo_dir

    def _git(self, *args: str, input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "ms-notif2",
            "GIT_AUTHOR_EMAIL": "ms-notif2@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "ms-notif2",
            "GIT_COMMITTER_EMAIL": "ms-notif2@users.noreply.github.com",
        }
        return subprocess.run(["git", *args], cwd=self.repo_dir, input=input,
                              capture_output=True, check=check, env=env)

    def load(self) -> dict:
        fetched = self._git("fetch", "--quiet", "origin", STATE_BRANCH, check=False)
        if fetched.returncode != 0:
            return empty_state()
        blob = self._git("show", f"FETCH_HEAD:{STATE_FILE}", check=False)
        if blob.returncode != 0:
            return empty_state()
        try:
            return decrypt(blob.stdout.strip(), self.key)
        except InvalidToken:
            print("state: could not decrypt (STATE_KEY changed?), starting fresh")
            return empty_state()

    def save(self, state: dict) -> None:
        data = encrypt(state, self.key)
        blob = self._git("hash-object", "-w", "--stdin", input=data).stdout.decode().strip()
        tree = self._git("mktree", input=f"100644 blob {blob}\t{STATE_FILE}\n".encode()).stdout.decode().strip()
        commit = self._git("commit-tree", tree, "-m", "state").stdout.decode().strip()
        self._git("push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{STATE_BRANCH}")
