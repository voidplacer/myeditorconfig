from __future__ import annotations

import json
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .common import SetupError, sha256


def valid_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not name or any(ord(char) < 32 for char in name):
        raise SetupError(f"Unsafe relative path: {name!r}")
    return path


class Network:
    def download(self, url, destination, digest=None):
        if urllib.parse.urlsplit(url).scheme != "https":
            raise SetupError("Downloads require HTTPS.")
        request = urllib.request.Request(url, headers={"User-Agent": "myeditorconfig-installer", "Accept": "application/vnd.github+json" if "api.github.com/" in url else "*/*"})
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        try:
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                if urllib.parse.urlsplit(response.url).scheme != "https":
                    raise SetupError("Refusing an insecure download redirect.")
                shutil.copyfileobj(response, output, 1024 * 1024)
            if not temporary.stat().st_size:
                raise SetupError(f"Empty download: {url}")
            if digest and sha256(temporary) != digest.removeprefix("sha256:").lower():
                raise SetupError(f"Checksum mismatch: {url}")
            temporary.replace(destination)
        except (urllib.error.URLError, OSError) as error:
            raise SetupError(f"Download failed: {url}: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def json(self, url):
        with tempfile.TemporaryDirectory(prefix="editor-metadata-") as directory:
            return json.loads(self.download(url, Path(directory) / "metadata.json").read_text())


class Repository:
    def __init__(self, root=None, *, remote=None, ref=None, network=None):
        self.root = Path(root).resolve() if root else None
        self.remote = remote
        self.ref = ref
        self.network = network or Network()
        if remote:
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", remote) or not re.fullmatch(r"[a-f0-9]{40}", ref or ""):
                raise SetupError("Remote runs require a repository and an immutable commit SHA.")
            tree = self.network.json(f"https://api.github.com/repos/{remote}/git/trees/{ref}?recursive=1")
            if tree.get("truncated"):
                raise SetupError("Repository metadata was truncated; refusing an incomplete menu.")
            self.paths = [item["path"] for item in tree["tree"] if item["type"] == "blob"]
        else:
            self.paths = [str(path.relative_to(self.root)) for prefix in ("configs", "fonts", "plugins") for path in (self.root / prefix).rglob("*") if path.is_file() and not path.is_symlink()]

    def files(self, prefix):
        return sorted(path for path in self.paths if path.startswith(prefix.rstrip("/") + "/"))

    def get(self, name, destination):
        valid_relative(name)
        if self.root:
            source = self.root / name
            if self.root not in source.resolve().parents or not source.is_file() or source.is_symlink():
                raise SetupError(f"Missing or unsafe repository file: {name}")
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        else:
            self.network.download(f"https://raw.githubusercontent.com/{self.remote}/{self.ref}/{urllib.parse.quote(name)}", destination)
        return Path(destination)

    def read_json(self, name):
        with tempfile.TemporaryDirectory(prefix="editor-catalog-") as directory:
            return json.loads(self.get(name, Path(directory) / "metadata.json").read_text())

    def stage(self, prefix, destination):
        paths = self.files(prefix)
        if not paths:
            raise SetupError(f"No files found for {prefix}.")
        for name in paths:
            relative = valid_relative(name).relative_to(prefix)
            self.get(name, Path(destination) / str(relative))


def extract(archive, destination):
    try:
        _extract(archive, destination)
    except (tarfile.TarError, zipfile.BadZipFile, EOFError) as error:
        raise SetupError(f"Invalid or damaged archive: {archive}: {error}") from error


def _extract(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                valid_relative(member.filename.rstrip("/"))
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SetupError("Archive contains a symlink.")
            bundle.extractall(destination)
    else:
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            for member in members:
                valid_relative(member.name.rstrip("/"))
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise SetupError("Archive contains a device or special file.")
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    base = PurePosixPath(member.name).parent if member.issym() else PurePosixPath()
                    depth = 0
                    if link.is_absolute():
                        raise SetupError("Archive contains an absolute link.")
                    for part in (base / link).parts:
                        depth += -1 if part == ".." else 0 if part == "." else 1
                        if depth < 0:
                            raise SetupError("Archive link escapes its destination.")
            bundle.extractall(destination, filter="data")


def release_asset(network, repository, name):
    release = network.json(f"https://api.github.com/repos/{repository}/releases/latest")
    asset = next((item for item in release["assets"] if item["name"] == name or callable(name) and name(item["name"])), None)
    if asset is None:
        raise SetupError(f"No compatible binary release asset in {repository}.")
    if not isinstance(asset.get("digest"), str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", asset["digest"]):
        raise SetupError(f"No published SHA-256 digest for {asset['name']}; refusing an unverified asset.")
    return {**asset, "release_tag": release.get("tag_name", "")}
