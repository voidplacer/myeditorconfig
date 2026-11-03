#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from editor_setup.common import Backups, Platform, SetupError, confirm
from editor_setup.config import Configurator
from editor_setup.keyboard import Keyboard
from editor_setup.menu import choose
from editor_setup.packages import Packages
from editor_setup.remote import Network, Repository


def entries_for(repository, keyboard, system):
    catalog = repository.read_json("catalog.json")
    if catalog.get("schema") != 1:
        raise SetupError("Unsupported catalog schema.")
    entries = catalog["applications"]
    families = sorted({name.split("/")[1] for name in repository.files("fonts") if len(name.split("/")) >= 3})
    for family in families:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", family):
            raise SetupError("Unsafe font family directory name.")
        descriptor = f"fonts/{family}/font.json"
        metadata = repository.read_json(descriptor) if descriptor in repository.paths else {"name": family}
        entry = {"id": "font-" + family, "name": "Font: " + metadata["name"], "handler": "font", "family": family, "font": metadata}
        if system.kind == "macos" and not metadata.get("macos", {}).get("package"):
            entry["unsupported"] = "This family has no Homebrew cask definition."
        entries.append(entry)
    status, reason = keyboard.status()
    current = "Enabled" if status.startswith("ON") else "Disabled" if status.startswith("OFF") else "Unknown"
    for desired in (True, False):
        entries.append({"id": "caps-lock-on" if desired else "caps-lock-off", "name": "Enable Caps Lock as Escape" if desired else "Disable Caps Lock as Escape", "handler": "keyboard", "desired": desired, "status": f"Current: {current}" + (" " + status.split(" ", 1)[1] if " " in status else ""), "unsupported": reason,
                        "description": "On: Caps Lock sends Escape. Off: restore the saved Caps Lock setting (or remove this remapping). Other keys stay unchanged. KDE may need a new login."})
    identities = [entry["id"] for entry in entries]
    if len(identities) != len(set(identities)):
        raise SetupError("Duplicate catalog entry IDs.")
    return entries


def validate_selection(entries):
    identities = {entry["id"] for entry in entries}
    if {"caps-lock-on", "caps-lock-off"} <= identities:
        raise SetupError("Select either Enable or Disable Caps Lock as Escape, not both.")
    groups = {}
    for entry in entries:
        group = entry.get("exclusive_group")
        if group:
            if group in groups:
                raise SetupError(f"Choose only one {group} style: {groups[group]} or {entry['id']}. The interactive menu unchecks the previous style automatically.")
            groups[group] = entry["id"]
    if "ideavim" in identities and identities & {"clion", "goland"}:
        raise SetupError("Standalone IdeaVim conflicts with the JetBrains profile. Select one profile per run.")
    for entry in entries:
        if entry.get("unsupported"):
            raise SetupError(f"{entry['name']}: {entry['unsupported']}")


def complete_selection(selected, entries):
    catalog = {entry["id"]: entry for entry in entries}
    wanted = {entry["id"] for entry in selected}
    pending = list(wanted)
    while pending:
        parent = catalog[pending.pop()].get("parent")
        if parent and parent not in wanted:
            if parent not in catalog:
                raise SetupError(f"Unknown parent selection: {parent}")
            wanted.add(parent)
            pending.append(parent)
    return [entry for entry in entries if entry["id"] in wanted]


def installation_actions(selected, system):
    actions = []
    for entry in selected:
        if entry.get("handler") == "nvim-extra":
            continue
        action = dict(entry)
        if entry.get("handler") == "nvim":
            extras = [child for child in selected if child.get("parent") == entry["id"]]
            action["extras"] = [child["module"] for child in extras]
            action["extra_packages"] = list(dict.fromkeys(package for child in extras for package in child["dependencies"][system.kind]))
            action["requires_jdk"] = any(child.get("requires_jdk") for child in extras)
        actions.append(action)
    return actions


