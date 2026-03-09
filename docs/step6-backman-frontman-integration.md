# Step 6: Backman/Frontman 統合 — 詳細設計書

> 前提: Step 1〜5 完了済み。RelationalGraphEngine は独立動作可能。
> 本ステップで既存フローにグラフ処理を接続する。

---

## 1. 変更スコープ

| ファイル | 変更内容 | 影響度 |
|----------|----------|--------|
| `src/memory_system.py` | `process_turn()` にグラフ処理を追加、`__init__` に GraphEngine 保持 | **高** |
| `src/frontman.py` | `build_context_prompt()` にグラフコンテキストセクション追加 | **中** |
| `src/mcp_server.py` | `store_memory()` でグラフ更新を呼び出す | **中** |
| `tests/test_memory_system.py` | 統合テスト追加 | — |
| `tests/test_frontman.py` | グラフコンテキスト表示テスト追加 | — |
| `tests/test_mcp_server.py` | store_memory → グラフ更新テスト追加 | — |

**変更しないファイル**: `config.py`, `db.py`, `relational_graph.py`, `backman.py`

---

## 2. memory_system.py の変更

### 2.1 `__init__`: RelationalGraphEngine の保持

```python
from .relational_graph import RelationalGraphEngine

class MemorySystem:
    def __init__(self, llm_client, db_manager, config=None):
        # ... 既存の初期化 ...
        self.graph_engine = RelationalGraphEngine(
            config=self.config,
            db_manager=db_manager,
            llm_adapter=llm_client,  # LLMAdapter duck type
        )
```

**設計判断**: `mcp_server.py` が既に `self.graph_engine` を持っているが、`MemorySystem` 側にも持たせる。理由:
- `MemorySystem.process_turn()` はAPIモード（MCP不使用）からも呼ばれる
- MCPサーバーの `graph_engine` は MCP ツール用、MemorySystem の `graph_engine` はパイプライン統合用
- 同じ `db_manager` を共有するため、データの不整合は発生しない

### 2.2 `process_turn()`: グラフ処理の接続ポイント

現在のフロー（9ステップ）に **ステップ 2.5** として挿入する。

```
既存フロー:
  1. ピン登録要求の検出
  2. backman.tag_emotion(user_input) → emotion_vec, scenes
  3. search_engine.search_memories(emotion_vec, scenes)
  3.5. diversity_watchdog.apply_exploration(...)
  4. frontman.build_context_prompt(working_mem, pins, search_results)
  5. frontman.generate_response(...)
  6〜9. ワーキングメモリ, TTL, フィードバック

変更後:
  1. ピン登録要求の検出
  2. backman.tag_emotion(user_input) → emotion_vec, scenes
★ 2.5. graph_engine.process_turn(user_input, emotion_vec) → graph_result
  3. search_engine.search_memories(emotion_vec, scenes)
  3.5. diversity_watchdog.apply_exploration(...)
★ 3.7. graph_engine.get_entity_context() で関連エンティティ取得
  4. frontman.build_context_prompt(working_mem, pins, search_results, graph_contexts)
  5. frontman.generate_response(...)
  6〜9. 変更なし
```

### 2.3 グラフ処理の具体コード

```python
# ステップ 2.5: グラフ更新
graph_result = {"updated": False}
try:
    graph_result = self.graph_engine.process_turn(user_input, emotion_vec)
except Exception as e:
    logger.warning(f"Graph processing failed: {e}")
    # グラフ処理失敗は非致命的 — 既存フローに影響させない

# ステップ 3.7: 関連エンティティのコンテキスト取得
graph_contexts = []
if graph_result.get("updated"):
    # process_turn で更新されたノードに対してコンテキストを取得
    # ただしエンティティ抽出結果は process_turn の内部で消費されるため、
    # ここでは直近のアクティブエンティティ（上位3件）からコンテキストを取得する
    graph_contexts = self._get_relevant_graph_contexts(user_input, emotion_vec)
```

### 2.4 `_get_relevant_graph_contexts()` — 新規ヘルパー

```python
def _get_relevant_graph_contexts(self, text: str, emotion_vec: dict) -> list:
    """テキストに関連するグラフコンテキストを最大3件取得する。

    戦略:
    1. 感情ベクトルで類似ノードを検索（search_by_emotion）
    2. 各ノードの EntityContext を取得
    3. 上位3件を返却
    """
    try:
        nodes = self.graph_engine.search_by_emotion(emotion_vec, top_k=3)
        contexts = []
        for node in nodes:
            ctx = self.graph_engine.get_entity_context(node["label"])
            if ctx:
                contexts.append(ctx)
        return contexts
    except Exception as e:
        logger.warning(f"Failed to get graph contexts: {e}")
        return []
```

