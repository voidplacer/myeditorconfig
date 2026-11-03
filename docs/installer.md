# Installer

The README's one-line command downloads the current installer and opens the selector. Review the selections before applying changes. Cross-platform package operations have automated coverage, but actual installation behavior still depends on the target system and its available packages.

## Local inspection and testing

From the repository directory:

```bash
bash install.sh --local --list
bash install.sh --local --select kitty,tmux,font-caskaydiamono --dry-run
python3 -m unittest discover -s tests -v
```

These inspection commands do not install packages or change configs. Python 3.12+ is required; help, dry-run/list, and restore-preview modes will not bootstrap missing prerequisites. Remote inspection still downloads installer code and menu metadata.

Launch the interactive menu with `bash install.sh --local`. The single inline tree groups Editors and IDEs, Terminals, Shells, Fonts, Developer tools, and Keyboard. Space/Enter expands or collapses a folder in place; its items appear indented directly below it while sibling folders remain in the same tree. Multiple folders may stay expanded. Space/Enter on an item checks/unchecks it. `j`/`k` or Up/Down moves between selectable rows. Right expands a folder or moves to its first child if already expanded; `h`/Left or Backspace collapses the focused branch and focuses its parent. Numbers 1–9 activate numbered rows; row numbers follow the current expanded tree. Checked items remain selected when their folder is collapsed. **Install selected** proceeds to confirmation; expanding or checking an item makes no system changes. Press `q` to quit without installing.

Folder rows and their children are compact, without intervening blank lines. Install and Quit sit under a separate **Actions** heading with a two-line gap from the tree. All collapsed categories and both actions fit a standard 80x24 terminal. Spacers, the keyboard-status line, and headings are not selectable: `j`/`k` skips them. ASCII branches and indentation show parent/child relationships; `[+]` means collapsed and `[-]` means expanded. The focused category is also named above the tree, so the parent remains identifiable when scrolling. Scrolling is stable until the cursor would leave the viewport; `^`/`v` at the bottom indicate more content above/below. Long lines end in `...` instead of silently running off-screen.

Every selectable row is numbered, including rows 10 and above. Numbers are recalculated when branches expand/collapse, with an aligned column wide enough for the largest number. In the fullscreen menu, `1–9` remain single-key shortcuts; use `j`/`k` and Space/Enter for any row, including higher numbers. The plain numbered menu accepts complete multi-digit numbers followed by Enter.

The header credits voidplacer, links to the repository, and reports the OS/distro version, desktop environment/version, terminal/version, and detected invoking shell. Detection is best-effort, read-only, and time-bounded; missing commands, remote/headless sessions, and hidden process ancestry report unknown values rather than block installation. Reported executable versions may differ from an already-running process after an update. Inside tmux/screen, the outer terminal is reported only if it can be identified. This does not expand the supported operating systems.

The menu uses portable ASCII icons: `[/]` folders, `[+]`/`[-]` expansion state, `[x]` selected, `[>]` install, and `[q]` quit. A `>` pointer and highlighted row indicate keyboard focus. No emojis, Nerd Fonts, or downloaded icon packages are needed. Where supported, restrained cyan accents mark headings/folders, green marks selections/install, and yellow marks unavailable entries. Colour is optional; unsupported terminals stay monochrome, and setting `NO_COLOR=1` disables it explicitly. Labels remain meaningful without colour.

The credit reads **Designed by voidplacer**, emphasized with bold and underline where supported. Terminal font size is controlled by the terminal emulator and cannot be increased for one line portably; this installer does not change global font settings or rely on emulator-specific text-scaling escape sequences.

If curses is missing, the terminal is `dumb`, or the screen is smaller than 60 columns by 18 rows, a plain numbered inline tree is used instead. In that mode enter a row number to expand/collapse/check, `h` (or `b`) to collapse the focused branch, or `q` to quit, followed by Enter. Use `--list` or `--dry-run` for noninteractive terminals.

**Keyboard / Caps Lock as Escape** shows **Current: Enabled**, **Disabled**, or **Unknown**, and separate Enable/Disable choices. Only one can be selected. Enabling makes Caps Lock send Escape; disabling restores the saved Caps Lock setting, or removes the Escape remapping when no saved setting exists. Other mappings remain intact. KDE shows the configured state; a new login may be required. Unsupported sessions show their reason and cannot be selected. CLI IDs are `caps-lock-on` and `caps-lock-off`; they request a state rather than blindly toggling it, so repeating the same request does not reverse it.

