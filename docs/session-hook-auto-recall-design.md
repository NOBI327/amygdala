# セッション開始時フック自動リコール — 設計書

> Claude Code の SessionStart hook を利用し、新セッション開始時にamygdalaの記憶を自動注入する。

---

## 1. 背景と課題

### 1.1 現状の限界

Phase 6 で自動コンテキストデーモンを実装し、memoriesテーブルのポーリング監視→コンテキスト一時ファイル書き出しまでは動作している。しかし:

- MCPプロトコルにはサーバー→クライアントへのプッシュ機構がない
- `get_active_context` MCPツールはLLMが「呼ぶ」と判断しない限り発火しない
- MCP Resources の自動購読もClaude Code未対応（手動@メンション参照のみ）
- 結果として、デーモンが「準備」した記憶が「配達」されない

### 1.2 最終ゴール

**新しいセッションを開始して話しかけたら、自動的に前セッションまでの会話をDBで読んできて会話を続けられる。**

LLMの判断を一切介さず、ユーザーの操作も不要。

### 1.3 解決策: Claude Code SessionStart Hook

Claude Code の hooks 機能を利用する。`SessionStart` イベントで shell コマンドを実行し、その stdout がセッションの初期コンテキストに自動注入される。

- MCPの外側で動作するため、MCPのプッシュ制約を回避できる
- LLMのツール呼び出し判断に依存しない
- ユーザー操作不要（セッション開始時に自動発火）

---

## 2. アーキテクチャ

### 2.1 全体フロー

```
ユーザーが新セッション開始
       │
       ▼
┌─────────────────────────┐
│ Claude Code SessionStart │
│ hook 発火               │
└────────┬────────────────┘
         │ shell command 実行
         ▼
┌─────────────────────────┐
│ session_hook.py          │
│ (軽量スクリプト)          │
│                          │
│ 1. context.json を読む   │
│    （前セッションの残存）  │
│ 2. 鮮度チェック           │
│    → 古すぎればDB検索     │
│ 3. 人間可読テキストに整形  │
│ 4. stdout に出力          │
└─────────┬───────────────┘
          │ stdout
          ▼
┌─────────────────────────┐
│ Claude Code             │
│ セッション初期コンテキスト │
│ として自動注入            │
└─────────────────────────┘
```

### 2.2 デーモンとの関係

| コンポーネント | 役割 | タイミング |
|--------------|------|-----------|
| デーモン (context_daemon.py) | 記憶の「準備」— DBポーリング→一時ファイル書き出し | 常時（MCPサーバーと同時起動） |
| フックスクリプト (session_hook.py) | 記憶の「配達」— 一時ファイル or DB → stdout → Claude | セッション開始時のみ |

### 2.3 context.json の永続化（Phase 6 からの変更点）

**変更: デーモン/MCPサーバー終了時に context.json を削除しない。**

Phase 6 の現行実装では、デーモン終了時の `_cleanup` と MCPサーバーの `_cleanup_daemon` で context.json を削除している。本設計ではこの削除を廃止し、context.json を**セッションをまたいで残存させる**。

**理由:**

SessionStart hook はMCPサーバー・デーモンの起動より先に発火する。前セッション終了時に context.json を削除すると、新セッション開始時に読めるファイルがなく、デーモンが感情ベース検索で選別したコンテキストが活用できない。

**context.json のライフサイクル（変更後）:**

```
セッション1: デーモン起動 → メモリ検知のたびに上書き → セッション終了
                                                       ↓
             context.json は残る（最後の記憶状態のスナップショット）
                                                       ↓
セッション2: SessionStart hook が読む → 前セッションの記憶が届く
              → デーモン再起動 → 新しいメモリで上書き → ...
```

**削除しないことの安全性:**

| 懸念 | 対処 |
|------|------|
| ファイルが古すぎる | `updated_at` を確認し、閾値（デフォルト24時間）超過ならDBフォールバック |
| ディスク容量 | 数KB（JSON 5件程度）。問題にならない |
| OS再起動で消える | tmpdir なので自然消滅。消えてもDBフォールバックがある |
| セキュリティ | 既存の `create_secure_tmpdir` で `0o700` + シンボリックリンク検出済み |

