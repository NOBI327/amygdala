# 感情タグ付き関係性グラフ — 実装設計書

> 提案書: `amygdala-relational-graph-proposal.md` の実装仕様。
> 対象: amygdala 既存コードベース（Phase 4完了時点）

---

## 1. スコープと前提

### 1.1 この文書がカバーする範囲

提案書 Step 1〜4（データ構造、エンティティ抽出、タグパイプライン、減衰処理）の実装仕様。
Step 5〜6（Backman/Frontman統合）は本フェーズでは **統合ポイントの設計のみ** とし、実装は次フェーズで行う。

### 1.2 設計判断の根拠

| 判断 | 理由 |
|------|------|
| 新モジュール `src/relational_graph.py` として分離 | Backmanの責務肥大を防ぐ。将来の独立エージェント化への布石 |
| SQLiteに3テーブル追加（グラフDB不使用） | ノード上限100、エッジ上限2000の規模でグラフDBは過剰。既存DBManagerのパターンを踏襲 |
| エンティティ抽出をLLM依存にする | ルールベースでは `person` と `topic` の境界が曖昧。Backmanの `tag_emotion` と同一呼び出しに相乗りさせてAPI呼び出し回数を増やさない |
| 同一性判定は `label` の正規化 + LLMアシストで段階的に対処 | 完全な固有表現解決（coreference resolution）は過剰。まず動かして精度を測る |

---

## 2. データモデル

### 2.1 DB スキーマ（`db.py` に追加）

```sql
-- ノード: 会話中のエンティティ
CREATE TABLE IF NOT EXISTS graph_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,                    -- 正規化済み表示名
    type TEXT NOT NULL CHECK(type IN ('person','topic','item','place','event')),
    aliases TEXT DEFAULT '[]',             -- JSON array: 同一エンティティの別名群
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    mention_count INTEGER DEFAULT 1,
    -- 基底感情ベクトル（10軸）
    joy REAL DEFAULT 0, sadness REAL DEFAULT 0,
    anger REAL DEFAULT 0, fear REAL DEFAULT 0,
    surprise REAL DEFAULT 0, disgust REAL DEFAULT 0,
    trust REAL DEFAULT 0, anticipation REAL DEFAULT 0,
    importance REAL DEFAULT 0, urgency REAL DEFAULT 0,
    archived BOOLEAN DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_label ON graph_nodes(label);

-- エッジ: ノード間の関係性
CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES graph_nodes(id),
    target_id INTEGER NOT NULL REFERENCES graph_nodes(id),
    strength REAL DEFAULT 1.0,              -- 接続強度（減衰対象）
    confidence REAL DEFAULT 0.5,            -- 推論信頼度
    last_activated DATETIME DEFAULT CURRENT_TIMESTAMP,
    activation_count INTEGER DEFAULT 1,
    -- 関係性の感情ベクトル（10軸）
    joy REAL DEFAULT 0, sadness REAL DEFAULT 0,
    anger REAL DEFAULT 0, fear REAL DEFAULT 0,
    surprise REAL DEFAULT 0, disgust REAL DEFAULT 0,
    trust REAL DEFAULT 0, anticipation REAL DEFAULT 0,
    importance REAL DEFAULT 0, urgency REAL DEFAULT 0,
    archived BOOLEAN DEFAULT FALSE,
    UNIQUE(source_id, target_id)
);

-- タグ: エッジ上の関係性ラベル
CREATE TABLE IF NOT EXISTS graph_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id INTEGER NOT NULL REFERENCES graph_edges(id),
    label TEXT NOT NULL,
    strength REAL DEFAULT 0.5,              -- 候補状態では0.5、昇格時に1.0
    activation_count INTEGER DEFAULT 1,
    decay_rate REAL DEFAULT 0.05,           -- 日あたり減衰率
    confirmed BOOLEAN DEFAULT FALSE,        -- 昇格済みフラグ
    created DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_activated DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(edge_id, label)
);
```

