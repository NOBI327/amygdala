#!/bin/bash
# recall_log を CSV エクスポートしてラベリングツールを起動する

DB_PATH="${1:-amygdala.db}"

if [ ! -f "$DB_PATH" ]; then
    echo "エラー: $DB_PATH が見つかりません"
    echo "使い方: ./run_labeling.sh [DBパス]"
    exit 1
fi

echo "=== recall_log エクスポート ==="
sqlite3 -header -csv "$DB_PATH" "SELECT * FROM recall_log ORDER BY recalled_at" > recall_log.csv

COUNT=$(wc -l < recall_log.csv)
echo "  ${COUNT} 行エクスポート (ヘッダー含む)"

if [ "$COUNT" -le 1 ]; then
    echo "  recall_log が空です。テストを実行してからもう一度。"
    exit 1
fi

echo ""
echo "=== ラベリング開始 ==="
python3 label_tool.py recall_log.csv "$DB_PATH"