**必要なコード変更:**

| ファイル | 変更内容 |
|---------|---------|
| `src/context_daemon.py` | `_cleanup` メソッドから context.json の削除処理を除去 |
| `src/mcp_server.py` | `_cleanup_daemon` メソッドから context.json の削除処理を除去 |

### 2.4 データソースの優先順位

フックスクリプトは以下の優先順位でデータを取得する:

```
1. context.json（前セッションのデーモンが残したスナップショット）
   ├─ 存在し、鮮度OK（24時間以内） → そのまま使用（感情ベース検索済みの良質なコンテキスト）
   ├─ 存在するが古い（24時間超過） → DBフォールバック
   └─ 存在しない → DBフォールバック

2. DBフォールバック（最新N件を id DESC で取得）
   └─ DB接続失敗 → 空出力（exit 0）
```

context.json が利用できる場合、デーモンが感情ベクトル＋シーンタグで検索した結果（単なる最新N件より関連性が高い）が届く。フォールバックは最新N件のrecency重視であり、context.json が使えるケースとは質が異なる。

### 2.5 CLAUDE.md動的書き込みを採用しない理由

- ユーザーの設定ファイルを自動プログラムが書き換えるのは侵襲的
- CLAUDE.md はユーザーが管理するファイルであり、自動書き込みは「嫌がらせに近い」
- hooks の stdout 注入はセッション内の一時的なコンテキストであり、ファイルを汚さない

---

## 3. フックスクリプト仕様

### 3.1 ファイル: `src/session_hook.py`

amygdalaプロジェクト内に配置する。単体実行可能なスクリプト。

```
python C:/claude_pj/amygdala/src/session_hook.py [--db-path PATH] [--max-memories N] [--max-age-hours H]
```

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--db-path` | (後述の優先順位で解決) | DBファイルの絶対パス |
| `--max-memories` | 5 | 取得するメモリの最大件数 |
| `--max-age-hours` | 24 | context.json の鮮度閾値（時間） |

**重要: このスクリプトは stdlib のみで自己完結する。** amygdala の src パッケージ（Config, DatabaseManager 等）には一切依存しない。理由:
- フックは任意のカレントディレクトリから直接実行される（`python path/to/session_hook.py`）
- 相対import（`from .config import ...`）は直接実行では動作しない
- `-m` 形式（`python -m src.session_hook`）はCWDがプロジェクトルートでないと失敗する
- 実際に必要な処理は `json`, `sqlite3`, `tempfile`, `getpass`, `argparse` のみで完結する

### 3.2 context.json のパス発見

context.json のパスは `context_daemon.py` の `create_secure_tmpdir()` と同じロジックで算出する:

```python
import tempfile, getpass, os
context_file = os.path.join(tempfile.gettempdir(), f"amygdala_{getpass.getuser()}", "context.json")
```

ロジックが単純（3行）なので、`create_secure_tmpdir()` を import せず複製する。stdlib のみの自己完結方針を維持するため。

### 3.3 処理フロー

```
1. context.json の存在・鮮度チェック
   ├─ 存在し、updated_at が閾値以内
   │   → ファイルから recalled_memories を取得
   └─ それ以外
       → DBフォールバック:
         a. DB接続（パス解決はセクション3.6参照）
         b. 最新N件のメモリを id DESC で取得
         c. 結果を構築

2. 結果を人間可読テキストに整形

3. stdout に出力（Claude Code が自動注入）
```

### 3.4 出力フォーマット

Claude のコンテキストに注入されるため、LLMが理解しやすい自然言語テキストとする。

```
[amygdala: 前回の記憶コンテキスト]

最終更新: 2026-03-12 07:08 JST
データソース: context.json（感情ベース検索）

関連する記憶:
1. (2026-03-11) 自動コンテキストデーモンの実機テストで、MCPサーバー起動時の
   _start_daemonのtime.sleep(0.3)+即死チェックがmcp.run()の開始を遅延させ...
   [感情: trust=0.6, importance=0.7]

