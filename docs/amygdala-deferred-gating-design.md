# Amygdala: Deferred Evaluation Gate (DEG) 設計書

> **Version**: 0.5  
> **Date**: 2026-03-12  
> **Status**: Proposal — レビュー待ち  
> **Scope**: MCP 起動率改善、感情タギング精度向上、Claude Code 環境向けアーキテクチャ最適化

---

## 1. 課題定義

### 1.1 現状の問題

Amygdala は MCP サーバーとして Claude Code に接続されている。動作自体は正常だが、**LLM が MCP 呼び出しを自発的に判断しない**という根本的な起動率問題がある。

- ユーザーが明示的に指示しない限り MCP が起動しない
- CLAUDE.md への記述だけでは起動率改善に効果なし
- 結果として、記憶システムが実質的に停止状態

### 1.2 原因分析

LLM の tool selection は **「このツールを使うべきか？」という意思決定** として処理されている。

「使うべきか？」という判断は LLM にとって難易度が高い。一方、「このパターンが存在するか？」というパターン認識は LLM が最も得意とするタスクである。

```
現行:  入力 → LLM判断("記憶すべき？") → MCP呼出 → 感情タギング
                 ↑ ここで失敗（起動率 ~10%）

改善:  数ターン蓄積 → LLM にパターン認識を依頼 → 該当あれば MCP呼出
                        ↑ LLM が得意なタスク
```

**問題の本質**: LLM に「意思決定」を求めていた。「パターン認識」に変換すれば起動率は改善する。

### 1.3 即時評価が不適切な理由

1ターンごとの即時評価には構造的な弱点がある:

- 発話の重要度は後続ターンで初めて確定することが多い
- 感情の分類（10軸ベクトルの配分）は文脈蓄積後の方が高精度
- 即時評価で store した場合、後から感情ベクトルを reconsolidate する追加コストが発生
- description が複雑になり（即時条件 + 遡及条件）、LLM が無視するリスクが上がる

---

## 2. デュアルモードアーキテクチャ

### 2.1 概要

Amygdala は利用環境に応じて2つのモードで動作する。

**API モード**: 既存の Amygdala 設計をそのまま使用。ワーキングメモリは Amygdala 自身が管理する。

**Claude Code モード (本設計の主対象)**: Claude Code のコンテキストウィンドウをワーキングメモリとして流用する。Amygdala 独自のワーキングメモリ空間は確保しない。

```
API モード:
  入力 → [Amygdala ワーキングメモリ] → 感情タギング → [Amygdala 長期記憶]
          ↑ Amygdala が管理

Claude Code モード:
  入力 → [コンテキストウィンドウ] → パターン監視+タギング → 圧縮 → [長期記憶]
          ↑ Claude Code が管理      ↑ Amygdala の責務はここから
```

### 2.2 Claude Code モードの設計根拠

Claude Code のコンテキストウィンドウは:

- 直近ターンが常に保持されている（ワーキングメモリ相当）
- セッション間で直近の対話履歴を復元する
- 追加コストゼロで参照可能

Amygdala が別途ワーキングメモリを確保する意味がない。コンテキストウィンドウを**監視対象**として利用し、Amygdala の責務を**タギング・圧縮・長期記憶管理**に集中させる。

---

## 3. Claude Code モード: 詳細設計

### 3.1 記憶パイプライン全体像

2段階パイプライン。感知とタギングは同時に行う。

```
[コンテキストウィンドウ — Claude Code 管理]
 │
 │  数ターン蓄積
 │
 ├─ パターン認識 + 感情タギング（同時実行）
 │    ├─ パターンなし → スキップ
 │    └─ パターンあり → 10軸タギング → Amygdala に一時保持
 │
 ├─ 次回の store 呼出時: 前回の一時保持を圧縮 → 長期記憶に格納
 │
 ├─ 正常終了: 一時保持中の記憶を即圧縮 → store
 │            未判定ターンを即判定 → 該当あれば即タギング → 即圧縮 → store
 │
 └─ 異常終了: 次セッション起動時にコンテキスト残存分から再開
```

### 3.2 ターン周期の扱い

**LLM はターンを正確にカウントできない。** これは設計上の前提として受け入れる。