**設計メモ**:
- `graph_nodes.aliases` はJSON配列。「上司」「佐藤部長」「部長」を同一ノードに紐付けるための簡易同一性解決。正規化は `label` カラムで行い、`aliases` は検索時のマッチングに使う。
- `graph_edges` に `UNIQUE(source_id, target_id)` を設ける。同一ペアの重複エッジは許可しない（代わりに `activation_count` をインクリメント）。
- `graph_tags.confirmed` を追加（提案書では `activation_count ≧ 3` で暗黙的に昇格だが、明示フラグの方がクエリが単純）。

### 2.2 Python データ構造

```python
# relational_graph.py 内で使用する型（TypedDict）

from typing import TypedDict, List, Optional

class GraphNode(TypedDict):
    id: int
    label: str
    type: str            # person | topic | item | place | event
    aliases: List[str]
    mention_count: int
    base_emotion: dict   # 10軸感情ベクトル

class GraphEdge(TypedDict):
    id: int
    source_id: int
    target_id: int
    tags: List["GraphTag"]
    emotion_vector: dict  # 10軸
    strength: float
    confidence: float
    activation_count: int

class GraphTag(TypedDict):
    id: int
    label: str
    strength: float
    activation_count: int
    confirmed: bool
    decay_rate: float

class EntityContext(TypedDict):
    """Frontmanに渡すサマリー形式"""
    entity: str
    primary_emotion: dict    # dominant emotion axes
    active_tags: List[str]   # confirmed tags only
    related_entities: List[str]
    confidence: float
```

---

## 3. モジュール設計

### 3.1 `src/relational_graph.py` — RelationalGraphEngine

```
RelationalGraphEngine
├── __init__(config, db_manager, llm_adapter=None)
│
├── # ノード操作
├── upsert_node(label, type, emotion_vec, aliases=[]) → GraphNode
├── find_node(label_or_alias) → Optional[GraphNode]
├── _normalize_label(raw_label) → str
├── _merge_base_emotion(existing_vec, new_vec, count) → dict
│
├── # エッジ操作
├── upsert_edge(source_label, target_label, emotion_vec, tag_labels=[]) → GraphEdge
├── get_edges(node_id, include_archived=False) → List[GraphEdge]
│
├── # タグ操作
├── upsert_tag(edge_id, label, decay_category="situational") → GraphTag
├── confirm_tag(tag_id) → None
├── _auto_confirm_tags() → int  # activation_count >= 閾値のものを一括昇格
│
├── # 減衰処理
├── apply_decay() → dict  # {"decayed_tags": int, "removed_tags": int, "archived_edges": int}
├── _compute_tag_strength(tag) → float
│
├── # 検索
├── get_entity_context(label, hops=1) → EntityContext
├── search_by_tag(tag_label) → List[GraphEdge]
├── search_by_emotion(emotion_vec, top_k=5) → List[GraphNode]
│
├── # エンティティ抽出（LLM依存）
├── extract_entities(text, emotion_vec) → List[dict]
│   # Returns: [{"label": str, "type": str, "aliases": [], "relations": [{"target": str, "tags": []}]}]
│
├── # スコープ管理
├── _enforce_node_limit() → int  # soft-archive excess nodes
├── _enforce_edge_limit(node_id) → int
├── _enforce_tag_limit(edge_id) → int
│
├── # グラフ更新の統合エントリポイント
└── process_turn(text, emotion_vec) → dict
    # extract → upsert nodes/edges/tags → auto_confirm → apply_decay → enforce_limits
```

### 3.2 エンティティ抽出プロンプト

`tag_emotion` への相乗りを検討したが、以下の理由で **別プロンプト** とする：
- `tag_emotion` のレスポンスフォーマットを変更すると既存テスト147件に影響
- エンティティ抽出はオプショナル機能（LLMなしでも他の機能は動く）

