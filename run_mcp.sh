#!/usr/bin/env bash
cd /mnt/c/claude_pj/amygdala
exec .venv/bin/python -m src.mcp_server "$@"
