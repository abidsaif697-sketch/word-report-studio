#!/bin/bash
set -euo pipefail

# Only needed in Claude Code cloud sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Project dependencies
pip install -q -r word_report_studio/requirements.txt --break-system-packages \
  || pip install -q -r word_report_studio/requirements.txt

# graphify CLI (used by the PreToolUse hooks and CLAUDE.md rules)
if ! command -v graphify >/dev/null 2>&1; then
  uv tool install graphifyy || pipx install graphifyy
fi
# markitdown MCP server (registered in .mcp.json)
if ! command -v markitdown-mcp >/dev/null 2>&1; then
  uv tool install markitdown-mcp || pipx install markitdown-mcp
fi
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$CLAUDE_ENV_FILE"