```python
ENTITY_EXTRACTION_PROMPT = """以下の対話テキストから、登場するエンティティ（人物・話題・アイテム・場所・出来事）を抽出せよ。

ルール:
- 各エンティティに type を付与: person | topic | item | place | event
- エンティティ間の関係性がある場合、relations で記述
- 関係性にはタグ（関係の性質を表す短いラベル）を付与
- 代名詞（「彼」「それ」等）は可能な限り具体名に解決
- 最大5エンティティまで（重要度順）

出力形式（JSONのみ）:
{{
  "entities": [
    {{
      "label": "上司",
      "type": "person",
      "aliases": ["佐藤部長"],
      "relations": [
        {{"target": "プロジェクトA", "tags": ["担当者", "責任者"]}}
      ]
    }}
  ]
}}

対話テキスト:
{text}
"""
```

**LLM非可用時の挙動**: エンティティ抽出をスキップし、グラフ更新なしで正常復帰。既存機能に影響しない。

### 3.3 `process_turn` フロー

```
process_turn(text, emotion_vec)
│
├── 1. extract_entities(text, emotion_vec)
│   └── LLM呼び出し → エンティティリスト取得
│       └── LLM非可用時: return {"updated": False}
│
├── 2. 各エンティティについて:
│   ├── upsert_node(label, type, emotion_vec, aliases)
│   │   ├── 既存ノード: mention_count++, last_seen更新, base_emotion移動平均
│   │   └── 新規ノード: 作成
│   │
│   └── 各relationについて:
│       ├── upsert_edge(source, target, emotion_vec, tags)
│       │   ├── 既存エッジ: activation_count++, strength補強, emotion移動平均
│       │   └── 新規エッジ: 作成（confidence=0.5）
│       │
│       └── 各tagについて:
│           └── upsert_tag(edge_id, tag_label, decay_category)
│               ├── 既存タグ: activation_count++, strength補強
│               └── 新規タグ: confirmed=False で作成
│
├── 3. _auto_confirm_tags()
│   └── activation_count >= TAG_CANDIDATE_THRESHOLD のタグを confirmed=True に
│
├── 4. apply_decay()
│   ├── 全タグの strength を指数減衰で更新
│   ├── strength < TAG_STRENGTH_THRESHOLD のタグを削除
│   └── タグが全削除されたエッジを soft-archive
│
├── 5. _enforce_node_limit() → _enforce_edge_limit() → _enforce_tag_limit()
│   └── 上限超過分を strength × confidence 昇順で soft-archive
│
└── return {
      "updated": True,
      "nodes_affected": int,
      "edges_affected": int,
      "tags_confirmed": int,
      "tags_decayed": int
    }
```

---

## 4. 感情ベクトルの扱い

### 4.1 ノードの基底感情（移動平均）

ノードが複数回言及されるたびに、基底感情を指数移動平均で更新する。

```python
def _merge_base_emotion(self, existing_vec: dict, new_vec: dict, mention_count: int) -> dict:
    """指数移動平均で基底感情を更新"""
    alpha = 2.0 / (mention_count + 1)  # 新しいデータほど重みが大きい
    merged = {}
    for axis in EMOTION_AXES + META_AXES:
        old = existing_vec.get(axis, 0.0)
        new = new_vec.get(axis, 0.0)
        merged[axis] = old * (1 - alpha) + new * alpha
    return merged
```

**根拠**: 単純平均だと初期値の影響が消えない。指数移動平均なら直近の感情状態が優先される。

### 4.2 エッジの感情ベクトル

エッジの感情は「2エンティティが共起した時点の会話感情」を記録する。ノードと同じ移動平均で更新。

---

## 5. 減衰処理

### 5.1 タグ強度の計算

```python
import math
from datetime import datetime

def _compute_tag_strength(self, tag_row) -> float:
    days = (datetime.now() - tag_row["last_activated"]).total_seconds() / 86400
    return tag_row["strength"] * math.exp(-tag_row["decay_rate"] * days)
```

### 5.2 減衰率カテゴリの自動判定

LLMにタグのカテゴリ判定を任せるのは過剰。以下のヒューリスティクスで判定する。

