# Amygdala Project

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