2. (2026-03-11) デーモンの実機テスト中。新しいメモリを保存してデーモンが検知するか確認する。
   [感情: trust=0.5, anticipation=0.4]

3. ...

このコンテキストはamygdala感情記憶システムにより自動生成されました。
ユーザーの過去の会話や記憶に関する発言には、recall_memoriesで追加検索してください。
```

データソース行で `context.json（感情ベース検索）` か `DB直接検索（最新N件）` かを明示する。LLMがコンテキストの質を判断する材料になる。

### 3.5 DBフォールバック検索ロジック

context.json が利用できない場合（存在しない、古すぎる）:

1. `memories` テーブルから最新N件（デフォルト5件）を `id DESC` で取得
2. 各メモリの content, timestamp, 感情ベクトルを抽出
3. 人間可読テキストに整形

感情ベースの類似検索は行わない（「前セッションの続き」が目的であり、最新記憶の提示で十分）。

### 3.6 DB_PATH の解決

フックスクリプトは任意のカレントディレクトリから起動される。`Config.from_env()` のデフォルト DB_PATH は `memory.db`（相対パス）であり、カレントディレクトリ次第で意図しないDBを参照する、またはDB自体が見つからない。

**解決策: 優先順位付きのパス解決**

```
1. --db-path コマンドライン引数（最優先）
2. 環境変数 EMS_DB_PATH
3. デフォルト: amygdalaプロジェクトルートからの相対パス
   （session_hook.py の __file__ から逆算）
```

hook の command 設定例では `--db-path` で絶対パスを明示指定することを推奨する。

### 3.7 エラーハンドリング

フックスクリプトは**絶対にセッション開始をブロックしてはならない**。

- context.json 読み込みエラー → DBフォールバックへ
- DB接続失敗 → 空出力（exit 0）
- 例外 → stderr にログ、stdout は空（exit 0）
- タイムアウト対策: DB操作に3秒のタイムアウトを設定

exit code 2 を返すと Claude Code がエラーとして扱うため、常に exit 0 で終了する。

### 3.8 パフォーマンス要件

SessionStart hook はセッション開始をブロックするため、高速であること。

- 目標: 500ms以内
- context.json 読み込み: ~10ms
- DBフォールバック: ~100-300ms（SQLite直接クエリ）
- Python起動オーバーヘッド: ~200ms

---

## 4. Claude Code hook 設定

### 4.1 設定ファイル

ユーザーレベル（全プロジェクト共通）:
```
~/.claude/settings.json
```

またはプロジェクトレベル:
```
C:\claude_pj\amygdala\.claude\settings.json
```

### 4.2 設定内容

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "python C:/claude_pj/amygdala/src/session_hook.py --db-path C:/claude_pj/amygdala/memory.db"
          }
        ]
      }
    ]
  }
}
```

**注意**: `--db-path` に絶対パスを指定すること。フックは任意のカレントディレクトリから起動されるため、相対パスでは正しいDBを参照できない（セクション3.5参照）。

### 4.3 matcher について

- `""` (空文字): 全てのSessionStartサブイベントで発火
- SessionStart の matcher は `"startup"`, `"resume"`, `"compact"` 等のサブイベントにマッチ可能
- **初期実装では `"startup"` のみを推奨**。resume/compact 時は既にコンテキストがセッション内に存在するため、再注入すると重複のリスクがある。運用実績を見てから対象を拡大する

### 4.4 ユーザーレベル vs プロジェクトレベル

| 配置場所 | メリット | デメリット |
|---------|---------|-----------|
| ユーザーレベル (`~/.claude/settings.json`) | どのプロジェクトでもamygdalaの記憶が使える | 他プロジェクトでもフックが発火する |
| プロジェクトレベル (`.claude/settings.json`) | amygdalaプロジェクト内のみ | 他プロジェクトでは記憶が届かない |

**推奨**: ユーザーレベルに配置。amygdalaは「LLMの記憶システム」であり、特定プロジェクトに閉じるべきではない。

---

## 5. 既存コードへの影響

### 5.1 新規ファイル

