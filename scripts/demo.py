#!/usr/bin/env python3
"""簡易扁桃体模倣型LLMメモリ拡張システム Phase 1 MVP インタラクティブデモ"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from anthropic import Anthropic
from src.config import Config
from src.db import DatabaseManager
from src.memory_system import MemorySystem


def main():
    logging.basicConfig(level=logging.WARNING)
    config = Config.from_env()
    print("=" * 60)
    print("簡易扁桃体模倣型LLMメモリ拡張システム Phase 1 MVP")
    print(f"バックマン: {config.BACKMAN_MODEL}")
    print(f"フロントマン: {config.FRONTMAN_MODEL}")
    print(f"ワーキングメモリ: {config.WORKING_MEMORY_TURNS}ターン")
    print(f"推定トークン/ターン: {config.BACKMAN_ESTIMATED_TOKENS_PER_CALL * 2}")
    print("終了: 'quit' または Ctrl+C")
    print("=" * 60)

    client = Anthropic()
    db = DatabaseManager(config.DB_PATH)
    db.init()

    with MemorySystem(client, db, config) as system:
        while True:
            try:
                user_input = input("\nYou: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("quit", "exit", "q"):
                    break
                response = system.process_turn(user_input)
                print(f"\nAI: {response}")
            except KeyboardInterrupt:
                print("\n終了します。")
                break
            except Exception as e:
                print(f"\nエラー: {e}")


if __name__ == "__main__":
    main()
