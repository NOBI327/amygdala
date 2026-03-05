# 感情基盤メモリシステム Phase 1 MVP

感情10軸でタグ付けしてSQLiteに保存し、感情ベース検索で記憶を取得するLLMメモリシステム。
Dual-Agent構造（バックマン + フロントマン）により、感情・場面・時間の多軸検索を実現。

## アーキテクチャ概要

### Dual-Agent構造

- **バックマン**: 入力の感情＋場面タグ付け、出力のサマリ＋タグ付け
- **フロントマン**: 検索結果を踏まえたプロンプト組立と応答生成

### 2階層メモリ + ピンメモリ

| メモリ種別 | 役割 |
|-----------|------|
| ワーキングメモリ | 直近N턴の会話コンテキスト（デフォルト10ターン） |
| 長期記憶 | ワーキングメモリから移管された過去会話（感情×場面×時間で検索） |
| ピンメモリ | 「覚えといて」等の明示的指示で登録した固定記憶 |

### DIパターン設計

全コンポーネントは依存注入（DI）で接続。テスト時にモックを注入可能。

### 処理フロー

```
ユーザー入力
    │
    ▼
バックマン: 感情+場面タグ付け
    │
    ▼
SearchEngine: 長期記憶検索（感情×場面×時間）
    │
    ▼
フロントマン: プロンプト組立 + 応答生成
    │
    ▼
ワーキングメモリ更新 → 10ターン超過で長期記憶に移管
```

## ディレクトリ構成

```
emotion-gravity-field-proposal/
├── src/
│   ├── __init__.py
│   ├── config.py          # 設定値（DI用コンテナ）
│   ├── db.py              # DatabaseManager（DI設計）
│   ├── backman.py         # BackmanService（llm_client注入）
│   ├── working_memory.py  # WorkingMemory（db_manager注入）
│   ├── pin_memory.py      # PinMemory（db_manager注入）
│   ├── search_engine.py   # SearchEngine（db_manager注入）
│   ├── frontman.py        # FrontmanService（llm_client注入）
│   └── memory_system.py   # MemorySystem（オーケストレーター）
├── scripts/
│   ├── init_db.py         # DB初期化スクリプト
│   └── demo.py            # インタラクティブデモ
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_backman.py
│   ├── test_working_memory.py
│   ├── test_pin_memory.py
│   ├── test_search_engine.py
│   ├── test_frontman.py
│   └── test_memory_system.py
├── docs/
│   └── emotion-memory-system-proposal-v0.4.md
└── requirements.txt
```

## セットアップ

```bash
cd /mnt/c/claude_pj/emotion-gravity-field-proposal
pip install -r requirements.txt
python scripts/init_db.py  # DB初期化
export ANTHROPIC_API_KEY="your-key-here"
```

## 環境変数

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| ANTHROPIC_API_KEY | (必須) | Anthropic APIキー |
| EMS_BACKMAN_MODEL | claude-haiku-4-5-20251001 | バックマンモデル |
| EMS_FRONTMAN_MODEL | claude-haiku-4-5-20251001 | フロントマンモデル |
| EMS_DB_PATH | memory.db | SQLiteDBファイルパス |

## バックマンのコスト見積もり

| 項目 | 値 |
|------|-----|
| モデル | claude-haiku-4-5-20251001（Claude Haiku 4.5） |
| 推定トークン/call | 約500（入力~350 + 出力~150） |
| 1ターンあたり呼び出し | 2回（入力タグ付け + 出力サマリ/タグ付け） |
| 1ターンあたり推定コスト | 約1000トークン ≈ $0.001未満 |
| モデル切替 | EMS_BACKMAN_MODEL環境変数で変更可能 |

## デモ実行方法

```bash
# インタラクティブデモ
python scripts/demo.py
```

実行例:

```
You: これを覚えといて: 誕生日は3月15日

# 記憶参照例（明示的言及）
You: さっきの誕生日の件だけど...
```

## テスト実行方法

```bash
# 全テスト + カバレッジ
python -m pytest tests/ -v --cov=src --cov-report=term-missing

# Core層カバレッジ確認（80%以上必須）
python -m pytest tests/ --cov=src --cov-fail-under=80

# 個別テスト
python -m pytest tests/test_db.py -v
python -m pytest tests/test_backman.py -v
python -m pytest tests/test_working_memory.py -v
python -m pytest tests/test_pin_memory.py -v
python -m pytest tests/test_search_engine.py -v
python -m pytest tests/test_frontman.py -v
python -m pytest tests/test_memory_system.py -v
```

## Phase 2以降のロードマップ

- **Phase 2**: フィードバックループ、多様性モニタリング、暗黙的フィードバック判定
- **Phase 3**: バックマンをsLMに置換、ベンチマーク、ペルソナダイヤル