| ファイル | 内容 |
|---------|------|
| `src/session_hook.py` | SessionStart hook 用スクリプト。単体実行可能 |

### 5.2 変更するファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/context_daemon.py` | `_cleanup` から context.json 削除処理を除去 |
| `src/mcp_server.py` | `_cleanup_daemon` から context.json 削除処理を除去 |

### 5.3 変更しないファイル

- `src/config.py` — 変更なし（必要に応じて環境変数でDB_PATHを指定）
- その他全ファイル — 変更なし

フックスクリプトはデーモンの出力（context.json）を読む消費者であり、amygdala本体のコア機能には手を入れない。

---

## 6. 実装ステップ

### Step 1: context.json 永続化（既存コード変更）
- `src/context_daemon.py` の `_cleanup` から context.json 削除処理を除去
- `src/mcp_server.py` の `_cleanup_daemon` から context.json 削除処理を除去
- 既存テストが壊れないことを確認

### Step 2: フックスクリプト本体
- `src/session_hook.py` を新規作成
- context.json 読み込み + 鮮度チェック
- DBフォールバック（最新N件取得）
- DB_PATH の優先順位付き解決（--db-path > EMS_DB_PATH > __file__逆算）
- 人間可読テキスト整形・stdout出力
- エラーハンドリング（絶対にブロックしない、常にexit 0）
- 単体テスト:
  - context.json 存在＋鮮度OK→テキスト整形の正常系
  - context.json 存在＋古すぎ→DBフォールバックの正常系
  - context.json 不在→DBフォールバックの正常系
  - DB接続失敗→空出力 + exit 0
  - 出力フォーマットの検証（ヘッダー、データソース表示、記憶項目、フッター）

### Step 3: Claude Code hook 設定
- `~/.claude/settings.json` にフック設定を追加（matcher: `"startup"` のみ）
- 動作確認（新セッション開始→記憶が自動注入されるか）

### Step 4: 結合テスト・調整
- デーモンが残した context.json での動作確認（感情ベース検索コンテキスト）
- context.json 不在時のDBフォールバック動作確認
- 出力テキストの可読性調整
- パフォーマンス計測（500ms以内目標）

---

## 7. 将来の拡張

### 7.1 UserPromptSubmit hook との連携

SessionStart に加え、`UserPromptSubmit` hook でも毎ターンのコンテキスト注入が可能。
ただし毎ターン発火はパフォーマンスコストが高いため、初期実装ではSessionStartのみとする。

### 7.2 コンテキストの選択的注入

現在は最新メモリを一律注入するが、将来的には:
- ユーザーの最初の発言を解析して関連記憶を選択的に注入
- UserPromptSubmit hook でユーザー入力を受け取り、それに基づくrecall

### 7.3 MCP仕様の進化

MCP に自動リソース購読やサーバー→クライアントプッシュが実装された場合、
フックスクリプトを廃止してMCPネイティブな方式に移行できる。
フックスクリプトを独立した消費者として設計しているため、移行時にamygdala本体への影響はない。

---

## 8. 割り切りポイント

| 項目 | 判断 | 理由 |
|------|------|------|
| context.json を削除しない | 前セッションのスナップショットとして次セッションで活用。tmpdir にあるためOS再起動で自然消滅。能動的に削除する必要がない |
| 鮮度閾値24時間 | 24時間以上前のコンテキストは状況が変わっている可能性が高い。DBフォールバック（最新N件）の方が適切 |
| SessionStart のみ（毎ターンではない） | セッション開始時に前回の記憶を提示すれば、LLMは文脈を継続できる。毎ターン注入は過剰 |
| startup のみ（resume/compact は後日） | resume/compact は既にコンテキストがある状態。再注入による重複リスクを避け、運用実績を見てから拡大 |
| フォールバックは最新N件のみ（感情検索なし） | フォールバックは「前セッションの続き」が目的。recency が最重要。感情類似性は二次的 |
| exit 0 固定 | フックがセッション開始を阻害するのは本末転倒。記憶が取れなくても会話はできる |
| Python起動オーバーヘッド許容 | ~200ms。コンパイル言語にする価値はない |
