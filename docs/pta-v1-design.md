# Amygdala: Pattern-Triggered Activation (PTA) v1.0 設計書

> **Version**: 1.0
> **Date**: 2026-03-13
> **Status**: レビュー中
> **Base**: DEG v0.5 設計書を現行コードベース（Phase 7 完了後）に適合させた改訂版
> **Scope**: MCP 起動率改善（store/recall の description リフレーミング）
> **旧称**: Deferred Evaluation Gate (DEG) — v0.5 では遅延評価+ゲート判定が設計の核だったが、v1.0 では遅延もゲートも不要となりパターン認識トリガーに収束したため改称

---

## 1. 課題定義

### 1.1 現状の問題

Amygdala は MCP サーバーとして Claude Code に接続されており、10ツールが公開されている。動作自体は正常だが、**LLM が store_memory / recall_memories を自発的に呼び出さない**という起動率問題がある。

- ユーザーが明示的に指示しない限り store/recall が発動しない
- CLAUDE.md に「重要な情報があったら store_memory で保存する」と記述しているが効果が薄い
- context_daemon + session_hook による受動的 recall は機能しているが、能動的な store/recall がほぼ停止状態

### 1.2 原因分析

現在の tool description は**意思決定フレーミング**になっている:

```
現行 store_memory description:
  「感情タギングしてメモリをDBに保存する」
  → LLM は「今この瞬間に保存すべきか？」を毎ターン判断する必要がある
  → 判断コストが高く、結果として呼び出しを回避する

現行 recall_memories description:
  「感情ベース検索でメモリを取得する」
  → LLM は「今この瞬間に検索すべきか？」を毎ターン判断する必要がある
  → コンテキストウィンドウ内の情報で足りるため、呼び出す動機がない
```

**解決策**: 「使うべきか？」（意思決定）→「このパターンが見えるか？」（パターン認識）にリフレーミングする。パターン認識は LLM が最も得意とするタスクであり、起動率の大幅改善が見込める。

### 1.3 現行システムとの関係

本設計は**既存の10ツール構成を維持**したまま、store_memory と recall_memories の description のみを変更する。

変更しないもの:
- サーバー側コード（`mcp_server.py` のロジック）
- 引数・戻り値の仕様
- DB スキーマ
- context_daemon / session_hook
- pin 系ツール（pin_memory, unpin_memory, list_pinned_memories）
- graph 系ツール（query_entity_graph, list_graph_entities, forget_entity）
- get_stats, get_active_context

---

## 2. 設計: パターン認識フレーミング

### 2.1 基本原理

コンテキストウィンドウを「ワーキングメモリ」として利用する。Amygdala 側に別途ワーキングメモリを確保しない（MCP モードでは既にそうなっている）。

LLM に求めるのは:
- ✗ 「このターンを保存すべきか？」（意思決定 — 難しい）
- ✓ 「直近の数ターンにこのパターンが見えるか？」（パターン認識 — 得意）

### 2.2 ターン周期

**LLM はターンを正確にカウントできない。** description には「every few turns」と記述し、厳密な周期を求めない。

実際の発動間隔は Phase 1 で実測する。想定レンジは 2〜5 ターン。このばらつきはシステムの動作に実質的な影響を与えない。

### 2.3 store 用 4軸パターン認識

| 軸 | 検出対象 | トリガー例 | 非トリガー例 |
|---|---|---|---|
| **semantic_weight** | 発話が扱う主題の重み。作業指示 vs 戦略的・感情的・意思決定的 | 「このアーキテクチャ根本から間違ってた」 | 「変数名をcamelCaseに変えて」 |
| **context_shift** | 直近ターン内での話題の変化量 | コーディング中に「最近ちょっと疲れた」 | 「次にこの関数をテストして」 |
| **disclosure_depth** | 自己開示の深さ（事実→意見→感情→脆弱性） | 「自分にはこれ向いてないのかも」 | 「このAPIはレスポンスが遅い」 |
| **compression_ratio** | 短い発話に圧縮された意味の密度 | 「...もういい」 | 「OKです、次に進みましょう」 |

いずれか1つ以上検出 → store_memory 呼出。全て非検出 → スキップ。

### 2.4 recall 用 4軸パターン認識

| 軸 | 検出対象 | トリガー例 | 非トリガー例 |
|---|---|---|---|
| **temporal_reference** | 過去の出来事・時点への言及 | 「前にこの問題やったよね」 | 「この関数にエラーハンドリングを追加して」 |
| **structural_echo** | 過去の意思決定と構造的に類似した状況 | 新アーキテクチャ選定中に過去の設計判断と同じパターン | 単純なリファクタリング作業 |
| **regret_signal** | 後悔・反省・「また同じ失敗」を示す発話 | 「前回は判断を間違えた」 | 「このテストが落ちてる」 |
| **explicit_request** | 過去の会話・決定についての明示的な問い | 「あの時なんで X にしたんだっけ」 | 「X の使い方を教えて」 |

### 2.5 context_daemon との補完関係

