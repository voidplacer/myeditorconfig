#!/usr/bin/env bash
set -euo pipefail

local_mode=0
if [[ ${1:-} == --local ]]; then
  local_mode=1
  shift
fi
inspect_mode=0
restore_mode=0
apply_mode=0
for argument in "$@"; do
  case $argument in
    --list|--dry-run|--help|-h) inspect_mode=1 ;;
    --restore) restore_mode=1 ;;
    --apply) apply_mode=1 ;;
  esac
done
if [[ $restore_mode == 1 && $apply_mode == 0 ]]; then inspect_mode=1; fi

work=$(mktemp -d)
bootstrap_root=
trap 'rm -rf "$work"' EXIT
system=$(uname -s)

fetch() {
  curl --proto '=https' --tlsv1.2 --fail --location --show-error --silent "$1" -o "$2"
  [[ -s "$2" ]] || { printf 'Empty download: %s\n' "$1" >&2; exit 1; }
}

bootstrap_backup() {
  local target=$1 parent base
  [[ ! -L "$target" ]] || { printf 'Refusing symlink: %s\n' "$target" >&2; exit 1; }
  parent=$(dirname "$target")
  [[ ! -L "$parent" ]] || { printf 'Refusing symlink directory: %s\n' "$parent" >&2; exit 1; }
  base=$HOME
  if [[ -d "$HOME/Documents" && -w "$HOME/Documents" && ! -L "$HOME/Documents" ]]; then base=$HOME/Documents; fi
  [[ ! -L "$base/editor_backup" ]] || { printf 'Refusing a symlink backup directory.\n' >&2; exit 1; }
  if [[ -z $bootstrap_root ]]; then
    bootstrap_root="$base/editor_backup/$(date -u +%Y%m%dT%H%M%SZ)-bootstrap-$$"
  fi
  (umask 077; mkdir -p "$bootstrap_root/homebrew")
  chmod 700 "$base/editor_backup" "$bootstrap_root" "$bootstrap_root/homebrew"
  if [[ -f "$target" ]]; then
    cp -p "$target" "$bootstrap_root/homebrew/$(basename "$target")"
  else
    printf 'Originally absent: %s\n' "$target" > "$bootstrap_root/homebrew/$(basename "$target").absent"
  fi
  printf 'Homebrew profile backup: %s\n' "$bootstrap_root"
}

if [[ $inspect_mode == 0 ]]; then
  command -v curl >/dev/null || { printf 'curl is required to start this installer. Install it with your package manager.\n' >&2; exit 1; }
  [[ ${EUID:-$(id -u)} != 0 ]] || { printf 'Run as your normal user, not root.\n' >&2; exit 1; }
  [[ $HOME == /* && $HOME != / && ! -L $HOME ]] || { printf 'Unsupported home-directory path.\n' >&2; exit 1; }
  if [[ $system == Darwin ]]; then
    major=$(sw_vers -productVersion | cut -d. -f1)
    [[ $major -ge 26 ]] || { printf 'Unsupported: macOS 26 or newer is required.\n' >&2; exit 1; }
    brew_path=$(command -v brew || true)
    if [[ -z $brew_path ]]; then
      for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [[ -x $candidate ]]; then brew_path=$candidate; break; fi
      done
    fi
    if [[ -z $brew_path ]]; then
      printf 'Setting up Homebrew using its official installer.\n'
      fetch https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh "$work/homebrew.sh"
      /bin/bash "$work/homebrew.sh" </dev/tty
      if [[ -x /opt/homebrew/bin/brew ]]; then brew_path=/opt/homebrew/bin/brew; else brew_path=/usr/local/bin/brew; fi
    fi
    [[ -x $brew_path ]] || { printf 'Homebrew setup failed.\n' >&2; exit 1; }
    eval "$("$brew_path" shellenv)"
    brew --prefix >/dev/null
    case ${SHELL:-/bin/zsh} in
      */zsh) profile=$HOME/.zprofile ;;
      */bash) profile=$HOME/.bash_profile ;;
      *) printf 'Unsupported shell for automatic Homebrew environment setup: %s\n' "${SHELL:-unknown}" >&2; exit 1 ;;
    esac
    line="eval \"\$($brew_path shellenv)\""
    if [[ ! -f $profile ]] || ! grep -Fqx "$line" "$profile"; then
      bootstrap_backup "$profile"
      printf '\n%s\n' "$line" >> "$profile"
      printf 'Homebrew environment configured. Open a new terminal to use it outside this installer.\n'
    fi
    if ! command -v python3 >/dev/null || ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3,12))' 2>/dev/null; then
      printf 'Installing binary Python prerequisite through Homebrew.\n'
      export HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
      unset HOMEBREW_NO_INSTALL_UPGRADE
      brew fetch --force-bottle --deps python
      brew deps --formula --recursive --topological python > "$work/python-dependencies"
      while IFS= read -r formula; do
        [[ -z $formula ]] || brew install --formula --force-bottle --ignore-dependencies "$formula"
      done < "$work/python-dependencies"
      brew install --formula --force-bottle --ignore-dependencies python
      hash -r
    fi
  elif [[ $system == Linux ]]; then
    . /etc/os-release
    case ${ID:-} in
      ubuntu) [[ ${VERSION_ID%%.*} -ge 24 ]] || { printf 'Unsupported: Ubuntu 24.04 or newer is required.\n' >&2; exit 1; } ;;
      fedora|arch) ;;
      *) printf 'Unsupported distribution: %s\n' "${ID:-unknown}" >&2; exit 1 ;;
    esac
    if ! command -v python3 >/dev/null || ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3,12))' 2>/dev/null; then
      . /etc/os-release
      printf 'Installing Python prerequisite using the distro package manager.\n'
      case ${ID:-} in
        ubuntu) sudo apt-get update --error-on=any; sudo apt-get install -y python3 ;;
        fedora) sudo dnf install -y python3 ;;
        arch) printf 'Arch requires a full upgrade; review the transaction below.\n'; sudo pacman -Syu --needed python ;;
        *) printf 'Unsupported distribution: %s\n' "${ID:-unknown}" >&2; exit 1 ;;
      esac
    fi
  else
    printf 'Unsupported OS: %s\n' "$system" >&2
    exit 1
  fi
fi

python3 -c 'import sys; raise SystemExit(sys.version_info < (3,12))' || { printf 'Python 3.12+ is required.\n' >&2; exit 1; }
if [[ $local_mode == 1 ]]; then
  root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  python3 "$root/scripts/install.py" "$@"
else
  fetch https://api.github.com/repos/voidplacer/myeditorconfig/commits/main "$work/commit.json"
  ref=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$work/commit.json")
  [[ $ref =~ ^[a-f0-9]{40}$ ]] || { printf 'Invalid repository commit.\n' >&2; exit 1; }
  mkdir -p "$work/scripts/editor_setup"
  for name in install.py editor_setup/__init__.py editor_setup/common.py editor_setup/remote.py editor_setup/packages.py editor_setup/config.py editor_setup/keyboard.py editor_setup/menu.py; do
    fetch "https://raw.githubusercontent.com/voidplacer/myeditorconfig/$ref/scripts/$name" "$work/scripts/$name"
  done
  if [[ -t 0 || $inspect_mode == 1 ]]; then
    python3 "$work/scripts/install.py" --remote voidplacer/myeditorconfig --ref "$ref" "$@"
  else
    python3 "$work/scripts/install.py" --remote voidplacer/myeditorconfig --ref "$ref" "$@" </dev/tty
  fi
fi
