from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import SetupError, confirm, run, safe_user_path, version
from .remote import extract, release_asset


class Packages:
    def __init__(self, system, network, backups, *, assume_yes=False):
        self.system = system
        self.network = network
        self.backups = backups
        self.assume_yes = assume_yes
        self.refreshed = False
        self.env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK="1")
        self.env.pop("HOMEBREW_NO_INSTALL_UPGRADE", None)

    def consent(self, message):
        print(message)
        if not self.assume_yes and not confirm("Continue?"):
            raise SetupError("Cancelled; configuration was not applied.")

    def sudo(self, arguments, **kwargs):
        if os.geteuid() == 0:
            raise SetupError("Run as your normal user, not root. The installer will request sudo when needed.")
        return run(["sudo", *arguments], **kwargs)

    def refresh(self):
        if self.refreshed:
            return
        kind = self.system.kind
        if kind == "ubuntu":
            self.sudo(["apt-get", "update", "--error-on=any"])
        elif kind == "fedora":
            self.sudo(["dnf", "makecache", "--refresh"])
        elif kind == "arch":
            self.consent("Arch requires a full system upgrade before installing packages; pacman will show its transaction.")
            self.sudo(["pacman", "-Syu"])
        else:
            run(["brew", "update"], env=self.env)
        self.refreshed = True

    def available(self, package):
        if not re.fullmatch(r"[A-Za-z0-9@+_.:/-]+", package) or package.startswith("-"):
            raise SetupError("Invalid package name.")
        kind = self.system.kind
        if kind == "ubuntu":
            output = run(["apt-cache", "policy", package], capture=True).stdout
            match = re.search(r"Candidate:\s*(\S+)", output)
            return match[1] if match and match[1] != "(none)" else None
        if kind == "fedora":
            result = run(["dnf", "repoquery", "--available", "--latest-limit=1", "--queryformat", "%{version}", package], capture=True, check=False)
            return result.stdout.strip().splitlines()[-1] if result.returncode == 0 and result.stdout.strip() else None
        if kind == "arch":
            result = run(["pacman", "-Si", package], capture=True, check=False)
            match = re.search(r"^Version\s*:\s*(\S+)", result.stdout, re.M)
            return match[1] if result.returncode == 0 and match else None
        result = run(["brew", "info", "--json=v2", package], capture=True, check=False, env=self.env)
        if result.returncode:
            return None
        info = json.loads(result.stdout)
        entries = info.get("formulae") or info.get("casks")
        return entries[0].get("versions", {}).get("stable", entries[0].get("version", "unknown")) if entries else None

    def install(self, packages, *, cask=False):
        packages = list(dict.fromkeys(packages))
        if not packages:
            return
        self.refresh()
        missing = [name for name in packages if not self.available(name)]
        if missing:
            raise SetupError(f"Unsupported on {self.system.kind}: packages unavailable: {', '.join(missing)}.")
        kind = self.system.kind
        if kind == "ubuntu":
            self.sudo(["apt-get", "install", "--no-install-recommends", "-y", *packages])
        elif kind == "fedora":
            self.sudo(["dnf", "install", "--refresh", "-y", *packages])
            self.sudo(["dnf", "upgrade", "-y", *packages])
        elif kind == "arch":
            self.sudo(["pacman", "-S", "--needed", *packages])
        else:
            for package in packages:
                dependencies = self.bottles(package, cask=cask)
                if not cask:
                    for formula in [*dependencies, package]:
                        run(["brew", "install", "--formula", "--force-bottle", "--ignore-dependencies", formula], env=self.env)
                else:
                    installed = run(["brew", "list", "--cask", package], capture=True, check=False, env=self.env).returncode == 0
                    run(["brew", "upgrade", "--cask", "--greedy", package] if installed else ["brew", "install", "--cask", package], env=self.env)

    def install_application(self, entry):
        package = entry.get("packages", {}).get(self.system.kind)
        if not package:
            raise SetupError(f"Unsupported application on {self.system.kind}: {entry['name']}.")
        self.refresh()
        self.check_existing(entry, package)
        if self.available(package):
            self.install([package], cask=entry.get("cask", False) and self.system.kind == "macos")
            return "package-manager"
        if self.system.kind == "arch" and entry.get("aur"):
            self.aur(entry["aur"])
            return "yay (binary package)"
        vendor = entry.get("vendor")
        if self.system.kind in ("ubuntu", "fedora") and vendor == "vscode":
            self.microsoft_repository()
            self.install([package])
            return "Microsoft package repository"
        if self.system.family == "linux" and self.system.kind != "arch" and vendor == "zed":
            return self.zed()
        if self.system.family == "linux" and self.system.kind != "arch" and vendor in ("clion", "goland"):
            return self.jetbrains(vendor)
        raise SetupError(f"Unsupported: {entry['name']} has no available package or approved binary source with an updater on this platform.")

    def bottles(self, package, *, cask=False):
        if cask:
            info = json.loads(run(["brew", "info", "--json=v2", "--cask", package], capture=True, env=self.env).stdout)
            dependencies = info["casks"][0].get("depends_on", {})
            if dependencies.get("formula") or dependencies.get("cask"):
                raise SetupError("This cask introduces additional package dependencies; binary-only installation needs manual review.")
            return []
        dependencies = run(["brew", "deps", "--formula", "--recursive", "--topological", package], capture=True, env=self.env).stdout.splitlines()
        info = json.loads(run(["brew", "info", "--json=v2", "--formula", package, *dependencies], capture=True, env=self.env).stdout)
        architecture = "arm64_" if self.system.machine == "arm64" else ""
        for formula in info["formulae"]:
            files = formula.get("bottle", {}).get("stable", {}).get("files", {})
            compatible = [tag for tag in files if not tag.endswith("linux") and (tag.startswith("arm64_") if architecture else not tag.startswith("arm64_"))]
            if not compatible:
                raise SetupError(f"Unsupported binary-only Homebrew dependency: {formula['name']} has no macOS bottle for this architecture.")
        run(["brew", "fetch", "--force-bottle", "--deps", package], env=self.env)
        return dependencies

    def check_existing(self, entry, package):
        executable = {"nvim": "nvim", "vscode": "code", "clangd": "clangd"}.get(entry["id"], entry["id"])
        existing = shutil.which(executable)
        if existing and str(existing).startswith("/snap/"):
            raise SetupError(f"{entry['name']} is installed through Snap. Automatic migration is unsupported; refresh it through Snap rather than creating a second installation.")
        flatpak_ids = {"zed": "dev.zed.Zed", "vscode": "com.visualstudio.code", "alacritty": "org.alacritty.Alacritty", "clion": "com.jetbrains.CLion", "goland": "com.jetbrains.GoLand", "nvim": "io.neovim.nvim"}
        flatpak_id = flatpak_ids.get(entry["id"])
        if flatpak_id and shutil.which("flatpak") and run(["flatpak", "info", flatpak_id], capture=True, check=False).returncode == 0:
            raise SetupError(f"Existing Flatpak {flatpak_id} needs a different config/update path. Automatic migration is unsupported; refusing a duplicate installation.")
        if not existing or entry["id"] in ("bash", "zsh", "clion", "goland"):
            return
        if entry["id"] == "zed" and self.system.home / ".local/zed.app" in Path(existing).resolve().parents:
            return
        kind = self.system.kind
        if kind == "macos":
            owned = run(["brew", "list", "--cask" if entry.get("cask") else "--formula", package], capture=True, check=False, env=self.env).returncode == 0
        else:
            arguments = ["dpkg-query", "-S", str(Path(existing).resolve())] if kind == "ubuntu" else ["rpm", "-qf", existing] if kind == "fedora" else ["pacman", "-Qo", existing]
            owned = run(arguments, capture=True, check=False).returncode == 0
        if not owned:
            raise SetupError(f"{entry['name']} is already installed outside the selected package manager ({existing}). Refusing a duplicate; migrate it to the package manager first.")

    def microsoft_repository(self):
        self.consent("VS Code is unavailable in configured repositories. Add Microsoft's signed package repository for normal apt/dnf updates.")
        with tempfile.TemporaryDirectory(prefix="editor-vscode-") as directory:
            key = self.network.download("https://packages.microsoft.com/keys/microsoft.asc", Path(directory) / "microsoft.asc")
            if self.system.kind == "ubuntu":
                key_target = Path("/etc/apt/keyrings/myeditorconfig-microsoft.asc")
                content = "Types: deb\nURIs: https://packages.microsoft.com/repos/code\nSuites: stable\nComponents: main\nSigned-By: /etc/apt/keyrings/myeditorconfig-microsoft.asc\n"
                target = Path("/etc/apt/sources.list.d/myeditorconfig-vscode.sources")
                self.sudo(["install", "-d", "-m", "0755", "/etc/apt/keyrings"])
                self.system_file(key_target, key.read_bytes(), directory)
            else:
                self.sudo(["rpm", "--import", key])
                content = "[myeditorconfig-code]\nname=Microsoft VS Code\nbaseurl=https://packages.microsoft.com/yumrepos/vscode\nenabled=1\ngpgcheck=1\ngpgkey=https://packages.microsoft.com/keys/microsoft.asc\n"
                target = Path("/etc/yum.repos.d/myeditorconfig-vscode.repo")
            self.system_file(target, content.encode(), directory)
        self.refreshed = False
        self.refresh()

    def system_file(self, target, data, directory):
        if target.exists():
            if target.is_symlink() or target.read_bytes() != data:
                raise SetupError(f"Refusing to overwrite existing system repository settings: {target}")
            return
        source = Path(directory) / "repository-data"
        source.write_bytes(data)
        self.sudo(["install", "-m", "0644", source, target])

    def aur_recipe(self, name):
        if not name.endswith("-bin") and name not in ("clion",):
            raise SetupError(f"Unapproved AUR binary package: {name}")
        with tempfile.TemporaryDirectory(prefix="editor-aur-review-") as directory:
            base = f"https://aur.archlinux.org/cgit/aur.git/plain/"
            recipe = self.network.download(f"{base}PKGBUILD?h={name}", Path(directory) / "PKGBUILD").read_text()
            info = self.network.download(f"{base}.SRCINFO?h={name}", Path(directory) / "SRCINFO").read_text()
        if re.search(r"\bbuild\s*\(\s*\)", recipe) or re.search(r"\b(?:cargo|rustc|gcc|g\+\+|cmake|ninja|go build|make\s+-C)\b", recipe):
            raise SetupError(f"AUR recipe may compile software: {name}. Refusing it.")
        for dependency in re.findall(r"^\s*(?:depends|makedepends|checkdepends)(?:_\w+)?\s*=\s*([^\s<>=]+)", info, re.M):
            satisfied = run(["pacman", "-T", dependency], capture=True, check=False).returncode == 0
            if not satisfied and not self.available(dependency):
                raise SetupError(f"AUR dependency {dependency} is not in pacman repositories; refusing a possible source build.")
        self.consent(f"AUR {name} repackages upstream binaries. Review yay's PKGBUILD prompts; abort if the recipe requests compilation. AUR recipes execute community-maintained code.")

    def aur(self, name):
        if self.system.kind != "arch":
            raise SetupError("AUR is supported only on Arch.")
        self.aur_recipe(name)
        self.install(["git", "base-devel"])
        if not shutil.which("yay"):
            self.aur_recipe("yay-bin")
            machine = self.system.machine
            asset = release_asset(self.network, "Jguer/yay", lambda name: name.endswith(f"_{machine}.tar.gz"))
            with tempfile.TemporaryDirectory(prefix="editor-yay-") as directory:
                archive = self.network.download(asset["browser_download_url"], Path(directory) / "yay.tar.gz", asset["digest"])
                extract(archive, Path(directory) / "unpacked")
                binary = next((path for path in (Path(directory) / "unpacked").rglob("yay") if path.is_file()), None)
                if binary is None:
                    raise SetupError("Official yay release has no helper binary.")
                binary.chmod(0o755)
                run([binary, "-S", "yay-bin"])
        if not shutil.which("yay"):
            raise SetupError("yay installation did not provide an executable.")
        run(["yay", "-S", name])

    def zed(self):
        if self.system.machine not in ("x86_64", "aarch64"):
            raise SetupError("Unsupported Zed architecture.")
        asset = release_asset(self.network, "zed-industries/zed", f"zed-linux-{self.system.machine}.tar.gz")
        binary = self.system.home / ".local/zed.app/bin/zed"
        if binary.exists() and version(run([binary, "--version"], capture=True).stdout) >= version(asset["release_tag"]):
            return "Zed built-in updater (existing installation is current)"
        self.consent("Install/update Zed using its official binary installer. Future updates use Zed's built-in updater.")
        for path in (self.system.home / ".local/zed.app", self.system.home / ".local/bin/zed", self.system.data / "applications/dev.zed.Zed.desktop"):
            if path.is_symlink() and path == self.system.home / ".local/bin/zed":
                if not str(path.resolve()).startswith(str(self.system.home / ".local/zed.app") + "/"):
                    raise SetupError("An unrelated zed launcher already exists.")
            elif path.exists():
                self.backups.remember(path, "zed")
        with tempfile.TemporaryDirectory(prefix="editor-zed-") as directory:
            archive = self.network.download(asset["browser_download_url"], Path(directory) / asset["name"], asset["digest"])
            script = self.network.download("https://zed.dev/install.sh", Path(directory) / "install.sh")
            run(["sh", script], env=dict(os.environ, ZED_CHANNEL="stable", ZED_BUNDLE_PATH=str(archive)))
        return "Zed built-in updater"

    def jetbrains(self, application):
        code = "CL" if application == "clion" else "GO"
        releases = self.network.json(f"https://data.services.jetbrains.com/products/releases?code={code}&latest=true&type=release")[code]
        release = releases[0]
        from .config import Configurator
        try:
            metadata, existing_info = Configurator(self.system, None, self, self.backups).find_ide(application)
        except SetupError:
            existing_info = None
        if existing_info is None and shutil.which(application):
            raise SetupError(f"An existing {application} launcher was found but its installation metadata could not be located; refusing a duplicate installation.")
        base = safe_user_path(self.system.data / "myeditorconfig/apps" / application, self.system.home)
        previous_installation = None
        if existing_info:
            installed_version = tuple(int(part) for part in re.findall(r"\d+", existing_info["version"]))
            latest_version = tuple(int(part) for part in re.findall(r"\d+", release["version"]))
            if installed_version >= latest_version:
                return "JetBrains updater (existing installation is current)"
            if base in metadata.parents:
                previous_installation = safe_user_path(metadata.parent, self.system.home)
            else:
                raise SetupError(f"Existing {application} at {metadata.parent} is older than {release['version']}. Update it using Toolbox/the IDE updater, then rerun; refusing to overwrite an externally managed installation.")
        key = "linuxARM64" if self.system.machine == "aarch64" else "linux" if self.system.machine == "x86_64" else None
        if key not in release["downloads"]:
            raise SetupError("Unsupported JetBrains architecture.")
        existing = list(base.glob("*/product-info.json")) if base.exists() else []
        if existing:
            if any(json.loads(path.read_text()).get("version") == release["version"] for path in existing):
                return "JetBrains built-in updater"
        self.consent(f"Install {application} {release['version']} into {base}. Future updates use the IDE's built-in updater. Download is approximately {release['downloads'][key]['size'] // (1024 * 1024)} MiB.")
        with tempfile.TemporaryDirectory(prefix="editor-jetbrains-") as directory:
            download = release["downloads"][key]
            checksum = self.network.download(download["checksumLink"], Path(directory) / "sha256").read_text().split()[0]
            if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
                raise SetupError("Invalid JetBrains release checksum.")
            archive = self.network.download(download["link"], Path(directory) / "ide.tar.gz", checksum)
            extract(archive, Path(directory) / "unpacked")
            product = next(iter((Path(directory) / "unpacked").glob("*/product-info.json")), None)
            if product is None:
                raise SetupError("JetBrains archive has no product metadata.")
            destination = base / product.parent.name
            if destination.exists():
                raise SetupError(f"Existing installation has conflicting metadata: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(product.parent, destination)
        launcher = self.system.home / ".local/bin" / application
        self.backups.write(launcher, f'#!/bin/sh\nexec "{destination}/bin/{application}.sh" "$@"\n', application, 0o755)
        desktop = "[Desktop Entry]\nType=Application\n" + f"Name={application}\nExec=\"{destination}/bin/{application}.sh\" %f\nIcon={destination}/bin/{application}.svg\nCategories=Development;IDE;\nTerminal=false\n"
        self.backups.write(self.system.data / f"applications/myeditorconfig-{application}.desktop", desktop, application)
        if previous_installation and previous_installation != destination:
            shutil.rmtree(previous_installation)
        return "JetBrains built-in updater"

    @staticmethod
    def jdk_major(text):
        match = re.search(r'(?:javac\s+|(?:java|openjdk)\s+(?:version\s+)?"?|^)(\d+)(?:\.(\d+))?', text, re.M)
        return int(match[2] or 0) if match and match[1] == "1" else int(match[1]) if match else 0

    def installed_jdks(self):
        candidates = []
        active = shutil.which("javac")
        if active:
            candidates.append(Path(active))
        if os.environ.get("JAVA_HOME"):
            candidates.append(Path(os.environ["JAVA_HOME"]) / "bin/javac")
        for root, pattern in ((Path("/usr/lib/jvm"), "*/bin/javac"), (Path("/usr/java"), "*/bin/javac"), (Path("/Library/Java/JavaVirtualMachines"), "*/Contents/Home/bin/javac"), (Path("/opt/homebrew/opt"), "openjdk*/libexec/openjdk.jdk/Contents/Home/bin/javac"), (Path("/usr/local/opt"), "openjdk*/libexec/openjdk.jdk/Contents/Home/bin/javac"), (self.system.home / ".sdkman/candidates/java", "*/bin/javac"), (self.system.home / ".asdf/installs/java", "*/bin/javac"), (self.system.home / ".local/share/mise/installs/java", "*/bin/javac")):
            candidates.extend(root.glob(pattern))
        found = {}
        for compiler in candidates:
            stable_home = compiler.parent.parent if re.search(r"/opt/openjdk(?:@\d+)?/libexec/openjdk\.jdk/Contents/Home/bin/javac$", str(compiler)) else None
            compiler = compiler.resolve()
            if compiler in found:
                if stable_home:
                    found[compiler] = (found[compiler][0], stable_home)
                continue
            runtime = compiler.with_name("java")
            if not compiler.is_file() or not runtime.is_file():
                continue
            try:
                majors = []
                for executable in (compiler, runtime):
                    output = subprocess.run([str(executable), "-version"], capture_output=True, text=True, timeout=5, check=False)
                    majors.append(self.jdk_major(output.stdout + "\n" + output.stderr) if output.returncode == 0 else 0)
                if majors[0] and majors[0] == majors[1]:
                    found[compiler] = (majors[0], stable_home or compiler.parent.parent)
            except (OSError, subprocess.SubprocessError):
                continue
        return sorted(found.values(), key=lambda item: item[0], reverse=True)

    def ensure_jdk(self):
        installed = self.installed_jdks()
        metadata = self.network.json("https://api.adoptium.net/v3/info/available_releases")
        latest = metadata.get("most_recent_lts") if isinstance(metadata, dict) else None
        releases = metadata.get("available_lts_releases") if isinstance(metadata, dict) else None
        if type(latest) is not int or not isinstance(releases, list) or latest not in releases:
            raise SetupError("Cannot determine current JDK LTS releases from official metadata; no JDK was installed.")
        supported = sorted({release for release in releases if type(release) is int and 21 <= release <= latest}, reverse=True)
        if installed and installed[0][0] >= latest:
            print(f"Using existing JDK {installed[0][0]}; no JDK installation needed.")
            return installed[0][1]
        self.refresh()
        target = None
        for release in supported:
            names = {"ubuntu": [f"openjdk-{release}-jdk"], "fedora": [f"java-{release}-openjdk-devel"], "arch": [f"jdk{release}-openjdk", "jdk-openjdk"], "macos": [f"openjdk@{release}", "openjdk"]}[self.system.kind]
            for package in names:
                candidate = self.available(package)
                if candidate and self.jdk_major(candidate.split(":")[-1]) == release and not re.search(r"(?:^|[~+_-])ea(?:$|[+._-]|\d)", candidate, re.I):
                    target = (release, package)
                    break
            if target:
                break
        if not target:
            if installed and installed[0][0] >= 21:
                print(f"Using existing compatible JDK {installed[0][0]}; no suitable LTS package is available and no JDK installation is needed.")
                return installed[0][1]
            raise SetupError("Unsupported Java extra: no suitable LTS JDK is available from this distro/Homebrew. No upstream binary fallback will be used.")
        release, package = target
        if installed and installed[0][0] >= release:
            print(f"Using existing JDK {installed[0][0]} (repository LTS: {release}); no JDK installation needed.")
            return installed[0][1]
        print(f"Installing repository LTS JDK {release}: {package}")
        self.install([package])
        installed = [item for item in self.installed_jdks() if item[0] >= release]
        if not installed:
            raise SetupError("JDK package installed, but matching java and javac could not be verified. Neovim config was not applied.")
        return installed[0][1]

    def lazyvim(self, extra_packages=()):
        packages = {
            "ubuntu": ["neovim", "git", "build-essential", "tree-sitter-cli", "curl", "lazygit", "fzf", "ripgrep", "fd-find", "clang-format", "unzip"],
            "fedora": ["neovim", "git", "gcc", "tree-sitter-cli", "curl", "lazygit", "fzf", "ripgrep", "fd-find", "clang-tools-extra", "unzip"],
            "arch": ["neovim", "git", "base-devel", "tree-sitter-cli", "curl", "lazygit", "fzf", "ripgrep", "fd", "clang", "unzip"],
            "macos": ["neovim", "git", "tree-sitter", "curl", "lazygit", "fzf", "ripgrep", "fd", "llvm", "unzip"],
        }[self.system.kind]
        packages = list(dict.fromkeys([*packages, *extra_packages]))
        self.refresh()
        self.check_existing({"id": "nvim", "name": "Neovim"}, "neovim")
        candidate = self.available("neovim")
        if not candidate or version(candidate.split(":")[-1]) < (0, 11, 2):
            raise SetupError(f"Unsupported LazyVim: repository Neovim {candidate or 'unavailable'} is below 0.11.2. No upstream fallback will be used.")
        self.install(packages)
        if version(run(["nvim", "--version"], capture=True).stdout) < (0, 11, 2):
            raise SetupError("The active nvim executable is too old; check your PATH.")
        if "LuaJIT" not in run(["nvim", "--version"], capture=True).stdout:
            raise SetupError("LazyVim requires Neovim built with LuaJIT.")
        if version(run(["git", "--version"], capture=True).stdout) < (2, 19, 0):
            raise SetupError("LazyVim requires Git 2.19 or newer.")
        if version(run(["fzf", "--version"], capture=True).stdout) < (0, 25, 1):
            raise SetupError("The available fzf is too old for this profile.")
        if self.system.kind == "macos":
            if not shutil.which("clang"):
                raise SetupError("A working C compiler is required; finish Apple's command-line tools installation first.")
            prefix = run(["brew", "--prefix"], capture=True).stdout.strip()
            os.environ["PATH"] = f"{prefix}/opt/llvm/bin:" + os.environ["PATH"]
        if not shutil.which("fd") and shutil.which("fdfind"):
            self.backups.write(self.system.home / ".local/bin/fd", '#!/bin/sh\nexec fdfind "$@"\n', "nvim", 0o755)
