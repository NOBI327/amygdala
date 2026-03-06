# Amygdala Phase 4 依頼書

## 上様より将軍への下知

---

## 案件概要

Amygdala（感情ベースLLMメモリ拡張システム）の Phase 4 実装を命ずる。
Phase 1-3 は完了済み（138テスト、93%カバレッジ）。

リポジトリ: https://github.com/NOBI327/amygdala

---

## Phase 4 で実装すべき内容

### 4-1. APIキーレス運用（Max/Proプラン対応）

**現状の問題:**
BackmanがLLMAdapter経由でAnthropic APIを直接呼び出すため、Max/Proプランユーザーでも別途APIキーが必要。Claude Codeの定額クォータを活用できていない。

**要件:**
- LLMAdapterに「Claude Code委任モード」を追加
- このモードでは、Backmanの感情タギングをClaude Code自体に委任する（APIを直接呼ばない）
- MCP経由でClaude Codeにタギングプロンプトを渡し、結果を受け取る構造
- config.yamlに `provider: claude_code_delegate` のような設定値で切替可能に
- 既存の `provider: anthropic / openai / gemini` はそのまま維持（API直接呼び出しも引き続きサポート）

**検討事項:**
- Claude Code自身がMCPクライアントかつMCPサーバーである構造上、BackmanがClaude Codeに「タギングして」と頼むのは循環呼び出しになる可能性がある
- 回避策1: タギングを同期的にPython内で処理し、LLM呼び出しをClaude Codeのセッション内プロンプトとして実行
- 回避策2: store_memory MCP呼び出し時にClaude Code側でタギングまで済ませてからAmygdalaに渡す（MCPツールのインターフェース変更）
- 将軍の判断に委ねる。一揆対象案件とする。

### 4-2. APIキー保安強化

**現状の問題:**
READMEのMCP設定例で `"env": { "ANTHROPIC_API_KEY": "your_key" }` と平文記載を示唆している。`.claude.json`にAPIキーが平文で入るリスク。

**要件:**
- READMEに保安注意書きセクションを追加
- 設定例を環境変数参照方式に統一（`$ANTHROPIC_API_KEY`）
- `.claude.json`へのAPIキー直接記載を非推奨と明記
- `.gitignore`テンプレートに `.env`, `.claude.json` を追加
- config.yamlの`api_key_env_var`フィールド（環境変数名指定方式）が正しく機能することを確認するテスト追加

### 4-3. README全面改訂

**現状の問題:**
READMEが技術者目線のみで、ユーザー価値の提示が弱い。

**要件:**
- 添付の `README_ja.md` 改訂案をベースにREADME_ja.mdおよびREADME.mdを更新
- Before/After テーブルの正確性確認（特にセッション切断時の挙動）
- 「実際に使い始めるまで（ステップバイステップ）」セクションの動作確認
- README記載のコマンドが実際に動くことをテストで担保

改訂案ファイルは本依頼書と同じディレクトリに配置済み。

### 4-4. フィードバック判定精度の実測基盤

**現状の問題:**
Phase 2で実装したフィードバック判定（recall記憶の使用/未使用判定）の精度が未測定。

**要件:**
- recall_logをCSVエクスポートするユーティリティスクリプト追加
- 手動ラベリングツール（対話形式でゴールドラベル付与）追加
- 精度レポート自動生成（混同行列、感情カテゴリ別精度、改善ポイント検出）
- テストシナリオ（5セッション×6ターン）のドキュメント追加

ラベリングツールとテストシナリオは本依頼書と同じディレクトリに配置済み。

---

## 優先度

| タスク | 優先度 | 理由 |
|--------|--------|------|
| 4-1. APIキーレス運用 | **最高** | Max/Proユーザーの導入障壁を直接的に除去。ユーザー数に直結 |
| 4-2. APIキー保安 | 高 | 保安問題は放置するとインシデントに直結。4-1と同時対応が効率的 |
| 4-3. README改訂 | 中 | 改訂案は既にあるため実装コストは低い。4-1完了後に実態と合わせて最終調整 |
| 4-4. フィードバック実測 | 中 | Phase 2品質の担保。ツールは配置済みだが、実測は上様が手動で実施する部分あり |

---

## 一揆対象

4-1のアーキテクチャ選定は一揆対象とする。Claude Code委任モードの実装方式について、将軍案と異なるアプローチを一揆に検討させよ。循環呼び出し回避の設計は慎重な判断が必要。

```yaml
ikki:
  enabled: true
  trigger_on: "4-1"
  advocatus_diaboli: false
```

---

## 参考資料

- 企画書: `docs/emotion-memory-system-proposal-v0.4.md`（リポジトリ内）
- Technical Deep Dive: `docs/technical-deep-dive.md`（リポジトリ内）
- README改訂案: 本依頼書と同梱
- ラベリングツール: 本依頼書と同梱
- テストシナリオ: 本依頼書と同梱

---

## 制約

- 既存のPhase 1-3のテストを壊さないこと（138テスト全PASS維持）
- カバレッジ80%以上を維持
- 新規モジュールのテストも追加すること
- LLMアダプターのDI設計を壊さないこと（全アダプターがモック可能であること）

---

*以上、Amygdala Phase 4の実装を命ずる。*
*将軍の健闘を祈る。*