**選択肢と判断**:

| 方式 | メリット | デメリット | 採否 |
|------|----------|------------|------|
| A. テキストからLLMで再抽出 | 精度が高い | LLM追加呼び出し、レイテンシ増 | × |
| B. process_turn の戻り値からノード名を取得 | 追加呼び出し不要 | process_turn の戻り値に名前がない | △ 要改修 |
| C. search_by_emotion で近い感情のノードを取得 | LLM不要、シンプル | テキスト内容と直接一致しない場合あり | ○ 採用 |

→ **方式 C** を採用。理由:
- LLM追加呼び出しを避ける（レイテンシ・コスト）
- 感情的に関連するエンティティは文脈として十分有用
- process_turn 内の extract_entities が既にグラフを更新済みなので、直後の search_by_emotion で最新状態が取得できる

**代替案 B の検討**: `process_turn` の戻り値に `node_labels: List[str]` を追加する拡張は将来検討。現時点では relational_graph.py の変更を避ける。

---

## 3. frontman.py の変更

### 3.1 `build_context_prompt()` の引数追加

```python
def build_context_prompt(
    self,
    working_memory: List[Dict],
    pin_memories: List[Dict],
    search_results: List[Dict],
    graph_contexts: Optional[List[Dict]] = None,  # ← 追加（後方互換）
) -> str:
```

**後方互換**: `graph_contexts` をオプション引数にすることで、既存の呼び出し箇所（テスト含む）が変更なしで動作する。

### 3.2 グラフコンテキストセクションの追加

プロンプト構造の更新:

```
1. [📌 ピンメモリ]         — 最大3件
2. [🔗 関連エンティティ]   — 最大3件 ★新規
3. [🔍 関連記憶]           — 検索結果上位3件
4. [💬 最近の会話]          — 直近5ターン
```

グラフコンテキストをピンメモリの直後、検索結果の前に配置する理由:
- エンティティ情報は「誰の話か」「何の話か」の文脈を提供する
- 検索結果より先に読ませることで、検索結果の解釈精度が上がる
- ピンメモリ（ユーザー明示の重要情報）の次に優先度が高い

### 3.3 セクション描画の実装

```python
if graph_contexts:
    graph_section = "🔗 **関連エンティティ（関係性グラフ）**:\n"
    for ctx in graph_contexts[:3]:
        entity = ctx["entity"]
        related = ", ".join(ctx["related_entities"][:5])
        tags = ", ".join(ctx["active_tags"][:5])
        # 主要感情の表示（上位2軸）
        emo = ctx.get("primary_emotion", {})
        emo_str = ", ".join(
            f"{k}:{v:.1f}" for k, v in
            sorted(emo.items(), key=lambda x: x[1], reverse=True)[:2]
        ) if emo else "N/A"
        line = f"- **{entity}**"
        if related:
            line += f" → 関連: {related}"
        if tags:
            line += f" [タグ: {tags}]"
        line += f" (感情: {emo_str})"
        graph_section += line + "\n"
    sections.append(graph_section)
```

**表示例**:
```
🔗 **関連エンティティ（関係性グラフ）**:
- **田中** → 関連: プロジェクトA, 佐藤 [タグ: 担当者, 上司] (感情: trust:0.7, joy:0.5)
- **プロジェクトA** → 関連: 田中, 納期 [タグ: 進行中] (感情: anticipation:0.6, importance:0.8)
```

---

## 4. mcp_server.py の変更

### 4.1 `store_memory()` でグラフ更新を呼び出す

現在の `store_memory()` はテキストをDBに保存するだけで、グラフ更新は行われない。MCPモード（Claude Code経由）で `store_memory` が呼ばれた場合にもグラフが更新されるようにする。

```python
def store_memory(self, text, context, emotions_input, scenes_input):
    # ... 既存の保存処理 ...

    # グラフ更新（非致命的）
    try:
        self.graph_engine.process_turn(text, emotion)
    except Exception as e:
        logger.warning(f"Graph update on store_memory failed: {e}")

    return result
```