description には「every few turns」（数ターンごと）と記述し、厳密な周期を求めない。LLM に求めるのは「前回スキャンしてから数ターン経ったので、直近の会話を振り返る」という自然な行動パターンであり、正確なカウントではない。

実際の発動間隔は Phase 1 で実測する。想定レンジは 2〜5ターン。この範囲内のばらつきはシステムの動作に実質的な影響を与えない。

設計書内で「3ターン」と記載する箇所は目安であり、要件ではない。

### 3.3 Phase 1: パターン認識 + 感情タギング

数ターンごとに、コンテキストウィンドウ内の直近ターンをスキャンする。パターンが検出されれば、**その場で10軸感情ベクトルを付与**する。感知とタギングを分離しない。

```
ターン群A → スキャン → パターンあり → 10軸タギング → 一時保持
ターン群B → スキャン → パターンなし → スキップ
ターン群C → スキャン → パターンあり → 10軸タギング → 一時保持
                                      + 前回一時保持分を圧縮→長期記憶
...
```

**4軸パターン認識 (store):**

LLM に求めるのは「使うべきか？」ではなく「このパターンが見えるか？」。

| 軸 | 検出対象 | トリガー例 | 非トリガー例 |
|---|---|---|---|
| **semantic_weight** | 発話が扱う主題の重み。作業指示 vs 戦略的・感情的・意思決定的 | 「このアーキテクチャ根本から間違ってた」 | 「変数名をcamelCaseに変えて」 |
| **context_shift** | 直近ターン内での話題の変化量 | コーディング中に「最近ちょっと疲れた」 | 「次にこの関数をテストして」 |
| **disclosure_depth** | 自己開示の深さ（事実→意見→感情→脆弱性） | 「自分にはこれ向いてないのかも」 | 「このAPIはレスポンスが遅い」 |
| **compression_ratio** | 短い発話に圧縮された意味の密度 | 「...그래」「もういい」 | 「OKです、次に進みましょう」 |

いずれか1つ以上検出 → タギング + 一時保持。全て非検出 → スキップ。

**4軸パターン認識 (recall):**

recall も同一フレーミングで統一する。「過去の記憶が有用か？」ではなく「このパターンが見えるか？」。

| 軸 | 検出対象 | トリガー例 | 非トリガー例 |
|---|---|---|---|
| **temporal_reference** | 過去の出来事・時点への言及 | 「前にこの問題やったよね」「あの時決めた方針」 | 「この関数にエラーハンドリングを追加して」 |
| **structural_echo** | 過去の意思決定と構造的に類似した状況 | 新アーキテクチャ選定中に過去の設計判断と同じパターン | 単純なリファクタリング作業 |
| **regret_signal** | 後悔・反省・「また同じ失敗」を示す発話 | 「N-bodyの時もこうだった」「前回は判断を間違えた」 | 「このテストが落ちてる」 |
| **explicit_request** | 過去の会話・決定についての明示的な問い | 「あの時なんで X にしたんだっけ」 | 「X の使い方を教えて」 |

**10軸感情ベクトル:**

```
Plutchik 8軸: joy, trust, fear, surprise, sadness, disgust, anger, anticipation
追加 2軸:     importance, urgency
```

### 3.4 Phase 2: 圧縮 → 長期記憶保存

一時保持された記憶は、**次回の store 呼出時**に圧縮されて長期記憶に格納される。

```
store 呼出 N:
  1. 前回の一時保持データがあるか確認
  2. あれば → 圧縮 → 長期記憶に格納
  3. 今回の判定結果を一時保持に入れる

store 呼出 N+1:
  1. 前回（N）の一時保持データがあるか確認
  2. あれば → 圧縮 → 長期記憶に格納
  3. 今回の判定結果を一時保持に入れる
```

**この設計の利点:**

- ターン数のカウントが不要。「前回の一時保持があるか」の確認だけ
- LLM が圧縮タイミングを判断する必要がない。store 呼出のたびに自動的に前回分を処理
- Amygdala サーバー内部で完結。タイマーもターンカウンタも不要
- 一時保持から長期記憶までの実質遅延は「次の store が発動するまで」（平均的に数ターン〜十数ターン）

**なぜ「承格判定」ではないか:**

