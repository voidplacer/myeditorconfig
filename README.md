# myeditorconfig

My personal editor and terminal configs, tailored to my preferences. Shared for reference, not as universal defaults.

## Try locally

```bash
bash install.sh --local --select kitty,tmux --dry-run
bash install.sh --local
```

Browse the inline tree with `j`/`k` or arrows. Space/Enter expands or collapses folders and checks items; Right opens a branch, and `h`/Left collapses its parent. Use `1–9` to activate numbered rows. Checks persist in collapsed folders. Review them, then activate **Install selected**. Existing user configs are backed up to `Documents/editor_backup/`, or `~/editor_backup/`.

## Install

Run the selector:

```bash
bash -o pipefail -c 'curl -fsSL https://raw.githubusercontent.com/voidplacer/myeditorconfig/main/install.sh | bash'
```

Only installer/menu metadata and selected config files are fetched—not a repo clone. First-run prerequisites may also be installed. Targets: Ubuntu 24.04+, Fedora, Arch, and macOS 26+; unavailable or incompatible setups are reported.

See [installer details](docs/installer.md), [config notes](docs/configs.md), and [fonts](fonts/README.md).