**注意**: `MemorySystem.process_turn()` からもグラフ更新が呼ばれるが、MCPモードでは `MemorySystem.process_turn()` は使われない（各MCPツールが個別にロジックを持つ）。したがって二重更新は発生しない。

---

## 5. エラーハンドリング方針

グラフ処理は **全て非致命的** とする。

| 場所 | 失敗時の挙動 |
|------|-------------|
| `memory_system.process_turn` 内の `graph_engine.process_turn` | `logger.warning` → 既存フロー続行 |
| `_get_relevant_graph_contexts` | `logger.warning` → 空リスト返却 |
| `frontman.build_context_prompt` 内の `graph_contexts` | None/空なら該当セクションをスキップ |
| `mcp_server.store_memory` 内の `graph_engine.process_turn` | `logger.warning` → 保存結果をそのまま返却 |

**原則**: グラフ機能はエンリッチメントであり、失敗しても記憶の保存・検索・応答生成は正常動作する。

---

## 6. テスト計画

### 6.1 `test_memory_system.py` に追加

| テストケース | 検証内容 |
|-------------|----------|
| `test_graph_engine_initialized` | `MemorySystem.__init__` 後に `graph_engine` が存在する |
| `test_process_turn_calls_graph_process_turn` | `process_turn` 内で `graph_engine.process_turn` が呼ばれる |
| `test_graph_failure_does_not_break_process_turn` | `graph_engine.process_turn` が例外を投げても、応答が正常に返る |
| `test_graph_contexts_passed_to_build_context_prompt` | `build_context_prompt` に `graph_contexts` 引数が渡される |
| `test_process_turn_without_llm_skips_graph` | LLM非可用時、`graph_engine.process_turn` は呼ばれるが entities=[] で即リターン |

### 6.2 `test_frontman.py` に追加

| テストケース | 検証内容 |
|-------------|----------|
| `test_graph_contexts_section_included` | `graph_contexts` を渡すとプロンプトに「関連エンティティ」セクションが含まれる |
| `test_graph_contexts_none_no_section` | `graph_contexts=None` でセクションが出ない（後方互換） |
| `test_graph_contexts_empty_no_section` | `graph_contexts=[]` でセクションが出ない |
| `test_graph_contexts_max_3` | 5件渡しても3件まで表示される |
| `test_graph_contexts_section_order` | ピンメモリの後、検索結果の前に配置される |
| `test_graph_context_display_format` | entity名、related_entities、active_tags、primary_emotionが正しくフォーマットされる |

### 6.3 `test_mcp_server.py` に追加

| テストケース | 検証内容 |
|-------------|----------|
| `test_store_memory_triggers_graph_update` | `store_memory` 呼び出し後にグラフノードが更新されている |
| `test_store_memory_graph_failure_still_saves` | `graph_engine.process_turn` が失敗しても `store_memory` は正常完了する |

---

## 7. 実装順序

```
Step 6a: frontman.py の変更                [既存テスト影響: なし]
  ├── build_context_prompt() に graph_contexts 引数追加
  ├── グラフコンテキストセクション描画
  └── test_frontman.py にテスト追加

Step 6b: memory_system.py の変更           [既存テスト影響: なし]
  ├── __init__ に graph_engine 追加
  ├── process_turn() にステップ 2.5, 3.7 追加
  ├── _get_relevant_graph_contexts() 追加
  └── test_memory_system.py にテスト追加

Step 6c: mcp_server.py の変更              [既存テスト影響: なし]
  ├── store_memory() にグラフ更新追加
  └── test_mcp_server.py にテスト追加
```

**各ステップ後にテスト全通過を確認してから次へ進む。**

---

## 8. リスクと対策

| リスク | 影響度 | 対策 |
|--------|--------|------|
| `graph_engine.process_turn` の LLM 呼び出しがレイテンシ増 | 中 | try/except で分離。LLM非可用時は即リターン（既存挙動） |
| `search_by_emotion` がゼロベクターで無意味な結果を返す | 低 | ゼロベクター時は `_get_relevant_graph_contexts` をスキップ |
| `build_context_prompt` のプロンプト長がモデルのコンテキスト窓を圧迫 | 低 | graph_contexts は最大3件（各1行）。約200トークン増加程度 |
| 既存テストの `build_context_prompt` 呼び出しが引数不一致で失敗 | 低 | `graph_contexts` はデフォルト `None` で後方互換 |