| 条件 | カテゴリ | decay_rate |
|------|----------|-----------|
| 関連ノードの type が `person` で、タグが肯定的感情（trust, joy 優位） | stable | 0.01 |
| 関連ノードの type が `person` で、タグが否定的感情（anger, fear 優位） | situational | 0.05 |
| 関連ノードの type が `topic` / `event` | situational | 0.05 |
| 関連ノードの type が `item` / `place` | temporary | 0.10 |
| デフォルト | situational | 0.05 |

**注**: このヒューリスティクスは初期値。運用データで調整する。

### 5.3 `apply_decay` の実行タイミング

`process_turn()` 内で毎回実行する。ノード上限100の規模であれば性能問題は生じない。

---

## 6. 同一性解決（Entity Resolution）

### 6.1 段階的アプローチ

完全な共参照解決は Phase 1 では行わない。以下の3段階で段階的に精度を上げる。

**Phase 1（本実装）: ラベル正規化 + aliases**
```python
def _normalize_label(self, raw_label: str) -> str:
    """基本的な正規化"""
    label = raw_label.strip()
    # 敬称除去: 「さん」「くん」「氏」「先生」等
    for suffix in ["さん", "くん", "ちゃん", "氏", "先生", "様"]:
        if label.endswith(suffix) and len(label) > len(suffix):
            label = label[:-len(suffix)]
    return label
```

- LLM抽出時に `aliases` も返させる
- `find_node` 時に `label` と `aliases` の両方を検索

**Phase 2（将来）: LLMアシスト同一性判定**
- 新規エンティティ追加時に、既存ノードリストをLLMに提示して同一候補を判定

**Phase 3（将来）: 埋め込みベースのクラスタリング**
- エンティティ名のembedding類似度で自動マージ候補を生成

### 6.2 手動マージAPI

ユーザーが明示的にエンティティを統合できるAPIを提供する。

```python
def merge_nodes(self, keep_id: int, merge_id: int) -> GraphNode:
    """merge_id のノードを keep_id に統合"""
    # 1. merge_id の aliases を keep_id に追加
    # 2. merge_id に接続するエッジを keep_id に付け替え
    # 3. mention_count を合算
    # 4. base_emotion を加重平均で統合
    # 5. merge_id を soft-archive
```

---

## 7. スコープ管理

### 7.1 制約値（`config.py` に追加）

```python
# relational graph
GRAPH_MAX_ACTIVE_NODES: int = 100
GRAPH_MAX_EDGES_PER_NODE: int = 20
GRAPH_MAX_TAGS_PER_EDGE: int = 10
TAG_CANDIDATE_THRESHOLD: int = 3        # 昇格に必要な activation_count
TAG_STRENGTH_THRESHOLD: float = 0.1     # これ以下で削除
GRAPH_HOP_LIMIT: int = 2
```

### 7.2 soft-archive 基準

上限超過時、以下のスコアが最低のものから soft-archive:
- **ノード**: `mention_count × base_emotion.importance × recency_factor`
- **エッジ**: `strength × confidence`
- **タグ**: `strength`（減衰後の値）

`recency_factor = 0.5^(days_since_last_seen / 30)`

---

## 8. MCP ツール拡張

既存6ツールに3ツールを追加する。

### 8.1 `query_entity_graph`

```python
@self.mcp.tool()
def query_entity_graph(
    entity: str,
    hops: int = 1
) -> dict:
    """エンティティの関係性グラフを検索する。

    Args:
        entity: エンティティ名（部分一致で検索）
        hops: 探索ホップ数（1 or 2、デフォルト1）
    """
    return self.graph_engine.get_entity_context(entity, min(hops, 2))
```

### 8.2 `list_graph_entities`

```python
@self.mcp.tool()
def list_graph_entities(
    type_filter: str = "",
    top_n: int = 20
) -> list:
    """グラフ上のアクティブなエンティティ一覧を返す。

    Args:
        type_filter: エンティティタイプでフィルタ（person/topic/item/place/event）
        top_n: 返却上限数（デフォルト20）
    """
```