For an explicit real installation, use `--select kitty,tmux --apply`. This requests confirmation; `--yes` accepts installer confirmations, not pacman/yay transaction and recipe-review prompts. Do not run the installer as root.

First test real installations in a disposable VM or a dedicated test account. Mocked tests cannot establish that an actual package manager, desktop session, or application updater works on every target OS.

## Selective downloads

The published one-liner downloads a bootstrap, resolves the current `main` commit, and downloads a small set of Python installer modules. Repository tree metadata and `catalog.json` build the menu; font descriptors are also menu metadata. All repo files for that run use the resolved commit SHA.

Only selected config trees, selected font archives, and selected app-specific artifacts are downloaded. The installer never clones this config repository or downloads its archive. When Neovim is selected, LazyVim's separate upstream starter repository is cloned if needed.

Bash and Zsh are separate menu options using the same `configs/shell/profile.sh`. Each installs/updates its shell plus `zoxide` and `eza`, preserves existing startup content, and appends a marked block to `~/.bashrc` or `~/.zshrc`. The block ends with the matching `zoxide init --cmd cd bash` or `zoxide init --cmd cd zsh`. Reruns refresh only that block at the end without duplicating it. Existing files are backed up under `editor_backup` before changes. Neither option changes your login shell or installs/replaces Oh My Zsh; existing framework settings remain untouched. Some personal aliases require Linux/Arch tools and are not portable to macOS.

Change existing config contents without editing the script. Application package names and destinations live in `catalog.json`; new applications need a catalog entry. Fonts are discovered automatically from family folders, without a script/catalog edit.

GitHub API limits and network/checksum failures stop the affected action. Files are downloaded into temporary staging before config writes. Supported XML, JSON/JSONC, and TOML files are parsed before package installation. This is not a syntax validator for every application's configuration language.

## Installation and updates

| Platform/source | Behavior and future updates |
| --- | --- |
| Ubuntu | Refresh apt metadata; install/upgrade selected packages with apt. VS Code can add Microsoft's signed apt repository. |
| Fedora | Refresh dnf metadata; explicitly install/upgrade selected packages. VS Code can add Microsoft's signed RPM repository. |
| Arch | First perform a user-reviewed full `pacman -Syu`; then install selected packages. If an approved app is absent, use binary-repackaging AUR entries through yay. Future AUR updates use `yay -Syu`. |
| macOS | Bootstrap/check Homebrew, configure zsh/bash login environment, then use formulae/casks. Future updates use `brew update` and `brew upgrade` (including `--cask --greedy` for self-updating casks). |
| Zed vendor install on Linux | Use the verified official GitHub binary with Zed's official installer. Future updates use Zed's built-in updater; rerunning also checks the upstream version. |
| CLion/GoLand vendor install on Linux | Install official checksummed releases into a user-writable app directory. Future updates use the IDE's built-in updater; rerunning can update installations created by this script. |

Metadata refresh and actual package operations can require sudo even when an app is already installed. Only selected applications and their required dependencies are requested, except that Arch requires a full system upgrade. No background unattended-upgrade jobs are added.

Homebrew remains the preferred macOS source; a newer upstream release alone is not a reason to bypass it. There is no generic unmanaged-binary fallback. If the chosen application has neither an available package nor an implemented official source with an updater, it is reported unsupported.

Homebrew formula dependencies are resolved in topological order, checked for macOS bottles, and explicitly installed with `--force-bottle --ignore-dependencies`. Automatic dependent rebuilding is disabled for these transactions. Casks introducing additional package dependencies currently require manual review rather than allowing an implicit source build. Bootstrapping Homebrew itself uses its official installer; binary Python is installed through Homebrew if needed. Source builds are not requested for applications.

On Arch, yay is bootstrapped from its official checksummed binary and installed as `yay-bin` if absent. Approved AUR recipes are checked for build functions/compiler commands and dependencies outside pacman before yay runs. Packaging an upstream binary is allowed; compiling an application is not. This check cannot prove that arbitrary shell code is harmless: review yay's recipe prompts and cancel suspicious changes. Community recipes are not official vendor repositories.

