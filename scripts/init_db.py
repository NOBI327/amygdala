#!/usr/bin/env python3
"""DB初期化スクリプト。単独実行可能。"""
import sys
import argparse
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.db import DatabaseManager


def main():
    parser = argparse.ArgumentParser(description="簡易扁桃体模倣型LLMメモリDBを初期化する")
    parser.add_argument("--db-path", default=None, help="DBファイルパス（省略時: Config.DB_PATH）")
    parser.add_argument("--reset", action="store_true", help="既存DBを削除して再作成")
    args = parser.parse_args()

    config = Config.from_env()
    db_path = args.db_path or config.DB_PATH

    if args.reset and Path(db_path).exists() and db_path != ":memory:":
        Path(db_path).unlink()
        print(f"Deleted existing DB: {db_path}")

    with DatabaseManager(db_path) as db:
        print(f"Database initialized: {db_path}")


if __name__ == "__main__":
    main()
