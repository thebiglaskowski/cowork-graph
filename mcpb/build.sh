#!/usr/bin/env bash
# Rebuild the Claude Desktop extension bundle (jl-graph.mcpb).
# The name is jl-graph, NOT cowork-graph: Claude Desktop reserves the
# 'cowork' prefix and silently drops colliding extensions before the
# remote-devices bridge. If the install ID ever changes, the ABSOLUTE
# path inside manifest.json's mcp_config args must be updated to match
# (the artifact-host connect path does not substitute ${__dirname}).
set -euo pipefail
cd "$(dirname "$0")"
npm ci --omit=dev
npx -y @anthropic-ai/mcpb validate manifest.json
npx -y @anthropic-ai/mcpb pack . jl-graph.mcpb
echo "Built jl-graph.mcpb — drag it onto Claude Desktop > Settings > Extensions."