| 機構 | トリガー | 方向 |
|---|---|---|
| context_daemon | 新しい memory が DB に INSERT された時 | 受動的（store が起きた後の自動 recall） |
| PTA recall パターン認識 | コンテキスト内にパターンが見えた時 | 能動的（LLM が自発的に recall） |

両者は競合せず補完する。daemon は「store された記憶に基づく連想」、PTA recall は「会話の流れから過去を引く」。

---

## 3. 実装: Tool Description

### 3.1 store_memory（変更後）

```python
"""Periodically (every few turns), scan the recent turns in context
for significance patterns. If any pattern detected, tag and store.

PATTERN CHECKLIST (trigger if ANY detected):
1. Semantic weight — topic addresses strategic decisions,
   architectural direction, project viability, or identity-level
   concerns (not routine code instructions)
2. Context shift — topic changed significantly in recent turns
   (e.g., from coding to personal reflection)
3. Disclosure depth — user revealed opinions, emotions, doubts,
   or vulnerability beyond factual statements
4. Compressed meaning — short utterance carrying disproportionate
   weight relative to its length

WHEN TO SCAN:
- Every few turns, look back at recent conversation

WHAT TO STORE: the significant content with full 10-axis emotion
vector and scene tags. Compress multi-turn discussions into one
memory unit focusing on decisions and emotional context.

DO NOT TRIGGER for routine development work (variable renames,
bug fixes, test runs, file operations, lint fixes).

Emotion axes (0.0-1.0):
  joy, sadness, anger, fear, surprise, disgust,
  trust, anticipation, importance, urgency

Scene tags (max 3):
  work, relationship, hobby, health, learning,
  daily, philosophy, meta

Entity extraction (optional):
  entities=[{"label":"X","type":"person|topic|item|place|event",
  "aliases":[],"relations":[{"target":"Y","tags":[]}]}]

Examples:
  STORE: 'このプロジェクト畳もうかな' (semantic_weight)
  STORE: coding → '最近ちょっと疲れた' (context_shift)
  STORE: 'N-bodyの時と同じ失敗だ' (disclosure_depth + compressed)
  SKIP: 'fix lint errors' / 'run tests' / 'add type annotations'"""
```

### 3.2 recall_memories（変更後）

```python
"""Periodically (every few turns), scan the recent turns in context
for patterns suggesting past memories would be relevant.

PATTERN CHECKLIST (trigger if ANY detected):
1. Temporal reference — user mentions past events, prior decisions,
   or specific time points ('前にこの問題やったよね',
   'あの時決めた方針')
2. Structural echo — current situation mirrors a past decision
   pattern (e.g., choosing between architectures again)
3. Regret signal — user expresses regret, self-correction, or
   'same mistake again' sentiment
4. Explicit request — user directly asks about past conversations
   or decisions ('あの時なんで X にしたんだっけ')

WHEN TO SCAN:
- Every few turns (same cycle as store evaluation)
- Immediately when user explicitly asks about the past

DO NOT TRIGGER for routine code tasks with no reference to
past decisions or experiences.

Args:
  query: search query (describe the topic or context to recall)
  top_n: max results (default 5)
  emotions: emotion vector for emotion-based search (0.0-1.0)
    axes: joy, sadness, anger, fear, surprise, disgust,
    trust, anticipation, importance, urgency

Note: scenes filtering is not supported in recall. The server
searches across all scenes using the emotion vector only."""
```

### 3.3 他ツールの description

変更なし。pin 系・graph 系・stats・get_active_context は現行のまま維持。これらは DEG のスコープ外であり、従来通りユーザー指示または LLM 判断で呼び出す。

---

## 4. CLAUDE.md 補助記述

現行の CLAUDE.md「行動ルール」セクションを以下に差し替える:

```markdown
## 行動ルール（必須）

### Amygdala Memory Protocol (Claude Code Mode)

Amygdala はコンテキストウィンドウをワーキングメモリとして利用する。
独自のワーキングメモリ空間は確保しない。

数ターンごとに、直近の会話を振り返って以下をチェック:

**STORE** — 以下のパターンが見えるか？
  - Semantic weight（戦略的・感情的・アイデンティティ的な話題）
  - Context shift（話題が大きく変わった）
  - Disclosure depth（ユーザーが意見・感情・脆弱性を開示した）
  - Compressed meaning（短い発話に重い意味が込められている）

**RECALL** — 以下のパターンが見えるか？
  - Temporal reference（過去の出来事・決定への言及）
  - Structural echo（現在の状況が過去の判断と類似）
  - Regret signal（過去の失敗の繰り返しを示唆）
  - Explicit request（過去の会話・決定について質問）

パターンが見えたら → ツールを呼ぶ。見えなければ → 何もしない。

あなたの仕事は **パターン認識** であり、**意思決定** ではない。

### その他
- deferred tool の場合は先に ToolSearch でスキーマを取得してから呼び出す
- 「覚えていない」「セッションが変わったので不明」と即答してはならない
```

### 4.1 CLAUDE.md の配置方針

