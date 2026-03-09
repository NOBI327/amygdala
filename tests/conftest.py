"""テスト共通設定。

mcp パッケージが未インストールの環境でも test_mcp_server.py を
実行できるよう、mcp モジュールをモックする。
"""
import sys
from unittest.mock import MagicMock

# mcp パッケージのスタブを sys.modules に注入
if "mcp" not in sys.modules:
    mcp_mock = MagicMock()
    sys.modules["mcp"] = mcp_mock
    sys.modules["mcp.server"] = mcp_mock.server
    sys.modules["mcp.server.fastmcp"] = mcp_mock.server.fastmcp
    # FastMCP().tool() がデコレータとして機能するようにする
    mcp_mock.server.fastmcp.FastMCP.return_value.tool.return_value = lambda fn: fn
