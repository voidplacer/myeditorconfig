# Fonts

Each immediate subfolder is one font family. The installer discovers these folders instead of maintaining a hardcoded font menu.

`caskaydiamono/font.json` describes an official Nerd Fonts download for Linux and a Homebrew cask for macOS. The font binaries are intentionally not stored here.

Adding families:

- For an upstream font, add a folder containing `font.json`, following the existing definition. Linux downloads must identify an official GitHub release asset; macOS uses a Homebrew cask.
- For a bundled font, add `.ttf` or `.otf` files and the redistribution license to a family folder. Linux can install these per user. A matching Homebrew cask is still required for macOS, otherwise that family will be marked unsupported there.

Only the selected family will be downloaded or installed. Selecting a font installs it; it does not rewrite the font preferences in every application's config.