amygdala の記憶プロトコルはプロジェクト CLAUDE.md（`amygdala/CLAUDE.md`）にのみ記述する。

グローバル CLAUDE.md（`~/.claude/CLAUDE.md`）には amygdala 固有のルールを書かない。グローバル側に既存の amygdala 記述がある場合は、プロジェクト CLAUDE.md への参照に置き換える。

**理由**: amygdala を MCP サーバーとして使う他ユーザーは、自身のグローバル CLAUDE.md を持っている。プロジェクト CLAUDE.md に集約することで:
- ユーザーのグローバル設定を汚染しない
- amygdala リポジトリの更新で記憶プロトコルも自動的に更新される
- 複数プロジェクトで amygdala を使う場合も一貫性が保たれる

---

## 5. トレードオフ分析

### 5.1 変更の影響範囲

| 変更対象 | 変更内容 | リスク |
|---|---|---|
| store_memory description | 意思決定型 → パターン認識型 | なし（引数・戻り値は同一） |
| recall_memories description | 意思決定型 → パターン認識型 | なし（引数・戻り値は同一） |
| CLAUDE.md | 行動ルール差し替え | なし（補助的な指示のみ） |
| サーバーコード | **変更なし** | — |
| DB スキーマ | **変更なし** | — |
| 他ツール | **変更なし** | — |

### 5.2 リスク

| リスク | 重大度 | 緩和策 |
|---|---|---|
| LLM の発動間隔が不安定（毎ターン or 10ターンに1回） | 中 | 実測して description 調整。極端な場合のみ対処 |
| False positive（ルーティン作業を store してしまう） | 低 | Soft-Archive の検索係数減衰で自然フィルタリング |
| Description が長すぎて LLM に無視される | 低 | store/recall 以外のツールは短い description のまま。全体の description 負荷は許容範囲内 |
| recall の二重発動（daemon + PTA） | 低 | 実害なし。daemon は受動、DEG は能動で用途が異なる |

### 5.3 期待効果

Phase 1 で実測する。現状の体感起動率（store ~10%、recall ~5%）からの改善幅を記録し、Phase 2 のチューニング判断に使う。

トークン増加は description の増加分のみ（~500トークン/ツール定義）。サーバー側の処理コストは変わらない。

---

## 6. 実装ロードマップ

### Phase 1: Description 差し替え + 実測（本設計のスコープ）

- [ ] `mcp_server.py`: store_memory の docstring を §3.1 に差し替え
- [ ] `mcp_server.py`: recall_memories の docstring を §3.2 に差し替え
- [ ] `CLAUDE.md`: 行動ルールを §4 に差し替え
- [ ] 起動率を実測（数セッションにわたって目視確認）
- [ ] 発動間隔・false positive / false negative の記録

### Phase 2: チューニング（将来検討）

- 4軸パターン認識の閾値調整
- description の長さと起動率の関係検証
- `/amygdala off` による手動停止オプション

---

## Appendix: 現行 description との差分

### store_memory

```diff
- 感情タギングしてメモリをDBに保存する。
-
- emotions引数は全10軸の感情スコアをdictで渡す(0.0-1.0)。
- 省略した場合、内部LLMで自動タギングする(ANTHROPIC_API_KEY必要)。
- APIキー未設定時はemotionsを明示的に渡すことを強く推奨。
- ...（API仕様の説明が続く）

+ Periodically (every few turns), scan the recent turns in context
+ for significance patterns. If any pattern detected, tag and store.
+
+ PATTERN CHECKLIST (trigger if ANY detected):
+ 1. Semantic weight — ...
+ 2. Context shift — ...
+ 3. Disclosure depth — ...
+ 4. Compressed meaning — ...
+ ...（パターン認識の指示 + API仕様）
```

**変更の本質**: 「何をするツールか」の説明 → 「いつ・どう使うか」の行動指示。引数の使い方の説明は維持。

### recall_memories

```diff
- 感情ベース検索でメモリを取得する。
-
- emotions引数で検索クエリの感情ベクトルを明示的に渡せる(0.0-1.0)。
- ...

+ Periodically (every few turns), scan the recent turns in context
+ for patterns suggesting past memories would be relevant.
+
+ PATTERN CHECKLIST (trigger if ANY detected):
+ 1. Temporal reference — ...
+ 2. Structural echo — ...
+ 3. Regret signal — ...
+ 4. Explicit request — ...
+ ...
```

---

*本設計書は DEG v0.5 設計書（2026-03-12）を現行コードベースに適合させた改訂版である。*
*v0.5 → v1.0 の主な変更: DEG → PTA に改称、ツール数削減の撤回（10ツール維持）、context_daemon との補完関係の明記、圧縮パイプラインの削除（MCP モードでは LLM が圧縮済みテキストを渡すため不要）、session ending 記述の削除（LLM がセッション終了を事前検知できないため）、目標数値の削除（根拠なし→実測で判断）、CLAUDE.md 配置方針の追加、Phase 1 に集中したスコープ限定。*
