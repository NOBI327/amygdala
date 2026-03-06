#!/usr/bin/env python3
"""
recall_log テーブルを CSV にエクスポートするユーティリティ

usage:
    python scripts/export_recall_log.py [db_path] [output_csv]

    db_path    : SQLite DB ファイルパス (デフォルト: memory.db)
    output_csv : 出力先 CSV ファイルパス (デフォルト: recall_log.csv)
"""

import csv
import sqlite3
import sys
from pathlib import Path


def export_recall_log(db_path: str, output_csv: str) -> int:
    """
    recall_log テーブルの全データを CSV に書き出す。

    Returns:
        エクスポートした行数
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recall_log ORDER BY recalled_at")
        rows = cur.fetchall()

        if not rows:
            print(f"recall_log は空です: {db_path}")
            return 0

        fieldnames = list(rows[0].keys())
        with open(output_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows([dict(row) for row in rows])

        return len(rows)
    finally:
        conn.close()


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    db_path = sys.argv[1] if len(sys.argv) > 1 else "memory.db"
    output_csv = sys.argv[2] if len(sys.argv) > 2 else "recall_log.csv"

    if not Path(db_path).exists():
        print(f"エラー: DB ファイルが見つかりません: {db_path}")
        sys.exit(1)

    count = export_recall_log(db_path, output_csv)
    if count > 0:
        print(f"エクスポート完了: {count} 件 → {output_csv}")


if __name__ == "__main__":
    main()