圧縮処理は「保存するか否かの判定」ではなく、「どう圧縮するか」の処理である。Phase 1 で判定を通過した記憶は**全て保存される**。

承格判定を挟むと「保存すべきか？」という LLM 意思決定が再び必要になり、起動率問題が再発する。承格ゲートは設けない。

誤って記憶された情報は Soft-Archive 原則（削除なし、検索係数の自然減衰）で時間経過により実質的にフィルタリングされる。

**圧縮の方針:**

- 具体的なコード断片やファイルパスは除去し、意思決定の要旨と感情文脈を保持
- 複数ターンにまたがる議論は1つの記憶ユニットに統合
- 10軸感情ベクトルはそのまま付与

**圧縮の実行主体:** Amygdala サーバー内部の自動処理。MCP ツールとして LLM に公開しない。

### 3.5 セッション終了時の処理

**正常終了:**

パイプライン上のどの段階にある記憶も、即座に処理して store する。

- 未判定ターン: 即スキャン → 該当あれば即タギング → 即圧縮 → store
- 一時保持中: 即圧縮 → store

正常終了時は通常フローより文脈が少ない可能性があるが、記憶喪失より許容可能。

**異常終了（フリーズ・強制終了）:**

別途の復旧メカニズムは不要。Claude Code はセッション間で直近の対話履歴をコンテキストウィンドウに復元する。

```
セッション1: 一時保持中の記憶あり、異常終了
セッション2: コンテキストに直近ターンが残存
  → 次の監視サイクルで自然にスキャン対象に含まれる
  → Amygdala 内部の一時保持データが残っていれば、次の store 時に圧縮→格納
  → 残っていなければ、コンテキストから再判定（重複 store の可能性あり → 許容）
```

重複 store が発生しても、Soft-Archive 原則により実害はない。同一内容の記憶が2件存在しても検索係数で自然に収束する。

**対話履歴の意図的削除:**

ユーザーが履歴を削除して新セッションを開始した場合、パイプライン上の未処理記憶は失われる。これは意図的な行動であり、システムが上書きすべきではない。

---

## 4. 実装: Tool Description (Claude Code モード)

LLM に公開するツールは **amygdala_store と amygdala_recall の2つのみ**。圧縮は Amygdala サーバー内部処理であり、ツールとして公開しない。

### 4.1 amygdala_store

```
"amygdala_store" - "Periodically (every few turns), scan the recent 
turns in context for significance patterns. If patterns found, 
tag and store in one call.

PATTERN CHECKLIST (check all, trigger if any detected):
1. Semantic weight: any turn addresses strategic decisions, 
   architectural direction, project viability, or identity-level 
   topics — not routine code instructions
2. Context shift: topic moved significantly in recent turns 
   (e.g., from coding to personal reflection)
3. Disclosure depth: user revealed opinions, emotions, doubts, 
   or vulnerability beyond factual statements
4. Compressed meaning: short utterance carrying disproportionate 
   weight relative to its length

WHEN TO EVALUATE:
- Every few turns, look back at recent conversation
- When the session is ending (evaluate remaining turns)

WHAT TO STORE: the significant content with full 10-axis emotion 
vector (Plutchik 8 + importance + urgency), tagged from the 
recent context.

DO NOT TRIGGER for: stretches of conversation containing only 
routine development commands (variable renames, bug fixes, 
test additions, file operations).

Examples:
  STORE: recent turns contain 'このプロジェクト畳もうかな'
  STORE: recent turns contain 'N-bodyの時と同じ失敗だ'
  STORE: conversation shifted from coding to '最近ちょっと疲れた'
  SKIP: recent turns contain only 'fix lint errors' / 
        'run tests' / 'add type annotations'"
```

### 4.2 amygdala_recall

```
"amygdala_recall" - "Periodically (every few turns), scan the 
recent turns in context for patterns suggesting past memories 
would be relevant.

PATTERN CHECKLIST (trigger if any detected):
1. Temporal reference: user mentions past events, prior decisions, 
   or specific time points ('前にこの問題やったよね', 
   'あの時決めた方針')
2. Structural echo: current situation mirrors a past decision 
   pattern (e.g., choosing between architectures again)
3. Regret signal: user expresses regret, self-correction, or 
   'same mistake again' ('N-bodyの時もこうだった', 
   '前回は判断を間違えた')
4. Explicit request: user directly asks about past conversations 
   or decisions ('あの時なんで X にしたんだっけ')

WHEN TO EVALUATE:
- Every few turns (same cycle as store evaluation)
- Immediately when user explicitly requests past context

DO NOT TRIGGER for: routine code tasks with no reference to 
past decisions or experiences."
```

