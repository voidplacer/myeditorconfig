# Config notes

## Layout

Each directory under `configs/` belongs to an application or a shared concern. The Neovim Lua files use their destination layout (`lua/config/` and `lua/plugins/`). These are overlays for a LazyVim installation, not a complete standalone Neovim distribution.

| Directory | Purpose |
| --- | --- |
| `alacritty/` | Alacritty terminal preferences using the platform's default shell |
| `clangd/` | clangd compiler flags |
| `formatting/` | Shared EditorConfig and LLVM-style clang-format defaults |
| `formatting-allman/` | Optional original Allman clang-format preference, independent of editor installation |
| `ideavim/` | Original standalone IdeaVim preferences |
| `jetbrains/` | Newer CLion/GoLand coding profile and its IdeaVim preferences |
| `kitty/` | kitty terminal preferences |
| `nvim/` | Personal LazyVim overlays; optional extras are selected separately and global formatting is unchanged |
| `tmux/` | tmux preferences |
| `vscode/` | VS Code settings and keybindings, including JSON with comments |
| `zed/` | Newer local Zed profile, replacing the previous GitHub copy |
| `shell/` | Shared Bash/Zsh functions and aliases, appended in a managed block; includes Linux/Arch-specific aliases |

Formatting preferences are separate from editor installation. The original Neovim Allman preference is offered as its own optional choice; the shared profile uses LLVM defaults. Selecting one style automatically unchecks the other in the menu without unchecking Neovim. If neither style is selected, the existing home-level `.clang-format` is untouched. Applying a style backs up an existing file first; more-specific project `.clang-format` files are not changed.

The root-level `windows/` directory preserves the original `EscVimBinds.ahk` and Windows/WSL Alacritty config for manual use only. It is separate from `configs/` and is never selected, downloaded, or applied by the Linux/macOS installer.

The original Neovim notes requested `lang.clangd` and `lang.java` extras. They are now optional nested selections under Neovim, defined in `catalog.json`, rather than automatically enabled by a text file. Selecting only Neovim enables neither extra; existing user extras remain intact. clang-format remains a base dependency for the personal formatter overlay.

## JetBrains

The snapshots under `configs/jetbrains/CLion2026.2/` and `GoLand2026.2/` came from the local portable coding profile. They contain keymaps, appearance settings, editor preferences, tool-window layouts, and sanitized VM options. They are reference profiles, not entire IDE config-directory backups.

The profile uses Catppuccin Mocha, CaskaydiaMono Nerd Font, IdeaVim, and custom pane-navigation actions. The custom plugin source and its manifest are in `plugins/jetbrains/coding-profile/`; its manifest currently requires JetBrains platform build 262 or newer. Compatibility with other IDE versions must be checked before applying the profile.

The VM options set a maximum Java heap of `3840m`. The installer must merge this preference rather than replace an existing VM-options file, which may contain application-launcher settings.

`project-format.xml` describes project-specific formatting preferences. It is not a replacement for an existing project's `.idea/workspace.xml`. Applying home-level files or modifying project settings requires an explicit selection or confirmation.

The standalone `ideavim/` profile and the JetBrains-specific `.ideavimrc` differ. The installer must not apply both in an undefined order.

The old local Linux-only installer, machine backups, caches, loose compiled class files, downloaded third-party plugins, and full profile archive are not included. The custom plugin's small prebuilt JAR is encoded in `plugins/jetbrains/coding-profile/release.json` with its checksum and source hashes, so clients do not need to compile it. The new installer handles CLion and GoLand independently.

## Privacy

Only intentionally selected config files and custom plugin source are included. Do not add complete IDE settings directories, recent-project lists, account or license files, SSH keys, tokens, logs, caches, or backups.

Before publication, review both the final file inventory and its diff, including hidden files. Automated pattern scans are an additional check, not proof that all private information has been removed.