Existing applications outside the selected package manager are not silently duplicated. Existing Toolbox/externally managed JetBrains installations are reused when current; if outdated, update through their existing updater first and rerun. Unknown/custom installation paths or custom JetBrains properties paths may require manual setup.

Package-manager commands use an English subprocess locale so version checks do not depend on the desktop language. This does not change your system or shell locale. A new LazyVim starter is downloaded before any old Neovim files are removed, and all replacement paths and backups are checked first. Download failures leave the existing Neovim config and data intact.

## Backups and recovery

User-file backups are private and organized as:

```text
Documents/editor_backup/<UTC timestamp>/<selection>/<original path under home>
Documents/editor_backup/<UTC timestamp>/manifest.json
```

The configured XDG Documents directory is used when present and accessible. Otherwise the root is `~/editor_backup/`. Existing files are saved once per run; unchanged files are not rewritten. Originally absent destinations are recorded so restore can remove newly created files. Destination symlinks are refused rather than followed.

An entire-directory snapshot also covers subsequent changes to its children. Later overlay writes cannot overwrite the original snapshot with intermediate files.

Preview and restore a backup using the path printed after installation:

```bash
python3 scripts/install.py --restore /path/to/editor_backup/run/manifest.json
python3 scripts/install.py --restore /path/to/editor_backup/run/manifest.json --apply
```

Restore replaces the recorded user files with their saved versions; review any changes made since the backup. Keep the original backup private. Application binaries, installed packages, newly added vendor repositories, and current desktop/hidutil settings are **not** rolled back by file restore. Use the Caps Lock toggle to undo its runtime mapping before restoring its saved files. GNOME/macOS mapping snapshots are also retained in the user's installer state directory for manual recovery.

First-run Homebrew shell-profile backups are kept separately under `editor_backup/<timestamp>-bootstrap-<pid>/homebrew/`. They are plain saved files, not part of the Python restore manifest. Existing conflicting system repository files are never overwritten. Newly created Microsoft repository files are explicitly named `myeditorconfig-*` under the package manager's configuration directories.

Actions are not a system-wide transaction: a package install or plugin download may succeed before a later config operation fails. The installer reports each failure and continues with other selections. It does not claim that a failed action was rolled back automatically.

## Neovim / LazyVim

