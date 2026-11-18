#!/usr/bin/env bash
# AutoApply Local installer for macOS (also works on other POSIX systems).
#   curl -fsSL https://raw.githubusercontent.com/tlamDson/autoapply-local/main/install.sh | bash
#   or, inside a checkout: ./install.sh [--no-setup]
# It never installs system software: a missing dependency prints its install command.
set -euo pipefail

REPO_URL="https://github.com/tlamDson/autoapply-local.git"

hint() {
  # Printed install commands only; this script runs none of them.
  if [ "$(uname -s)" = "Darwin" ]; then
    if ! command -v brew >/dev/null 2>&1; then
      echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    fi
    if [ "$1" = "python" ]; then
      echo "  brew install python@3.12"
    else
      echo "  brew install git"
    fi
  else
    echo "  Install $1 with your package manager"
  fi
  echo "Then open a new terminal and rerun the installer."
}

is_checkout() {
  [ -f "$1/autoapply_local/__init__.py" ] && [ -f "$1/pyproject.toml" ] && [ -f "$1/scripts/install.py" ]
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

main() {
  local here="" repo="" py=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -n "$here" ] && is_checkout "$here"; then
    repo="$here"
  elif is_checkout "$PWD"; then
    repo="$PWD"
  elif is_checkout "$PWD/autoapply-local"; then
    repo="$PWD/autoapply-local"  # rerun of the one-line install: reuse the earlier clone
  else
    if ! command -v git >/dev/null 2>&1; then
      echo "Missing dependency (nothing was installed): git"
      hint git
      return 3
    fi
    if [ -e "$PWD/autoapply-local" ]; then
      echo "$PWD/autoapply-local exists but is not an AutoApply Local checkout; move it and rerun."
      return 2
    fi
    git clone "$REPO_URL" "$PWD/autoapply-local"
    repo="$PWD/autoapply-local"
  fi
  if ! py="$(find_python)"; then
    echo "Missing dependency (nothing was installed): Python 3.11+"
    hint python
    return 3
  fi
  # `curl | bash` feeds this script on stdin; give interactive setup the terminal.
  if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
    "$py" "$repo/scripts/install.py" "$@" </dev/tty
  else
    "$py" "$repo/scripts/install.py" "$@"
  fi
}

main "$@"
