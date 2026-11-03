from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import SetupError, run, safe_user_path
from .remote import extract, release_asset


class Configurator:
    def __init__(self, system, repository, packages, backups):
        self.system = system
        self.repository = repository
        self.packages = packages
        self.backups = backups

    def destination(self, value):
        return safe_user_path(Path(value.format(home=self.system.home, config=self.system.config, data=self.system.data)), self.system.home)

    @staticmethod
    def validate(staged):
        for path in Path(staged).rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".xml":
                ET.parse(path)
            elif path.suffix == ".toml":
                tomllib.loads(path.read_text())
            elif path.suffix == ".json":
                text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,\s*(?=[}\]])', lambda match: match[0] if match[0].startswith('"') else " ", path.read_text(), flags=re.S)
                json.loads(text)

    def apply(self, entry, staged):
        handler = entry.get("handler")
        if handler == "nvim":
            self.nvim(staged, entry.get("extras", ()), entry.get("java_home"))
        elif handler == "jetbrains":
            self.jetbrains(entry, staged)
        elif handler == "shell":
            self.shell(entry, staged)
        else:
            destination = entry.get("macos_destination") if self.system.kind == "macos" else None
            destination = destination or entry.get("destination")
            for source in sorted(Path(staged).rglob("*")):
                if source.is_file():
                    relative = source.relative_to(staged)
                    target = self.destination(entry["mappings"][str(relative)]) if "mappings" in entry else self.destination(str(Path(destination) / relative))
                    self.backups.write(target, source.read_bytes(), entry["id"])
            if entry["id"] == "vscode":
                for extension in ("vscodevim.vim", "Catppuccin.catppuccin-vsc", "Catppuccin.catppuccin-vsc-icons", "formulahendry.code-runner"):
                    run(["code", "--install-extension", extension])

    def shell(self, entry, staged):
        shell = entry["id"]
        if shell not in ("bash", "zsh"):
            raise SetupError(f"Unsupported shell profile: {shell}")
        target = safe_user_path(self.system.home / f".{shell}rc", self.system.home)
        original = target.read_bytes().decode("utf-8") if target.exists() else ""
        begin = "# BEGIN myeditorconfig shell profile"
        end = "# END myeditorconfig shell profile"
        remaining = original
        if begin in original or end in original:
            pattern = re.compile(r"^" + re.escape(begin) + r"\n.*?^" + re.escape(end) + r"(?:\n|$)", re.M | re.S)
            if original.count(begin) != 1 or original.count(end) != 1 or not pattern.search(original):
                raise SetupError(f"Malformed shell profile markers in {target}; repair them before applying.")
            remaining = pattern.sub("", original, count=1)
        profile = (Path(staged) / "profile.sh").read_text().rstrip()
        block = f'{begin}\n{profile}\n\neval "$(zoxide init --cmd cd {shell})"\n{end}\n'
        separator = "\n" if remaining and not remaining.endswith("\n") else ""
        self.packages.install(["zoxide", "eza"])
        self.backups.write(target, remaining + separator + block, shell)
        print(f"Shared shell profile appended to {target}. Existing settings preserved; login shell unchanged. Personal aliases include Linux/Arch-specific commands.")

    @staticmethod
    def lazyvim_state(state, extras):
        if not isinstance(state, dict) or not isinstance(state.get("extras", []), list):
            raise SetupError("Invalid lazyvim.json: expected an object with an extras list.")
        state = dict(state)
        legacy = state.get("version") is None
        if not legacy and (type(state["version"]) is not int or state["version"] < 0):
            raise SetupError("Invalid lazyvim.json: version must be a nonnegative integer.")
        prefix = "lazyvim.plugins.extras."

        def normalize(name, qualify):
            if not isinstance(name, str) or not name.strip():
                raise SetupError("Invalid LazyVim extra: expected a nonempty module name.")
            name = name.strip()
            qualified = name.startswith(prefix)
            while name.startswith(prefix):
                name = name.removeprefix(prefix)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*", name):
                raise SetupError(f"Invalid LazyVim extra module name: {name!r}")
            return prefix + name if qualified or qualify else name

        state["extras"] = sorted({normalize(name, legacy) for name in state.get("extras", [])} | {normalize(name, True) for name in extras})
        if legacy:
            state["version"] = 1
        return state

    def nvim(self, staged, extras=(), java_home=None):
        destination = safe_user_path(self.system.config / "nvim", self.system.home)
        lazy_file = destination / "lua/config/lazy.lua"
        is_lazyvim = lazy_file.is_file() and re.search(r'["\x27]LazyVim/LazyVim["\x27]', lazy_file.read_text())
        if not is_lazyvim:
            cache = Path(os.environ.get("XDG_CACHE_HOME", self.system.home / ".cache")) / "nvim"
            targets = [safe_user_path(target, self.system.home) for target in (destination, self.system.data / "nvim", cache, self.system.state.parent / "nvim")]
            with tempfile.TemporaryDirectory(prefix="editor-lazyvim-") as directory:
                starter = Path(directory) / "starter"
                run(["git", "clone", "--depth=1", "https://github.com/LazyVim/starter.git", starter])
                shutil.rmtree(starter / ".git")
                for target in targets:
                    if target.exists():
                        self.backups.remember(target, "nvim")
                for target in targets:
                    if target.exists():
                        self.backups.remove(target, "nvim")
                self.backups.remember(destination, "nvim")
                shutil.copytree(starter, destination)
        lazyvim_path = destination / "lazyvim.json"
        state = json.loads(lazyvim_path.read_text()) if lazyvim_path.exists() else {"extras": []}
        state = self.lazyvim_state(state, extras)
        self.backups.write(lazyvim_path, json.dumps(state, indent=2) + "\n", "nvim")
        for source in Path(staged).rglob("*"):
            if source.is_file():
                target = destination / source.relative_to(staged)
                self.backups.write(target, source.read_bytes(), "nvim")
        if self.system.kind == "macos":
            prefix = run(["brew", "--prefix"], capture=True).stdout.strip()
            options = destination / "lua/config/options.lua"
            original = options.read_text() if options.exists() else ""
            begin, end = '-- BEGIN myeditorconfig Homebrew tools', '-- END myeditorconfig Homebrew tools'
            original = re.sub(rf"(?ms)^{re.escape(begin)}\n.*?^{re.escape(end)}\n?", "", original).rstrip()
            path = f"{prefix}/opt/llvm/bin:"
            block = f'{begin}\nvim.env.PATH = {json.dumps(path, ensure_ascii=False)} .. vim.env.PATH\n{end}\n'
            self.backups.write(options, original + "\n\n" + block, "nvim")
        if java_home:
            options = destination / "lua/config/options.lua"
            original = options.read_text() if options.exists() else ""
            begin, end = '-- BEGIN myeditorconfig Java runtime', '-- END myeditorconfig Java runtime'
            original = re.sub(rf"(?ms)^{re.escape(begin)}\n.*?^{re.escape(end)}\n?", "", original).rstrip()
            block = f'{begin}\nvim.env.JAVA_HOME = {json.dumps(java_home, ensure_ascii=False)}\nvim.env.PATH = {json.dumps(str(Path(java_home) / "bin") + ":", ensure_ascii=False)} .. vim.env.PATH\n{end}\n'
            self.backups.write(options, original + "\n\n" + block, "nvim")
        print("LazyVim overlays installed. Open nvim to finish its normal plugin, language-tool, and parser setup; compilation is allowed only for these LazyVim requirements.")

    def find_ide(self, application):
        title = "CLion" if application == "clion" else "GoLand"
        if self.system.kind == "macos":
            candidates = [Path(f"/Applications/{title}.app/Contents/Resources/product-info.json"), self.system.home / f"Applications/{title}.app/Contents/Resources/product-info.json"]
        else:
            patterns = [
                self.system.data / f"myeditorconfig/apps/{application}/*/product-info.json",
                self.system.data / f"JetBrains/Toolbox/apps/{application}/product-info.json",
                self.system.data / f"JetBrains/Toolbox/apps/{application}/*/*/product-info.json",
                Path(f"/opt/{application}/product-info.json"),
                Path(f"/opt/{application}*/product-info.json"),
                Path(f"/usr/share/{application}/product-info.json"),
            ]
            import glob
            candidates = [Path(name) for pattern in patterns for name in glob.glob(str(pattern))]
            executable = shutil.which(application)
            if executable:
                candidates.extend(parent / "product-info.json" for parent in Path(executable).resolve().parents[:4])
        found = [(path, json.loads(path.read_text())) for path in candidates if path.is_file()]
        found = [pair for pair in found if pair[1].get("productCode") == ("CL" if application == "clion" else "GO")]
        unique = {str(path.resolve()): (path, info) for path, info in found}
        if not unique:
            raise SetupError(f"Cannot locate {title} product-info.json. Custom installation paths are currently unsupported.")
        return max(unique.values(), key=lambda pair: tuple(int(part) for part in re.findall(r"\d+", pair[1].get("buildNumber", "0"))))

    def assert_ide_closed(self, application):
        vimrc = safe_user_path(self.system.home / ".ideavimrc", self.system.home)
        if vimrc.exists():
            text = vimrc.read_text()
            if text.count('" BEGIN voidplacer JetBrains profile') != text.count('" END voidplacer JetBrains profile') or text.count('" BEGIN voidplacer JetBrains profile') > 1:
                raise SetupError("Malformed IdeaVim profile markers; repair them before applying the JetBrains profile.")
        try:
            metadata, info = self.find_ide(application)
        except SetupError:
            return
        product = metadata.parent.parent if self.system.kind == "macos" else metadata.parent
        processes = run(["ps", "-axo", "command="], capture=True).stdout
        if any(str(product) in line and ("java" in line or "MacOS/" + application in line) for line in processes.splitlines()):
            raise SetupError(f"Close {info['name']} before installing/updating or configuring it.")

    def jetbrains(self, entry, staged):
        application = entry["id"]
        metadata, info = self.find_ide(application)
        build = info.get("buildNumber", "")
        if not build.startswith("262."):
            raise SetupError(f"Unsupported JetBrains profile: {info.get('version')} (build {build}). This profile/plugin has only been validated for 2026.2.")
        product = metadata.parent.parent if self.system.kind == "macos" else metadata.parent
        self.assert_ide_closed(application)
        config_name = info.get("dataDirectoryName", "CLion2026.2" if application == "clion" else "GoLand2026.2")
        if not re.fullmatch(r"(?:CLion|GoLand)2026\.2", config_name):
            raise SetupError("Unexpected JetBrains config directory name.")
        if self.system.kind == "macos":
            config = self.system.home / "Library/Application Support/JetBrains" / config_name
            plugins = config / "plugins"
            launcher = product / "MacOS" / application
        else:
            config = self.system.config / "JetBrains" / config_name
            plugins = self.system.data / "JetBrains" / config_name
            launcher = product / "bin" / f"{application}.sh"
        safe_user_path(config, self.system.home)
        safe_user_path(plugins, self.system.home)
        if any(name in os.environ for name in (application.upper() + "_PROPERTIES", application.upper() + "_VM_OPTIONS")):
            raise SetupError("Custom JetBrains properties/VM-options paths need manual review; refusing standard-path writes.")
        self.backups.remember(config, application)
        if plugins != config / "plugins":
            self.backups.remember(plugins, application)
        run([launcher, "installPlugins", "IdeaVIM", "com.github.catppuccin.jetbrains", "com.github.catppuccin.jetbrains_icons"])
        release = self.repository.read_json("plugins/jetbrains/coding-profile/release.json")
        artifact = base64.b64decode(release["artifact"], validate=True)
        if hashlib.sha256(artifact).hexdigest() != release["sha256"]:
            raise SetupError("Custom JetBrains plugin checksum mismatch.")
        self.backups.write(plugins / "Voidplacer Coding Profile/lib/voidplacer-coding-profile.jar", artifact, application)
        for source in sorted(Path(staged).rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(staged)
            if self.system.kind == "macos" and (relative.parts[0] == "options" and "linux" in relative.parts):
                continue
            if source.suffix == ".vmoptions":
                target = config / (f"{application}.vmoptions" if self.system.kind == "macos" else source.name)
                original = target.read_text() if target.exists() else ""
                updated, count = re.subn(r"(?m)^-Xmx[^\r\n]*$", "-Xmx3840m", original)
                self.backups.write(target, updated if count else "-Xmx3840m\n" + original, application)
                continue
            target = config / relative
            if source.suffix == ".xml" and target.exists() and relative.parts[0] == "options":
                source_root = ET.fromstring(source.read_bytes())
                target_root = ET.fromstring(target.read_bytes())
                for component in source_root:
                    name = component.get("name")
                    existing = next((node for node in target_root if node.get("name") == name), None)
                    if existing is None:
                        target_root.append(component)
                    else:
                        self.merge_xml(existing, component)
                self.backups.write(target, ET.tostring(target_root, encoding="utf-8") + b"\n", application)
            else:
                self.backups.write(target, source.read_bytes(), application)
        with tempfile.TemporaryDirectory(prefix="editor-ideavim-") as directory:
            block = self.repository.get("configs/jetbrains/.ideavimrc", Path(directory) / "ideavimrc").read_text()
        target = self.system.home / ".ideavimrc"
        original = target.read_text() if target.exists() else ""
        begin, end = '" BEGIN voidplacer JetBrains profile', '" END voidplacer JetBrains profile'
        if original.count(begin) != original.count(end) or original.count(begin) > 1:
            raise SetupError("Malformed IdeaVim profile markers; refusing replacement.")
        original = re.sub(rf"(?ms)^{re.escape(begin)}\n.*?^{re.escape(end)}\n?", "", original).rstrip()
        self.backups.write(target, (original + "\n\n" if original else "") + block, application)
        print("JetBrains profile applied. Shared home formatting and project settings were not changed. Restart the IDE; its plugin manager handles third-party plugin updates.")

    @staticmethod
    def merge_xml(target, source):
        target.attrib.update(source.attrib)
        if source.text and source.text.strip():
            if source.get("name") == "PropertyService":
                old = json.loads(target.text or "{}")
                new = json.loads(source.text)
                old.setdefault("keyToString", {}).update(new.get("keyToString", {}))
                target.text = json.dumps(old)
            elif source.get("name") == "ToolWindowLayout":
                old = json.loads(target.text or "{}")
                old.setdefault("layouts", {}).update(json.loads(source.text).get("layouts", {}))
                target.text = json.dumps(old)
            else:
                target.text = source.text
        for child in source:
            key = next((name for name in ("name", "id", "key", "value") if child.get(name) is not None), None)
            existing = next((node for node in target if node.tag == child.tag and (node.findtext("text") == child.findtext("text") if child.tag == "shortcut-conflict" else key is None or node.get(key) == child.get(key))), None)
            if existing is None:
                target.append(child)
            else:
                Configurator.merge_xml(existing, child)

    def font(self, entry):
        metadata = entry["font"]
        if self.system.kind == "macos":
            package = metadata.get("macos", {}).get("package")
            if not package:
                raise SetupError("Unsupported font on macOS: no Homebrew cask definition.")
            self.packages.install([package], cask=True)
            return
        destination = self.system.data / "fonts" / entry["family"]
        with tempfile.TemporaryDirectory(prefix="editor-font-") as directory:
            definition = metadata.get("linux", {})
            if definition:
                if definition.get("provider") != "github-release":
                    raise SetupError("Unsupported font provider.")
                asset = release_asset(self.repository.network, definition["repository"], definition["asset"])
                archive = self.repository.network.download(asset["browser_download_url"], Path(directory) / asset["name"], asset["digest"])
                extracted = Path(directory) / "fonts"
                extract(archive, extracted)
            else:
                extracted = Path(directory) / "fonts"
                self.repository.stage("fonts/" + entry["family"], extracted)
            files = [path for path in extracted.rglob("*") if path.is_file() and path.suffix.lower() in (".ttf", ".otf")]
            if not files:
                raise SetupError("Font family contains no TTF/OTF files.")
            for source in files:
                self.backups.write(destination / source.relative_to(extracted), source.read_bytes(), entry["id"])
            for source in extracted.rglob("*"):
                if source.is_file() and ("license" in source.name.lower() or source.name.lower().startswith("ofl")):
                    self.backups.write(destination / source.relative_to(extracted), source.read_bytes(), entry["id"])
        if shutil.which("fc-cache"):
            run(["fc-cache", "-f", destination])
        print(f"Installed {metadata['name']} per user. Existing app font preferences were preserved.")