### 4.3 CLAUDE.md 補助記述

```markdown
## Amygdala Memory Protocol (Claude Code Mode)

Amygdala uses the context window as working memory. 
No separate working memory space is allocated.

Every few turns, look back at recent conversation and check:

For STORE — do you see any of these patterns?
  - Semantic weight (strategic/emotional/identity-level topic)
  - Context shift (topic changed significantly)
  - Disclosure depth (user shared opinions/emotions/vulnerability)
  - Compressed meaning (short utterance, heavy weight)

For RECALL — do you see any of these patterns?
  - Temporal reference (user mentions past events/decisions)
  - Structural echo (current situation mirrors a past one)
  - Regret signal (user hints at repeating past mistakes)
  - Explicit request (user asks about prior discussions)

Pattern found → call the tool. No pattern → do nothing.

Your job is PATTERN RECOGNITION, not DECISION MAKING.

On session end: evaluate and store all pending memories.
```

---

## 5. トレードオフ分析

### 5.1 トークン効率比較

前提: 20ターン会話、記憶が必要なターン10

| 方式 | MCP呼出回数 | トークン消費 | カバー率 | トークン/カバー率% | 追加インフラ |
|------|-----------|------------|---------|------------------|------------|
| MCP only (現状) | ~2 | ~1,600 | ~10% | 160 | なし |
| Description改善のみ | ~8 | ~5,000〜8,000 | ~60〜80% | ~83〜100 | なし |
| 2段階DEG (v0.1) | ~16 | ~10,000〜12,000 | ~80〜90% | ~125〜133 | なし |
| **本案 (v0.5)** | **~7** | **~4,500〜5,500** | **~80〜90%** | **~56〜61** | **なし** |
| CLI 強制 full recall | ~20 | ~20,000 | 100% | 200 | Hook構築 |
| ハイブリッド (CLI+MCP) | ~15 | ~3,500 | ~90% | ~39 | CLI構築 |

### 5.2 バージョン間比較

| 項目 | v0.1 | v0.2 | v0.3 | v0.4 | v0.5 (本案) |
|------|------|------|------|------|-------------|
| パイプライン段階 | 3 | 1 | 3 | 2 | **2** |
| MCP 呼出/20ターン | ~16 | ~10 | ~10 | ~7 | **~7** |
| トークン | ~10K〜12K | ~6K〜7K | ~6K〜7K | ~4.5K〜5.5K | **~4.5K〜5.5K** |
| タギング精度 | 即時分不完全 | 3ターン文脈 | 6ターン文脈 | 3ターン文脈 | **数ターン文脈** |
| reconsolidate | ~6回 | 0 | 0 | 0 | **0** |
| description 複雑度 | 高 | 低 | 中 | 低 | **低** |
| ターンカウント依存 | なし | 厳密3ターン | 厳密3ターン | 厳密3ターン | **不要（概算）** |
| 圧縮トリガー | — | — | サーバータイマー | サーバータイマー | **次回store時自動** |
| LLM 公開ツール数 | 2+ | 2 | 3 | 3 | **2** |
| recall フレーミング | 意思決定型 | 意思決定型 | 意思決定型 | 意思決定型 | **パターン認識型** |

### 5.3 v0.4 → v0.5 の変更点

1. **ターン周期**: 「every 3 turns」→「every few turns」。LLM にカウントを求めない設計に変更
2. **圧縮トリガー**: サーバー側タイマー → 次回 store 呼出時に前回分を自動圧縮。タイマー/カウンタ不要に
3. **recall の description**: 意思決定フレーミング → 4軸パターン認識フレーミングに統一
4. **amygdala_compress**: ツールとして LLM に公開しない。サーバー内部処理に変更
5. **圧縮品質**: Phase 3 に評価方法の具体化を追記

### 5.4 リスク

