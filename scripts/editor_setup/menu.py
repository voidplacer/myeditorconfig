from __future__ import annotations

try:
    import curses
except ImportError:
    curses = None
import os
import subprocess
import textwrap

REPOSITORY = "https://github.com/voidplacer/myeditorconfig"


def palette():
    if curses is None or "NO_COLOR" in os.environ:
        return {}
    try:
        curses.start_color()
        if not curses.has_colors() or curses.COLORS < 8 or curses.COLOR_PAIRS < 4:
            return {}
        curses.use_default_colors()
        styles = {}
        for index, (role, foreground) in enumerate((("accent", curses.COLOR_CYAN), ("selected", curses.COLOR_GREEN), ("warning", curses.COLOR_YELLOW)), 1):
            curses.init_pair(index, foreground, -1)
            styles[role] = curses.color_pair(index)
        return styles
    except curses.error:
        return {}


def probe(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=1, check=False)
        return result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ""


def clean(value):
    return "".join(character for character in str(value) if character.isprintable())[:200]


def system_details(system):
    if system is None:
        return ["OS: unknown", "Desktop: unknown", "Terminal: unknown", "Shell: unknown"]
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION", "")
    if system.kind == "macos":
        desktop = "macOS desktop"
        desktop_version = system.release
    elif "gnome" in desktop.lower():
        desktop_version = probe(["gnome-shell", "--version"])
    elif any(name in desktop.lower() for name in ("kde", "plasma")):
        desktop_version = probe(["plasmashell", "--version"])
    else:
        desktop_command = next((command for name, command in (("xfce", "xfce4-session"), ("cinnamon", "cinnamon"), ("mate", "mate-session"), ("lxqt", "lxqt-session")) if name in desktop.lower()), None)
        desktop_version = probe([desktop_command, "--version"]) if desktop_command else ""
    terminal = os.environ.get("TERM_PROGRAM", "")
    terminal_version = os.environ.get("TERM_PROGRAM_VERSION", "")
    if os.environ.get("KITTY_PID"):
        terminal = "kitty"
    elif os.environ.get("ALACRITTY_SOCKET"):
        terminal = "alacritty"
    elif os.environ.get("GNOME_TERMINAL_SCREEN"):
        terminal = "gnome-terminal"
    elif os.environ.get("KONSOLE_VERSION"):
        terminal = "konsole"
        encoded = os.environ["KONSOLE_VERSION"]
        if encoded.isdigit():
            terminal_version = ".".join(str(int(encoded) // divisor % 100) for divisor in (10000, 100, 1))
    shells = {"bash", "zsh", "fish", "dash", "sh", "ksh", "nu", "tcsh"}
    terminals = {"kitty", "alacritty", "gnome-terminal", "gnome-terminal-server", "konsole", "wezterm", "foot", "xterm", "ghostty", "Terminal", "iTerm2"}
    shell = ""
    parent = os.getppid()
    for unused in range(12):
        process = probe(["ps", "-p", str(parent), "-o", "ppid=", "-o", "comm="])
        fields = process.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            break
        name = os.path.basename(fields[1]).lstrip("-")
        if name in shells:
            shell = name
        if not terminal and name in terminals:
            terminal = name.removesuffix("-server")
        parent = int(fields[0])
        if parent <= 1:
            break
    shell_detail = probe([shell, "--version"]) if shell in ("bash", "zsh", "fish", "nu") else ""
    if not shell:
        login = os.path.basename(os.environ.get("SHELL", ""))
        shell_detail = f"unknown (login shell: {login or 'unknown'})"
    if not terminal_version and terminal in ("kitty", "alacritty", "gnome-terminal", "konsole", "wezterm", "foot", "xterm", "ghostty"):
        terminal_version = probe([terminal, "-version" if terminal == "xterm" else "--version"])
    multiplex = " (inside tmux)" if os.environ.get("TMUX") else " (inside screen)" if os.environ.get("STY") else ""
    name = {"ubuntu": "Ubuntu", "fedora": "Fedora", "arch": "Arch Linux", "macos": "macOS"}[system.kind]
    return [clean(f"OS: {name} {system.release or 'rolling release'} | {system.machine}"),
            clean(f"Desktop: {desktop or 'unknown / headless'} | {desktop_version or 'version unknown'}"),
            clean(f"Terminal: {terminal or 'unknown'} | {terminal_version or 'version unknown'}{multiplex}"),
            clean(f"Shell: {shell_detail or shell or 'unknown'}")]


def category(entry):
    identity = entry["id"]
    if entry.get("parent") == "nvim":
        return "Editors and IDEs"
    if entry.get("handler") == "keyboard":
        return "Keyboard"
    if entry.get("handler") == "font":
        return "Fonts"
    if identity in ("kitty", "tmux", "alacritty"):
        return "Terminals"
    if identity in ("bash", "zsh"):
        return "Shells"
    if identity in ("zed", "nvim", "vscode", "clion", "goland", "ideavim"):
        return "Editors and IDEs"
    return "Developer tools"


class Selection:
    def __init__(self, entries):
        self.entries = entries
        self.selected = set()
        self.expanded = set()
        self.cursor = 0
        self.groups = [name for name in ("Editors and IDEs", "Terminals", "Shells", "Fonts", "Developer tools", "Keyboard") if any(category(entry) == name for entry in entries)]

    def children(self, identity):
        return [entry for entry in self.entries if entry.get("parent") == identity]

    def focused_group(self):
        action, value, label = self.rows()[self.cursor]
        return value if action == "folder" else category(value) if action == "entry" else None

    def breadcrumb(self):
        action, value, label = self.rows()[self.cursor]
        if action == "entry":
            parent = value.get("parent")
            if parent:
                name = next(entry["name"] for entry in self.entries if entry["id"] == parent)
                return f"Configuration tree / {name}"
            if self.children(value["id"]):
                return f"Configuration tree / {value['name']}"
        group = self.focused_group()
        return "Configuration tree" + (f" / {group}" if group else " / Actions")

    def back(self):
        action, value, label = self.rows()[self.cursor]
        if action == "entry":
            parent = value.get("parent")
            if parent:
                self.expanded.discard(parent)
                self.cursor = next(index for index, (action, value, label) in enumerate(self.rows()) if action == "entry" and value["id"] == parent)
                return
            if value["id"] in self.expanded:
                self.expanded.discard(value["id"])
                return
        group = self.focused_group()
        if group:
            self.expanded.discard(group)
            self.cursor = next(index for index, (action, value, label) in enumerate(self.rows()) if action == "folder" and value == group)

    def open(self):
        action, value, label = self.rows()[self.cursor]
        if action == "folder" or (action == "entry" and self.children(value["id"])):
            identity = value if action == "folder" else value["id"]
            if identity in self.expanded:
                self.cursor += 1
            else:
                self.expanded.add(identity)

    def rows(self):
        rows = []
        for group in self.groups:
            children = [entry for entry in self.entries if category(entry) == group]
            count = sum(entry["id"] in self.selected for entry in children)
            disclosure = "-" if group in self.expanded else "+"
            rows.append(("folder", group, f"[/] [{disclosure}] {group}/ ({count} selected)"))
            if group in self.expanded:
                for entry in children:
                    if entry.get("parent"):
                        continue
                    mark = "x" if entry["id"] in self.selected else " "
                    suffix = " [unavailable]" if entry.get("unsupported") else ""
                    disclosure = ("[-] " if entry["id"] in self.expanded else "[+] ") if self.children(entry["id"]) else ""
                    rows.append(("entry", entry, f"[{mark}] {disclosure}{entry['name']}{suffix}"))
                    if entry["id"] in self.expanded:
                        for child in self.children(entry["id"]):
                            mark = "x" if child["id"] in self.selected else " "
                            suffix = " [unavailable]" if child.get("unsupported") else ""
                            rows.append(("entry", child, f"[{mark}] {child['name']}{suffix}"))
        rows.extend([("install", None, f"[>] Install selected ({len(self.selected)})"), ("quit", None, "[q] Quit without installing")])
        return rows

    def activate(self, index):
        self.cursor = index
        action, value, label = self.rows()[index]
        if action == "folder":
            self.expanded.symmetric_difference_update({value})
        elif action == "quit":
            return []
        elif action == "install" and self.selected:
            return [entry for entry in self.entries if entry["id"] in self.selected]
        elif action == "entry" and not value.get("unsupported"):
            identity = value["id"]
            if identity in self.selected:
                self.selected.remove(identity)
                self.selected.difference_update(child["id"] for child in self.children(identity))
                self.expanded.discard(identity)
            else:
                group = value.get("exclusive_group")
                if group:
                    self.selected.difference_update(entry["id"] for entry in self.entries if entry.get("exclusive_group") == group)
                if value.get("handler") == "keyboard":
                    self.selected.difference_update(entry["id"] for entry in self.entries if entry.get("handler") == "keyboard")
                self.selected.add(identity)
                if self.children(identity):
                    self.expanded.add(identity)
                if value.get("parent"):
                    self.selected.add(value["parent"])
                    self.expanded.add(value["parent"])
        return None

    def help(self):
        action, entry, label = self.rows()[self.cursor]
        if action == "entry":
            if entry.get("handler") == "keyboard":
                explanation = "Enable: Caps Lock sends Escape instead of toggling letter case." if entry.get("desired") else "Disable: restore the saved Caps Lock behavior, or remove its Escape remapping."
                return clean(entry.get("unsupported") or entry.get("status", "").split(" (", 1)[0] + ". " + explanation + " Other keys stay unchanged.")
            return clean(entry.get("unsupported") or entry.get("description") or "Space checks this config. Left/h collapses its parent folder; selections are kept.")
        if action == "folder":
            if entry == "Keyboard":
                keyboard = next(item for item in self.entries if item.get("handler") == "keyboard")
                instruction = "Choose Enable or Disable below. Left/h collapses this folder." if entry in self.expanded else "Expand to choose Enable or Disable Caps Lock as Escape."
                return clean(keyboard.get("status", "") + ". " + (keyboard.get("unsupported") or instruction))
            instruction = "collapses" if entry in self.expanded else "expands"
            return f"Space/Enter {instruction} this folder. Right opens it; Left/h collapses it. Selections are kept."
        if action == "install":
            if not self.selected:
                return "No configs selected. Expand a folder, then check an item before installing."
            return "Install/update and apply only checked configs, including those in collapsed folders. Confirmation is required."
        return "Quit without installing or applying anything."


def spaced_rows(model):
    rows = model.rows()
    lines = []

    def has_next_sibling(index):
        for action, value, label in rows[index + 1:]:
            if action != "entry":
                return False
            if not value.get("parent"):
                return True
        return False

    for index, (action, value, label) in enumerate(rows):
        if action == "install":
            lines.extend([(None, ""), (None, ""), (None, "Actions")])
        if action == "folder" and value == "Keyboard" and value in model.expanded:
            lines.append((index, label))
            keyboard = next(entry for entry in model.entries if entry.get("handler") == "keyboard")
            lines.append((None, "      |  " + keyboard.get("status", "Current: Unknown")))
            continue
        if action == "entry":
            if value.get("parent"):
                parent = next(position for position, (kind, entry, text) in enumerate(rows[:index]) if kind == "entry" and entry["id"] == value["parent"])
                stem = "|   " if has_next_sibling(parent) else "    "
                branch = "|--" if index + 1 < len(rows) and rows[index + 1][0] == "entry" and rows[index + 1][1].get("parent") == value["parent"] else "`--"
                label = f"      {stem}{branch} {label}"
            else:
                branch = "|--" if has_next_sibling(index) else "`--"
                label = f"      {branch} {label}"
        lines.append((index, label))
    return lines


def viewport(lines, cursor, height, start):
    focus = next(index for index, (option, label) in enumerate(lines) if option == cursor)
    if focus < start:
        start = focus
    elif focus >= start + height:
        start = focus - height + 1
    return max(0, min(start, max(0, len(lines) - height)))


def plain_choose(model, details):
    print("Designed by voidplacer")
    print(REPOSITORY)
    print("\n".join(details))
    while True:
        print("\n" + model.breadcrumb())
        number_width = max(2, len(str(len(model.rows()))))
        for index, label in spaced_rows(model):
            print(f"{index + 1:>{number_width}}. {label}" if index is not None else " " * (number_width + 2) + label)
        print(model.help())
        try:
            answer = input("Number expands/selects; h collapses parent; q quits: ").strip().lower()
        except EOFError:
            return []
        if answer in ("q", "quit"):
            return []
        if answer in ("b", "h", "left"):
            model.back()
        elif answer.isdigit() and 1 <= int(answer) <= len(model.rows()):
            result = model.activate(int(answer) - 1)
            if result is not None:
                return result


def choose(entries, system=None):
    model = Selection(entries)
    details = system_details(system)

    def screen(window):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        window.keypad(True)
        colours = palette()
        start = 0
        while True:
            window.erase()
            height, width = window.getmaxyx()
            if height < 18 or width < 60:
                raise curses.error("Use the plain-text menu for a small terminal")
            def draw(row, text, style=0):
                text = clean(text).encode("ascii", "replace").decode()
                available = width - 3
                if len(text) > available:
                    text = text[:available - 3] + "..."
                window.addnstr(row, 1, text, available, style)
            draw(0, "Designed by voidplacer", curses.A_BOLD | curses.A_UNDERLINE | colours.get("accent", 0))
            draw(1, REPOSITORY)
            for row, detail in enumerate(details, 2):
                draw(row, detail)
            draw(6, "-" * (width - 3))
            draw(7, model.breadcrumb(), curses.A_BOLD | colours.get("accent", 0))
            rows = model.rows()
            number_width = max(2, len(str(len(rows))))
            list_row = 8
            visible = height - list_row - 4
            lines = spaced_rows(model)
            start = viewport(lines, model.cursor, visible, start)
            for row, (index, label) in enumerate(lines[start:start + visible], list_row):
                if index is None:
                    draw(row, " " * (number_width + 4) + label, curses.A_DIM)
                    continue
                action, entry, original_label = rows[index]
                role = "warning" if action == "entry" and entry.get("unsupported") else "selected" if action == "install" or (action == "entry" and entry["id"] in model.selected) else "accent" if action == "folder" else ""
                style = colours.get(role, 0)
                if index == model.cursor:
                    style |= curses.A_REVERSE | curses.A_BOLD
                pointer = ">" if index == model.cursor else " "
                draw(row, f"{pointer} {index + 1:>{number_width}}  {label}", style)
            for row, line in enumerate(textwrap.wrap(model.help(), width - 3)[:2], height - 4):
                draw(row, line)
            draw(height - 2, "j/k move | Space select | Right open | h close | q quit")
            above = " ^" if start else ""
            below = " v" if start + visible < len(lines) else ""
            draw(height - 1, f"1-9 activate | Selected: {len(model.selected)} | Option {model.cursor + 1}/{len(rows)}{above}{below}")
            window.refresh()
            key = window.get_wch()
            if key in ("q", "Q", "\x1b"):
                return []
            if key in ("j", curses.KEY_DOWN):
                model.cursor = min(len(rows) - 1, model.cursor + 1)
            elif key in ("k", curses.KEY_UP):
                model.cursor = max(0, model.cursor - 1)
            elif key in ("h", curses.KEY_LEFT, curses.KEY_BACKSPACE, "\x7f"):
                model.back()
            elif key == curses.KEY_RIGHT:
                model.open()
            elif key == curses.KEY_RESIZE:
                continue
            else:
                index = int(key) - 1 if isinstance(key, str) and key in "123456789" else model.cursor if key in (" ", "\n", "\r", curses.KEY_ENTER) else None
                if index is not None and index < len(rows):
                    result = model.activate(index)
                    if result is not None:
                        return result
    if curses is None or os.environ.get("TERM", "") in ("", "dumb"):
        return plain_choose(model, details)
    try:
        return curses.wrapper(screen)
    except curses.error:
        return plain_choose(model, details)
