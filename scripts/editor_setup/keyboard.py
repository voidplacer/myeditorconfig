from __future__ import annotations

import ast
import json
import os
import plistlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from .common import SetupError, run, safe_user_path


CAPS = 0x700000039
ESCAPE = 0x700000029


def mac_mappings(output):
    if output.strip() in ("(null)", "null", "") or re.fullmatch(r"\(\s*\)", output.strip()):
        return []
    mappings = []
    for block in re.findall(r"\{([^{}]*)\}", output, re.S):
        values = dict(re.findall(r"(HIDKeyboardModifierMapping(?:Src|Dst))\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;", block))
        if set(values) != {"HIDKeyboardModifierMappingSrc", "HIDKeyboardModifierMappingDst"}:
            raise SetupError("Cannot safely parse the current macOS keyboard mapping.")
        mappings.append({key: int(value, 0) if value.startswith("0x") else int(value) for key, value in values.items()})
    if not mappings:
        raise SetupError("Unrecognized hidutil output; keyboard settings were not changed.")
    return mappings


class Keyboard:
    def __init__(self, system, backups, repository, *, assume_yes=False):
        self.system = system
        self.backups = backups
        self.repository = repository
        self.assume_yes = assume_yes
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        self.backend = "macos" if system.kind == "macos" else "gnome" if "gnome" in desktop else "kde" if "kde" in desktop else None
        self.state_path = system.state / f"caps-{self.backend}.json"

    def read(self):
        if self.backend == "gnome":
            if not shutil.which("gsettings") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                raise SetupError("GNOME keyboard changes require a local desktop session with gsettings and D-Bus.")
            writable = run(["gsettings", "writable", "org.gnome.desktop.input-sources", "xkb-options"], capture=True).stdout.strip()
            if writable != "true":
                raise SetupError("GNOME xkb-options is locked or unavailable.")
            text = run(["gsettings", "get", "org.gnome.desktop.input-sources", "xkb-options"], capture=True).stdout.strip().removeprefix("@as ")
            options = ast.literal_eval(text)
            if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
                raise SetupError("Unexpected GNOME keyboard options.")
            return {"options": options}
        if self.backend == "kde":
            reader = shutil.which("kreadconfig6") or shutil.which("kreadconfig5")
            writer = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
            if not reader or not writer:
                raise SetupError("KDE keyboard changes require kreadconfig and kwriteconfig.")
            options = run([reader, "--file", "kxkbrc", "--group", "Layout", "--key", "Options", "--default", ""], capture=True).stdout.strip()
            reset = run([reader, "--file", "kxkbrc", "--group", "Layout", "--key", "ResetOldOptions", "--default", "false"], capture=True).stdout.strip()
            return {"options": [item for item in options.split(",") if item], "reset": reset == "true"}
        if self.backend == "macos":
            output = run(["/usr/bin/hidutil", "property", "--get", "UserKeyMapping"], capture=True).stdout
            return {"mappings": mac_mappings(output)}
        raise SetupError("Caps Lock remapping is supported only in GNOME, KDE Plasma, and macOS, not this session.")

    def enabled(self, state):
        if self.backend == "macos":
            return any(item["HIDKeyboardModifierMappingSrc"] == CAPS and item["HIDKeyboardModifierMappingDst"] == ESCAPE for item in state["mappings"])
        return any(item in ("caps:escape", "caps:swapescape") for item in state["options"]) and (self.backend != "kde" or state["reset"])

    def status(self):
        try:
            state = self.read()
            suffix = "configured; login may be required" if self.backend == "kde" else "hidutil mapping" if self.backend == "macos" else "desktop setting"
            return ("ON" if self.enabled(state) else "OFF") + f" ({suffix})", None
        except (SetupError, ValueError, SyntaxError) as error:
            return "unsupported/unknown", str(error)

    def toggle(self, desired=None):
        from .common import confirm

        current = self.read()
        enabled = self.enabled(current)
        if desired is not None and enabled == desired:
            print(f"Caps Lock as Escape is already {'enabled' if desired else 'disabled'}; nothing changed.")
            return
        owned = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        if owned and owned.get("backend") != self.backend:
            raise SetupError("Keyboard state belongs to a different backend.")
        if enabled and not owned:
            if not self.assume_yes and not confirm("This mapping was not enabled by this installer. Remove only its Caps Lock → Escape setting?"):
                raise SetupError("Existing keyboard mapping preserved.")
        if not enabled:
            owned = {"backend": self.backend, "before": current}
            self.backups.write(self.state_path, json.dumps(owned, indent=2) + "\n", "caps-lock")
        prior = owned["before"] if owned else {"mappings": [], "options": [], "reset": False}
        if self.backend == "macos":
            others = [item for item in current["mappings"] if item["HIDKeyboardModifierMappingSrc"] != CAPS]
            restored = [item for item in prior["mappings"] if item["HIDKeyboardModifierMappingSrc"] == CAPS]
            wanted = others + (restored if enabled else [{"HIDKeyboardModifierMappingSrc": CAPS, "HIDKeyboardModifierMappingDst": ESCAPE}])
            self.backups.write(self.system.state / "caps-macos-before.json", json.dumps(current, indent=2), "caps-lock")
            run(["/usr/bin/hidutil", "property", "--set", json.dumps({"UserKeyMapping": wanted})])
            self.macos_agent(not enabled)
        else:
            original_caps = [item for item in prior["options"] if item.startswith("caps:")]
            options = [item for item in current["options"] if not item.startswith("caps:")]
            options.extend(original_caps if enabled else ["caps:escape"])
            if self.backend == "gnome":
                self.backups.write(self.system.state / "caps-gnome-before.json", json.dumps(current), "caps-lock")
                run(["gsettings", "set", "org.gnome.desktop.input-sources", "xkb-options", repr(options)])
            else:
                target = safe_user_path(self.system.config / "kxkbrc", self.system.home)
                self.backups.remember(target, "caps-lock")
                writer = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
                run([writer, "--file", "kxkbrc", "--group", "Layout", "--key", "Options", ",".join(options)])
                original_others = [item for item in prior["options"] if not item.startswith("caps:")]
                reset = prior.get("reset", False) if enabled and options == original_others + original_caps else True
                run([writer, "--file", "kxkbrc", "--group", "Layout", "--key", "ResetOldOptions", "--type", "bool", str(reset).lower()])
                if shutil.which("dbus-send"):
                    run(["dbus-send", "--session", "--type=signal", "/Layouts", "org.kde.keyboard.reloadConfig"], check=False)
                print("KDE mapping saved. Log out and back in if it does not take effect immediately; live activation is not verified.")
        if enabled and self.state_path.exists():
            self.backups.remove(self.state_path, "caps-lock")
        expected = not enabled
        if self.enabled(self.read()) != expected:
            raise SetupError("Keyboard setting could not be verified; check the backup before retrying.")
        print(f"Caps Lock → Escape: {'enabled' if expected else 'disabled; previous Caps setting restored'}.")

    def macos_agent(self, enable):
        helper = self.system.state / "caps-login.py"
        agent = self.system.home / "Library/LaunchAgents/local.myeditorconfig.caps-escape.plist"
        label = f"gui/{os.getuid()}/local.myeditorconfig.caps-escape"
        if not enable:
            run(["launchctl", "bootout", label], check=False, capture=True)
            for path in (helper, agent):
                if path.exists():
                    self.backups.remove(path, "caps-lock")
            return
        with tempfile.TemporaryDirectory(prefix="editor-caps-") as directory:
            source = self.repository.get("scripts/macos_caps.py", Path(directory) / "helper.py")
            self.backups.write(helper, source.read_bytes(), "caps-lock")
        prefix = run(["brew", "--prefix"], capture=True).stdout.strip()
        python = Path(prefix) / "bin/python3"
        if not python.is_file():
            python = Path(sys.executable)
        settings = {
            "Label": "local.myeditorconfig.caps-escape",
            "ProgramArguments": [str(python), str(helper)],
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": f"{prefix}/bin:/usr/bin:/bin"},
        }
        self.backups.write(agent, plistlib.dumps(settings), "caps-lock")
        run(["launchctl", "bootout", label], check=False, capture=True)
        run(["launchctl", "bootstrap", f"gui/{os.getuid()}", agent])
        print("macOS mapping is reapplied at login. Other remapping tools and newly connected keyboards may require separate attention.")