| リスク | 重大度 | 緩和策 |
|--------|--------|--------|
| LLM の発動間隔が不安定（毎ターン or 10ターンに1回） | 中 | Phase 1 で実測。極端に偏る場合は description 調整 |
| 一時保持が長期間圧縮されない（store 未発動が続く場合） | 低 | 実害は小さい（一時保持のまま参照可能）。正常終了時に一括処理 |
| 異常終了で一時保持データが消失 | 低 | コンテキスト復元から再判定。重複 store は Soft-Archive で吸収 |
| 圧縮で重要な文脈が失われる | 低 | 圧縮ルールを保守的に設定（意思決定根拠と感情文脈は必ず保持） |
| False positive（不要な store） | 低 | Soft-Archive の検索係数減衰で自然フィルタリング |
| Description が長すぎて無視される | 低 | LLM 公開ツール2つに削減。各 description も簡潔化済み |

---

## 6. 実装ロードマップ

### Phase 1: Description 差し替え + 実測（即日実施可能）

- [ ] amygdala_store / amygdala_recall の description を本設計書の内容に差し替え
- [ ] CLAUDE.md に補助記述を追加
- [ ] 起動率を測定（目標: 10% → 60%+）
- [ ] 実際の発動間隔を記録（LLM が何ターンおきに発動するか）
- [ ] store / recall それぞれの false positive / false negative を目視確認

### Phase 2: 圧縮パイプライン実装

- [ ] store 呼出時に「前回の一時保持があれば圧縮→長期記憶格納」のロジックをサーバー側に実装
- [ ] 圧縮ルールの定義と検証
- [ ] セッション終了時の一括処理フローの実装
- [ ] 圧縮前後の記憶内容を比較し、必要情報の保持率を確認

### Phase 3: チューニング

- [ ] 4軸パターン認識の閾値調整（軸ごとの信頼度差を検証）
- [ ] false positive / false negative 率の定量測定
- [ ] 圧縮品質の評価基準を定義: 圧縮後の記憶で recall した際に、元の文脈の意思決定根拠と感情文脈が復元可能か
- [ ] description の長さと起動率の関係を検証

### Phase 4: 手動オーバーライド（オプション）

- [ ] `/amygdala off` による完全停止オプション
- [ ] 純粋コーディングセッションでの不要発動を抑制

---

## Appendix: 棄却された設計案

本設計に至るまでに検討・棄却された設計案の記録。

**A. CLI 強制実行**: hook で毎ターン store/recall を強制。起動率100%だが、トークン効率が最悪（~20,000/20ターン）。不要ターンでの発動を止められない。

**B. ハイブリッド (CLI index + MCP)**: CLI で軽量インデックスを毎ターン注入し、MCP で full recall。効率は良いが CLI 構築コストが発生。

**C. 手動 on/off スイッチ**: `/amygdala on` で強制起動、`off` で停止。ユーザー認知コストが発生。Phase 4 でオプションとして残す。

**D. 2段階 DEG (v0.1)**: 即時パス（importance/urgency）+ 遡及パス（Plutchik 8軸）。description が複雑になり LLM 無視リスクが高い。reconsolidate コストも追加。

**E. 10ターン承格判定**: タギング後10ターンで「長期記憶に入れるか」を判定。承格判定が LLM 意思決定の再導入となり、起動率問題が再発するため棄却。

**F. 感知・タギング分離 (v0.3)**: 感知後さらに3ターン待ってタギング。6ターン文脈による精度向上を期待したが、3ターン文脈で十分であり、パイプライン複雑化に見合わないため統合。

**G. サーバー側タイマーによる圧縮 (v0.3/v0.4)**: 一時保持から N ターン後にサーバー側タイマーで圧縮。サーバーが Claude Code のターン数を知る手段がないため、「次回 store 呼出時に前回分を圧縮」に変更。

---

*本設計書は 2026-03-12 の設計議論に基づく。*  
*議論経緯: MCP 起動率問題 → CLI/Hook 代替案 → 手動 on/off → 遅延評価着想 → 2段階案 → 3ターン統一 → デュアルモード + 圧縮パイプライン → 感知+タギング統合 → ターンカウント撤廃 + 圧縮トリガー簡素化 + recall パターン認識化に収束*
