from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class SetupError(Exception):
    pass


def run(arguments, *, capture=False, check=True, env=None, input=None):
    result = subprocess.run(
        [str(argument) for argument in arguments],
        text=True,
        capture_output=capture,
        check=False,
        env=dict(os.environ if env is None else env, LC_ALL="C"),
        input=input,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise SetupError(f"Command failed ({result.returncode}): {shlex.join(map(str, arguments))}\n{detail}")
    return result


def version(value):
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else (0, 0, 0)


def confirm(message):
    try:
        return input(f"{message} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".editor-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def safe_user_path(path, home):
    path = Path(os.path.abspath(path))
    home = Path(os.path.abspath(home))
    if path == home or home not in path.parents:
        raise SetupError(f"Refusing to change a path outside your home: {path}")
    current = path
    while current != home:
        if current.is_symlink():
            raise SetupError(f"Refusing a symlink destination: {current}")
        current = current.parent
    return path


class Platform:
    def __init__(self, *, system=None, release=None, os_release=None, machine=None, home=None):
        self.home = Path(home or Path.home())
        self.system = system or platform.system()
        self.machine = machine or platform.machine()
        if self.system == "Darwin":
            self.kind = "macos"
            self.release = release or platform.mac_ver()[0]
            if version(self.release)[0] < 26:
                raise SetupError("Unsupported: macOS 26 or newer is required.")
        elif self.system == "Linux":
            values = os_release if os_release is not None else platform.freedesktop_os_release()
            self.kind = values.get("ID", "")
            self.release = values.get("VERSION_ID", "")
            if self.kind not in ("ubuntu", "fedora", "arch"):
                raise SetupError(f"Unsupported Linux distribution: {self.kind or 'unknown'}. Supported: Ubuntu, Fedora, Arch.")
            if self.kind == "ubuntu" and version(self.release) < (24, 4, 0):
                raise SetupError("Unsupported: Ubuntu 24.04 or newer is required.")
        else:
            raise SetupError(f"Unsupported OS: {self.system}.")
        self.config = Path(os.environ.get("XDG_CONFIG_HOME", self.home / ".config"))
        self.data = Path(os.environ.get("XDG_DATA_HOME", self.home / ".local/share"))
        self.state = Path(os.environ.get("XDG_STATE_HOME", self.home / ".local/state")) / "myeditorconfig"

    @property
    def family(self):
        return "macos" if self.kind == "macos" else "linux"


class Backups:
    def __init__(self, home, documents=None):
        self.home = Path(home)
        if documents is None:
            documents = self.home / "Documents"
            user_dirs = self.home / ".config/user-dirs.dirs"
            if user_dirs.is_file():
                match = re.search(r'^XDG_DOCUMENTS_DIR="([^"\n]+)"', user_dirs.read_text(), re.M)
                if match:
                    documents = Path(match[1].replace("${HOME}", str(self.home)).replace("$HOME", str(self.home)))
        documents = Path(documents)
        if not documents.is_absolute() or self.home not in documents.parents or not documents.is_dir() or not os.access(documents, os.W_OK):
            documents = self.home
        self.root = documents / "editor_backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.records = []

    def remember(self, path, entry):
        path = safe_user_path(path, self.home)
        if any(record["path"] == str(path) for record in self.records):
            return
        if any(record["kind"] in ("directory", "absent") and Path(record["path"]) in path.parents for record in self.records):
            return
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", entry):
            raise SetupError("Invalid backup entry name.")
        safe_user_path(self.root, self.home)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.parent.chmod(0o700)
        destination = self.root / entry / path.relative_to(self.home)
        record = {"path": str(path), "backup": None, "kind": "absent"}
        if path.exists():
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_dir():
                shutil.copytree(path, destination, symlinks=True)
                record["kind"] = "directory"
            else:
                shutil.copy2(path, destination)
                record["kind"] = "file"
            record["backup"] = str(destination.relative_to(self.root))
        self.records.append(record)
        atomic_write(self.root / "manifest.json", json.dumps({"home": str(self.home), "records": self.records}, indent=2).encode())

    def write(self, path, data, entry, mode=None):
        path = safe_user_path(path, self.home)
        data = data.encode() if isinstance(data, str) else data
        if path.is_file() and path.read_bytes() == data:
            return False
        self.remember(path, entry)
        existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        atomic_write(path, data, mode if mode is not None else existing_mode)
        return True

    def remove(self, path, entry):
        path = safe_user_path(path, self.home)
        self.remember(path, entry)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    @staticmethod
    def restore(manifest, home):
        manifest = Path(manifest).resolve()
        root = manifest.parent
        data = json.loads(manifest.read_text())
        if data["home"] != str(home):
            raise SetupError("This backup belongs to a different home directory.")
        prepared = []
        for record in data["records"]:
            target = safe_user_path(record["path"], home)
            source = root / record["backup"] if record["backup"] else None
            if source and (root not in source.resolve().parents or not source.exists()):
                raise SetupError("Invalid or missing backup source.")
            if record["kind"] not in ("absent", "file", "directory") or (record["kind"] == "absent") != (source is None):
                raise SetupError("Invalid backup record type.")
            prepared.append((record, target, source))
        for record, target, source in reversed(prepared):
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            if source:
                target.parent.mkdir(parents=True, exist_ok=True)
                if record["kind"] == "directory":
                    shutil.copytree(source, target, symlinks=True)
                else:
                    shutil.copy2(source, target)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