### 8.3 `forget_entity`

```python
@self.mcp.tool()
def forget_entity(entity: str) -> dict:
    """指定エンティティとその関連エッジを soft-archive する。

    Args:
        entity: エンティティ名
    """
```

---

## 9. テスト計画

### 9.1 新規テストファイル: `tests/test_relational_graph.py`

既存テストパターン（`:memory:` DB、pytest fixture、Mock LLM）を踏襲。

| テストカテゴリ | テストケース |
|---------------|-------------|
| **ノード CRUD** | 作成、重複防止（UNIQUE制約）、aliases検索、mention_count増分 |
| **エッジ CRUD** | 作成、重複防止、activation_count増分、strength更新 |
| **タグライフサイクル** | 作成（候補状態）、activation_count増分、閾値到達で昇格、confirmed フラグ |
| **減衰処理** | 指数減衰の計算精度、閾値以下でタグ削除、全タグ消失でエッジarchive |
| **同一性解決** | ラベル正規化（敬称除去）、aliases経由の検索 |
| **スコープ管理** | ノード上限超過時のsoft-archive、エッジ上限超過 |
| **感情移動平均** | 2回目の言及で基底感情が更新される |
| **process_turn統合** | テキスト→抽出→グラフ更新→減衰の一連フロー |
| **LLM非可用時** | extract_entities がスキップされ、エラーなく復帰 |
| **MCP ツール** | query_entity_graph, list_graph_entities, forget_entity |

### 9.2 既存テストへの影響

- `test_db.py`: `DatabaseManager.init()` に3テーブル追加されるが、既存テーブルへの影響なし。既存テスト通過を確認
- その他の既存テスト: 変更なし

---

## 10. 実装順序

```
Step 1: データ層                           [既存影響: なし]
  ├── db.py にスキーマ追加
  ├── relational_graph.py 骨格 + ノード/エッジ/タグ CRUD
  └── test_relational_graph.py (CRUD テスト)

Step 2: エンティティ抽出                    [既存影響: なし]
  ├── extract_entities() + プロンプト
  ├── _normalize_label() + aliases
  └── テスト追加（Mock LLM）

Step 3: タグパイプライン + 減衰              [既存影響: なし]
  ├── upsert_tag / _auto_confirm_tags
  ├── apply_decay / _compute_tag_strength
  ├── スコープ管理（_enforce_*_limit）
  └── テスト追加

Step 4: process_turn 統合                   [既存影響: なし]
  ├── process_turn() 実装
  ├── 統合テスト
  └── LLM非可用時のフォールバックテスト

Step 5: MCP ツール                          [既存影響: mcp_server.py に追加]
  ├── 3ツール追加
  └── テスト追加

Step 6: Backman/Frontman統合（次フェーズ）   [既存影響: あり]
  ├── memory_system.process_turn() にグラフ処理を接続
  ├── frontman.build_context_prompt() にグラフコンテキスト追加
  └── 統合テスト
```

**Step 1〜4 は既存コードを一切変更しない**。Step 5 で `mcp_server.py` に追記。Step 6 で初めて既存フローに接続する。

---

## 11. リスクと対策

| リスク | 影響度 | 対策 |
|--------|--------|------|
| Haiku のエンティティ抽出精度が不足 | 中 | Few-shot例を3つ以上用意。精度不足ならBackmanモデルを上位に切替可能（config.BACKMAN_MODEL） |
| 同一性解決の誤り（別人の統合/同一人物の分裂） | 中 | Phase 1 は保守的（aliases完全一致のみ）。手動merge APIで救済 |
| process_turn のレイテンシ増加（LLM追加呼び出し） | 低 | tag_emotion と並列呼び出し可能。非同期化は将来検討 |
| テーブル追加時の既存マイグレーション | 低 | `CREATE TABLE IF NOT EXISTS` パターンで既存DBに安全に追加 |