The current supported minimum is repository Neovim 0.11.2 with LuaJIT, Git 2.19, and fzf 0.25.1. Requirements follow [LazyVim](https://www.lazyvim.org/) and must be maintained as upstream evolves. An older distro candidate is unsupported: no Neovim upstream binary, PPA, source build, or other fallback is used.

Before applying the overlay, install/check the available distro/Homebrew packages for Git, a C compiler, tree-sitter CLI, curl, lazygit, fzf, ripgrep, fd, clang-format, and unzip. Missing required packages stop the action without replacing the user's Neovim config. Ubuntu 24.04's standard Neovim version may therefore be unsupported even though other installer entries work there.

Selecting Neovim reveals two optional nested checkboxes: **C/C++ support (clangd)** (`nvim-clangd`) and **Java support** (`nvim-java`). Right expands this branch without selecting it; checking an extra automatically checks Neovim. Unchecking Neovim clears its pending extras selections. Extras are applied together with their parent once, not as separate editor installations. CLI requests for an extra also include Neovim automatically. Examples:

```bash
bash install.sh --local --select nvim --dry-run
bash install.sh --local --select nvim,nvim-clangd,nvim-java --dry-run
```

Selecting only Neovim adds no language extras or JDK requirement. Existing enabled extras are preserved, not silently disabled by leaving an install checkbox unchecked; use LazyVim's `:LazyExtras` to remove them deliberately. The clangd option installs distro/Homebrew clangd tooling; some base clang-format packages also bundle clangd even without that extra enabled. LazyVim's normal plugin/tool manager completes the selected language setup on launch.

The Java option detects matching `java` and `javac` from PATH, `JAVA_HOME`, and common distro, Homebrew, macOS JVM, SDKMAN, asdf, and mise locations. It reads the current LTS release list from Eclipse Adoptium's official metadata API, then chooses the newest stable LTS JDK available in the distro repositories or Homebrew (at least Java 21 for this profile). If an installed JDK has the same or a newer **major release**, it is reused without installing another JDK, downgrading, or forcing an LTS replacement for a newer non-LTS release. Normal system updates remain responsible for patch updates to reused packages. If the newest upstream LTS is unavailable, the newest compatible repository LTS is considered. A compatible installed JDK is also kept when no suitable repository LTS exists. A JRE without `javac` does not count as a JDK.

Only if needed does the script install the native JDK package/formula and verify both executables afterward. It downloads no unmanaged JDK and never changes global alternatives or shell startup files. The verified JDK home is configured in a backed-up, managed block in Neovim's `lua/config/options.lua` so a newer keg-only or non-default JDK can be used without changing the user's global Java selection. Metadata errors or a missing compatible JDK stop the Java-enabled Neovim action before config writes.

LazyVim extras are stored as fully qualified module names, with exactly one `lazyvim.plugins.extras.` prefix. New or unversioned state files declare schema version 1 (the full-name format), leaving further migrations to LazyVim. Existing schema versions and unrelated settings are preserved. Rerunning the installer also repairs duplicated prefixes produced by earlier installer versions, with the original `lazyvim.json` backed up before writing.

The previous duplicated-import error was caused by an installer-written state file missing its schema version. LazyVim treated its already-qualified module names as legacy short names and prefixed them again. Installing a language server or JDK cannot fix that malformed Lua import path; the state/schema repair above does. Valid extra definitions are bundled with LazyVim, while their external servers and plugins are separate dependencies.

An existing LazyVim profile is recognized through `lua/config/lazy.lua`; a non-LazyVim config and its standard data/cache/state directories are backed up before installing the official starter. Existing LazyVim preferences outside the selected overlays are preserved. Start `nvim` afterward to complete normal plugin/tool/parser setup. Compiling those LazyVim requirements is the explicit exception to binary-only application installation. Their normal plugin/tool-manager update mechanisms remain available.

Neovim installation does not write `~/.clang-format`. Under Developer tools, **Shared formatting (LLVM)** (`formatting`) and **C/C++ formatting (Allman)** (`formatting-allman`) are independent optional style choices targeting that file. Choosing either automatically unchecks the other; Neovim stays selected and works with either choice. Choosing neither preserves the current home-level formatting. Existing files are backed up before application; project-specific formatting files are untouched. Explicit CLI requests containing both style IDs are rejected before installation rather than choosing an arbitrary winner. Standalone IdeaVim still cannot be combined with the newer JetBrains profile in the same run.

## JetBrains profiles

CLion and GoLand are independent selections. Close the IDE before installation/configuration. Config paths are resolved separately for Linux and macOS. The supplied profiles and custom plugin are currently restricted to JetBrains 2026.2 (build 262); another version is reported unsupported rather than blindly receiving this profile.

IdeaVim, Catppuccin Theme, and Catppuccin Icons are installed through the IDE's official `installPlugins` command, with compatibility handled by its plugin repository. The custom pane-navigation plugin is a small prebuilt artifact in `plugins/jetbrains/coding-profile/release.json`, with its hash and source hashes; clients do not compile Java. It has no automatic public release feed: future versions are applied by rerunning this installer. Third-party plugin updates use the IDE's plugin manager.

Settings are merged with unrelated XML components/options where possible. The profile's named layout replaces that layout, not every other saved layout. Launcher VM options are preserved while the selected heap maximum is updated. Global formatting and existing project workspace files are not silently changed.

## Caps Lock → Escape

The menu shows the detected state and the next action. On/off changes are independent of editor installs and preserve unrelated mappings. State is stored per desktop backend. Disabling a mapping enabled by this installer restores its previous Caps Lock setting. Removing an existing mapping not owned by this installer requires confirmation.

- GNOME: use the logged-in session's writable `org.gnome.desktop.input-sources` XKB options.
- KDE Plasma: update the `kxkbrc` Caps option and its reset flag, preserving the other options. Show configured status; logout/login may be required, and live activation is not claimed.
- macOS: use `hidutil`, with a per-user LaunchAgent to reapply at login. Status refers to this mapping mechanism, not every third-party/per-device remapper. Other remappers and newly attached keyboards may require separate attention.

Unsupported desktops, missing desktop-session tools, locked settings, or unrecognized mappings are reported rather than guessed. The macOS login helper preserves other current hidutil mappings instead of replaying a stale full keyboard map.

## Publication

Do not commit, push, or change GitHub About until the owner has tested and approved the result. The proposed About text is:

> My personal editor and terminal configs, tailored to my preferences.
