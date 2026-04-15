#!/usr/bin/env bash
# Build a macOS .pkg installer for claude-sessions.
#
# Usage:  ./scripts/build-pkg.sh
# Output: dist/claude-sessions-<version>.pkg
#
# The .pkg installs a standalone Python venv to /usr/local/lib/claude-sessions
# and symlinks the CLI to /usr/local/bin/claude-sessions.

set -euo pipefail

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
IDENTIFIER="io.celestialtech.claude-sessions"

echo "Building claude-sessions v${VERSION} .pkg"

# ── Clean ───────────────────────────────────────────────────────────────────
rm -rf dist/pkg-root dist/pkg-scripts dist/*.pkg

# ── Build wheel ─────────────────────────────────────────────────────────────
uv build --wheel --out-dir dist/wheels

# ── Create payload directory ────────────────────────────────────────────────
INSTALL_ROOT="dist/pkg-root/usr/local/lib/claude-sessions"
mkdir -p "$INSTALL_ROOT"
uv venv "$INSTALL_ROOT/venv"
uv pip install --python "$INSTALL_ROOT/venv/bin/python" dist/wheels/claude_sessions-*.whl

# Symlink into /usr/local/bin
mkdir -p dist/pkg-root/usr/local/bin
ln -sf ../lib/claude-sessions/venv/bin/claude-sessions dist/pkg-root/usr/local/bin/claude-sessions

# ── Post-install script ────────────────────────────────────────────────────
mkdir -p dist/pkg-scripts
cat > dist/pkg-scripts/postinstall << 'POSTINSTALL'
#!/bin/bash
# Fix venv paths after install (they were built relative to dist/pkg-root)
VENV_DIR="/usr/local/lib/claude-sessions/venv"
# Rewrite the shebang in the entrypoint
sed -i '' "1s|.*|#!${VENV_DIR}/bin/python3|" "${VENV_DIR}/bin/claude-sessions" 2>/dev/null || true
echo "claude-sessions installed. Run: claude-sessions sync"
POSTINSTALL
chmod +x dist/pkg-scripts/postinstall

# ── Build .pkg ──────────────────────────────────────────────────────────────
pkgbuild \
    --root dist/pkg-root \
    --scripts dist/pkg-scripts \
    --identifier "$IDENTIFIER" \
    --version "$VERSION" \
    --install-location / \
    "dist/claude-sessions-${VERSION}.pkg"

echo ""
echo "Built: dist/claude-sessions-${VERSION}.pkg"
echo "Install: sudo installer -pkg dist/claude-sessions-${VERSION}.pkg -target /"
