#!/usr/bin/env python3
"""The Health Assistant MCP server (stdio transport).

Run from the repository root (the project venv is installed by uv):

    uv run python mcp-server/server.py

Point any MCP client at it, e.g. OpenCode / Claude-style clients:

    {"command": "uv", "args": ["run", "python", "mcp-server/server.py"],
     "cwd": "<repository root>"}

Tools exposed: ask_health(question, session_id?) and semantic_layer_surface().
Protocol logic lives in assistant.mcp (tested by the backend suite); this file is the
process entry point.
"""

from assistant.mcp import serve

if __name__ == "__main__":
    raise SystemExit(serve())