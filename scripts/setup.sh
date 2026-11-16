#!/usr/bin/env sh
# One-time setup on macOS / Linux. Usage: scripts/setup.sh [--no-models]
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python and package manager, user-local)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

if [ "$(uname -s)" = "Linux" ] && ! (ldconfig -p 2>/dev/null | grep -q libportaudio); then
  echo "Note: recording needs PortAudio. On Debian/Ubuntu: sudo apt install libportaudio2"
fi

chmod +x "$ROOT/vc" 2>/dev/null || true
uv sync
uv run vc init
if [ "${1:-}" != "--no-models" ]; then
  uv run vc models download
fi
uv run vc doctor || true
echo
echo "Done. Try: ./vc record start --name test   then   ./vc record stop --analyze"