def describe(entries, system, repository):
    print(f"Platform: {system.kind} {system.release} ({system.machine})")
    print(f"Source: {repository.remote + '@' + repository.ref if repository.remote else repository.root}")
    for entry in entries:
        print(f"\n{entry['id']}: {entry['name']}")
        if entry.get("handler") == "keyboard":
            print(f"  {entry['status']}. Requested: {'Enabled' if entry['desired'] else 'Disabled'}.")
            print("  " + entry["description"])
        elif entry.get("handler") == "font":
            print("  Download/install only this font family; Homebrew on macOS, official release or bundled files on Linux.")
        elif entry.get("handler") == "nvim-extra":
            print(f"  Enable optional extra: lazyvim.plugins.extras.{entry['module']}")
            if entry["dependencies"][system.kind]:
                print("  Install/update prerequisites: " + ", ".join(entry["dependencies"][system.kind]))
            if entry.get("requires_jdk"):
                print("  Reuse an installed equal/newer JDK; otherwise install the newest available distro/Homebrew LTS JDK. No global Java switch.")
            print("  Applied together with Neovim. LazyVim manages its plugins and language tools on launch.")
        else:
            package = entry.get("packages", {}).get(system.kind)
            if entry.get("handler") == "nvim":
                print("  Require repository Neovim >= 0.11.2 with LuaJIT; install/check LazyVim tools before applying overlays.")
                extras = [child["module"] for child in entries if child.get("parent") == entry["id"]]
                print("  Optional extras to enable: " + (", ".join(extras) or "none; preserve existing extras"))
            elif package:
                print(f"  Install/update package: {package}; source priority: distro/Homebrew, approved binary AUR/vendor source if needed.")
            if entry.get("handler") == "shell":
                print(f"  Install/update zoxide and eza; append/refresh managed block in ~/.{entry['id']}rc, preserving existing content.")
            for name in repository.files(entry["prefix"]):
                print("  Selected file: " + name)
        print("  No change performed (plan only).")
    print("\nBackups on application: Documents/editor_backup/<timestamp>/<entry>/, or ~/editor_backup/ if unavailable.")


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Selectively install my personal editor configs.")
    parser.add_argument("--list", action="store_true", help="list entries without installing anything")
    parser.add_argument("--select", help="comma-separated entry IDs (see --list)")
    parser.add_argument("--dry-run", action="store_true", help="show a plan, without package managers or config writes")
    parser.add_argument("--apply", action="store_true", help="apply an explicit --select or --restore request")
    parser.add_argument("--yes", action="store_true", help="accept installer confirmations, but not pacman/yay recipe-review prompts")
    parser.add_argument("--remote", help=argparse.SUPPRESS)
    parser.add_argument("--ref", help=argparse.SUPPRESS)
    parser.add_argument("--restore", type=Path, help="restore a user-config backup manifest; does not uninstall applications")
    args = parser.parse_args(arguments)
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    if args.select and not (args.apply or args.dry_run):
        parser.error("--select requires --dry-run or --apply")
    if args.restore:
        if args.select or args.list or args.remote:
            parser.error("--restore must be run locally and cannot be combined with selection/list options")
        if not args.apply:
            print("Restore preview:")
            for record in json.loads(args.restore.read_text())["records"]:
                print(f"  {record['kind']}: {record['path']}")
            print("Add --apply to restore. This can replace current files with the saved versions.")
            return 0
        if os.geteuid() == 0:
            raise SetupError("Run restore as the normal user who owns the backup, not root.")
        if args.yes or confirm("Restore this backup? Current files at its recorded destinations will be replaced"):
            Backups.restore(args.restore, Path.home())
            print("User files restored. Runtime keyboard settings, installed applications, and vendor repositories are not rolled back; see docs/installer.md.")
        return 0
    system = Platform()
    if not (args.list or args.dry_run) and os.geteuid() == 0:
        raise SetupError("Run the installer as your normal user, not root.")
    if system.home.is_symlink() or any(char in str(system.home) for char in ('\n', '"', '`', '$')):
        raise SetupError("Unsupported home-directory path; refusing unsafe generated launchers.")
    network = Network()
    repository = Repository(None if args.remote else Path(__file__).resolve().parents[1], remote=args.remote, ref=args.ref, network=network)
    backups = Backups(system.home)
    packages = Packages(system, network, backups, assume_yes=args.yes)
    keyboard = Keyboard(system, backups, repository, assume_yes=args.yes)
    entries = entries_for(repository, keyboard, system)
    if args.list:
        for index, entry in enumerate(entries, 1):
            print(f"{index:2}. {entry['id']:22} {entry['name']}" + (f" [{entry['status']}]" if entry.get("status") else "") + (f" — unsupported: {entry['unsupported']}" if entry.get("unsupported") else ""))
        return 0
    if args.select:
        requested = set(args.select.split(","))
        unknown = requested - {entry["id"] for entry in entries}
        if unknown:
            raise SetupError("Unknown selection IDs: " + ", ".join(sorted(unknown)))
        selected = [entry for entry in entries if entry["id"] in requested]
    elif args.dry_run:
        parser.error("--dry-run requires --select")
    else:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise SetupError("The menu needs a terminal. Use --list or --select ID --dry-run for noninteractive inspection.")
        selected = choose(entries, system)
    if not selected:
        print("Nothing selected; nothing changed.")
        return 0
    selected = complete_selection(selected, entries)
    validate_selection(selected)
    if args.dry_run:
        describe(selected, system, repository)
        return 0
    if not args.yes and not confirm("Install/update and apply: " + ", ".join(entry["name"] for entry in selected)):
        print("Cancelled; nothing changed.")
        return 0
    os.environ["PATH"] = str(system.home / ".local/bin") + ":" + os.environ.get("PATH", "")
    configurator = Configurator(system, repository, packages, backups)
    actions = installation_actions(selected, system)
    failures = 0
    with tempfile.TemporaryDirectory(prefix="editor-selection-") as directory:
        for entry in actions:
            print(f"\n== {entry['name']} ==", flush=True)
            try:
                handler = entry.get("handler")
                staged = Path(directory) / entry["id"]
                if entry.get("prefix"):
                    repository.stage(entry["prefix"], staged)
                    configurator.validate(staged)
                if handler == "jetbrains":
                    configurator.assert_ide_closed(entry["id"])
                if handler == "font":
                    configurator.font(entry)
                elif handler == "keyboard":
                    keyboard.toggle(entry["desired"])
                else:
                    if handler == "nvim":
                        packages.lazyvim(entry["extra_packages"])
                        if entry["requires_jdk"]:
                            entry["java_home"] = str(packages.ensure_jdk())
                    elif handler != "files":
                        source = packages.install_application(entry)
                        print("Update mechanism: " + source)
                    configurator.apply(entry, staged)
                print("Done: " + entry["name"])
            except (SetupError, OSError, ValueError, KeyError, SyntaxError) as error:
                failures += 1
                print(f"FAILED/SKIPPED: {entry['name']}: {error}", file=sys.stderr)
    if backups.records:
        print(f"\nBackups: {backups.root}")
        print(f"Restore preview: python3 scripts/install.py --restore '{backups.root / 'manifest.json'}'")
    print(f"\nFinished: {len(actions) - failures} successful, {failures} failed/skipped application actions. Selected extras are applied with their parent. No unselected configs were downloaded.")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SetupError, OSError, ValueError, KeyError, SyntaxError, KeyboardInterrupt) as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        sys.exit(130 if isinstance(error, KeyboardInterrupt) else 1)
