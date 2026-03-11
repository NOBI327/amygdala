# 自動コンテキスト更新デーモン — 設計書

> amygdala MCPモードにおける「言われなくても思い出す」機能の実装仕様。

---

## 1. 背景と課題

### 1.1 問題

amygdalaには2つの動作モードがある。

| モード | recall（記憶検索） | store（記憶保存） |
|--------|-------------------|-------------------|
| CLIモード（`process_turn`） | 毎ターン自動 | ワーキングメモリ溢れ時に自動 |
| MCPモード | LLMが呼ぶと判断した時のみ | LLMが呼ぶと判断した時のみ |

MCPモードでは、LLM（Claude Code等）がツールを呼ぶかどうかの判断に依存するため、CLAUDE.mdにルールを書いても発火が保証されない。結果として「言われなければ思い出せない」状態になる。

### 1.2 MCPの構造的制約

- MCPツールは受動的。LLM側が「呼ぶ」と判断しない限り動かない
- MCPサーバーから会話に割り込む手段がない
- ツール呼び出しを強制するMCPの仕組みは存在しない

### 1.3 方針

MCPの外側に**薄い常駐プロセス（デーモン）**を置き、CLIモードと同等の自動recall機能をMCPモードでも実現する。amygdala本体の責務（記憶の保存・検索・グラフ管理）は変えない。

### 1.4 割り切りポイント: `get_active_context` の発火保証

本設計では `get_active_context` MCPツールを追加するが、これもLLMが「呼ぶ」と判断しない限り動かない。つまりセクション1.1で指摘した構造的問題は完全には解消されない。

ただし、改善は明確にある:
- **Before**: 複数ツール（recall_memories, store_memory等）をLLMが状況判断して呼び分ける必要があった
- **After**: 「毎ターン `get_active_context` を1つ呼ぶ」という単純なルールに集約される

判断の複雑さが大幅に下がることで、CLAUDE.mdルールの発火率は確実に上がる。完全自動化（LLM判断を一切介さない）はMCPプロトコルの制約上不可能であり、これは現実的な最善策として割り切る。

---

## 2. 設計判断の根拠

| 判断 | 理由 |
|------|------|
| amygdala本体のDBスキーマ変更なし | `memories`テーブルを監視対象とするため、既存スキーマで完結。既存テスト207件に影響なし |
| session_id不要 | 長期記憶（`memories`テーブル）はセッション横断。同一ユーザーが設計/実装を別セッションで行う利用想定のため、最新の感情コンテキストが共有されても問題ない |
| プロセス間通信に一時ファイルを使用 | 実装が最も単純。速度も実用上十分（OSファイルキャッシュ）。コンテキストファイルは1つのみ |
| DB案（`active_context`テーブル）を不採用 | SQLiteの同時書き込みロック問題。コンテキストは揮発性データであり、DBに永続化する必要がない |
| 共有メモリ案を不採用 | 可変長JSONデータの扱いが煩雑。実装の複雑さに対してメリットが薄い |
| 名前付きパイプ案を不採用 | ストリーム型通信は「最新値を読む」ユースケースに不向き。クロスプラットフォーム対応も面倒 |
| デーモンはMCPサーバーと同時起動 | 別起動だとユーザーの手動操作が必要。MCPサーバーはクライアント設定で自動起動されるため、相乗りが最も確実 |
| デーモンはSearchEngineを薄いインターフェース経由で呼ぶ | 直接依存を避け、将来的なプロセス分離に備える（セクション3.4参照） |
| デーモンはスレッドではなくサブプロセスとして起動 | 既存の`DatabaseManager`はスレッドセーフ設計ではない（`check_same_thread=False`未設定）。サブプロセスなら別のDB接続を開くため安全 |

---

## 3. アーキテクチャ

### 3.1 全体構成

```
┌─────────────────┐
│  LLMクライアント  │  (Claude Code, 自前CLI等)
└────────┬────────┘
         │ MCP protocol
         ▼
┌─────────────────────────────────┐
│  amygdala MCPサーバー            │
│  ┌───────────┐  ┌─────────────┐ │
│  │ MCPツール群 │  │  デーモン    │ │
│  │(既存9種+1) │  │(サブプロセス)│ │
│  └───────────┘  └──────┬──────┘ │
│         │               │        │
│         ▼               ▼        │
│  ┌─────────────┐  ┌──────────┐  │
│  │ memory.db   │  │ tmpファイル │  │
│  │(変更なし)    │  │ (1ファイル)│  │
│  └─────────────┘  └──────────┘  │
└─────────────────────────────────┘
```

### 3.2 デーモンの責務（2つだけ）

**責務1: 初期発火チェック**
- MCPサーバー起動時にデーモンも起動
- amygdalaのDB接続・状態を確認
- 問題があればログ出力（致命的エラーでない限りMCPサーバーは動作継続）

**責務2: コンテキスト自動更新**
- `memories` テーブルを定期的に監視
- 新しいレコードがINSERTされたことを検知
- そのレコードの感情ベクトル・シーンタグを取得し、`memories`テーブル全体を検索
- 検索結果を一時ファイルに書き出す

### 3.3 デーモンが持たないもの

- 応答生成ロジック（Frontmanの責務）
- 感情タグ付けロジック（Backmanの責務）
- グラフ処理ロジック（RelationalGraphEngineの責務）

デーモンは**検知 → 検索 → 書き出し**のみを行うトリガー係。

### 3.4 検索インターフェース層

デーモンは `SearchEngine.search_memories()` を直接呼ばず、薄いインターフェース関数を経由する。

デーモン起動時に `SearchEngine` インスタンスを1回だけ生成し、ポーリングループ内では使い回す。毎回インスタンス化しない理由は、`SearchEngine` が現状ステートレスであり、生成コストを無駄に払う必要がないため。将来プロセス分離する際は `recall_for_context` をRPC呼び出しに差し替えるだけで対応可能。

```python
from src.config import Config
from src.db import DatabaseManager
from src.search_engine import SearchEngine

class ContextDaemon:
    def __init__(self, config: Config, db: DatabaseManager):
        self.config = config
        self.db = db
        self._engine = SearchEngine(config, db)  # 起動時に1回だけ生成

    def recall_for_context(self, emotion_vec: dict, scenes: list,
                           top_k: int) -> list[dict]:
        """デーモン用の検索インターフェース。
        内部でSearchEngineを使うが、呼び出し側はSearchEngineを知らない。
        scenes はパース済みのリスト（json.loads適用済み）を期待する。
        戻り値はSearchEngine.search_memoriesの出力をそのまま返す。"""
        return self._engine.search_memories(emotion_vec, scenes, top_k=top_k)
```

- デーモンはこのメソッドのみに依存する
- `SearchEngine` のコンストラクタは `(config: Config, db_manager: DatabaseManager)` の2引数を要求するため、`config` も渡す
- `scenes` 引数はパース済みリストを期待する（DBから取得した JSON 文字列は呼び出し側で `json.loads()` すること）
- 戻り値は `search_memories` の出力そのまま（変換はしない）。形式はセクション4.3参照

---

## 4. 一時ファイル仕様

### 4.1 ファイルパス

```
{tmpdir}/amygdala_{user}/context.json
```

- `{tmpdir}`: OS標準の一時ディレクトリ（Python `tempfile.gettempdir()`）
  - Linux/macOS: `/tmp`
  - Windows: `C:\Users\{user}\AppData\Local\Temp`
- `{user}`: 実行ユーザー名（`getpass.getuser()`）
- コンテキストファイルは1つのみ（session_id不要のため）

### 4.2 ディレクトリセキュリティ

マルチユーザー環境でのシンボリックリンク攻撃を防ぐため、ディレクトリ作成時にパーミッションを制限する。

```python
import os
import sys
import tempfile
import getpass

def create_secure_tmpdir() -> str:
    """ユーザー固有の安全な一時ディレクトリを作成する。"""
    base = os.path.join(tempfile.gettempdir(), f"amygdala_{getpass.getuser()}")
    os.makedirs(base, mode=0o700, exist_ok=True)

    # 既存ディレクトリがシンボリックリンクでないことを確認
    if os.path.islink(base):
        raise RuntimeError(f"Symlink detected at {base}, refusing to use")

    # パーミッションが正しいことを確認（POSIX環境のみ）
    # Windowsでは mode=0o700 が無視されるが、
    # AppData\Local\Temp 自体がユーザー固有ディレクトリのため
    # 他ユーザーからのアクセスリスクは低い
    if sys.platform != "win32":
        stat = os.stat(base)
        if stat.st_mode & 0o077:
            os.chmod(base, 0o700)

    return base
```

- `0o700`: オーナーのみ読み書き実行可能（POSIX環境で有効）
- シンボリックリンク検出: 他ユーザーが先にリンクを仕込むことへの防御
- Windows環境: `mode=0o700` はOS側で無視されるが、`AppData\Local\Temp` がユーザー固有のディレクトリであるため実質的なリスクは低い。ACL設定等の追加対策は不要と判断

### 4.3 ファイル形式

`recalled_memories` の各要素は `SearchEngine.search_memories()` の戻り値フォーマットに準拠する。

```json
{
  "updated_at": "2026-03-12T14:30:00+09:00",
  "source_memory_id": 42,
  "trigger_emotion": {
    "joy": 0.1,
    "sadness": 0.0,
    "anger": 0.0,
    "fear": 0.2,
    "surprise": 0.0,
    "disgust": 0.0,
    "trust": 0.5,
    "anticipation": 0.3,
    "importance": 0.7,
    "urgency": 0.2
  },
  "trigger_scenes": ["work", "development"],
  "recalled_memories": [
    {
      "id": 15,
      "content": "...",
      "score": 0.82,
      "emotion": {
        "joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
        "surprise": 0.0, "disgust": 0.0, "trust": 0.6, "anticipation": 0.0,
        "importance": 0.3, "urgency": 0.0
      },
      "scenes": ["work"],
      "timestamp": "2026-03-10T10:00:00",
      "pinned_flag": false,
      "recall_count": 3,
      "relevance_score": 1.1
    }
  ]
}
```

- `source_memory_id`: トリガーとなった`memories`テーブルの主キー（`id`, AUTOINCREMENT）
- `trigger_emotion`: 検索に使用した感情ベクトル（感情8軸 + メタ2軸の計10軸）
- `trigger_scenes`: 検索に使用したシーンタグ（トリガーmemoryの`scenes`カラムから取得）
- `recalled_memories`: `SearchEngine.search_memories()` の戻り値をそのまま格納。各メモリの `emotion` は常に全10軸（感情8軸+メタ2軸）を含む

### 4.4 排他制御（アトミックリネーム）

デーモンの書き込みとMCPツールの読み込みが競合してJSONパースエラーになることを防ぐため、アトミックリネームパターンを採用する。

```
書き込み手順:
  1. 一時ファイルに書き出す: context.json.tmp
  2. 書き込み完了後、os.replace() でリネーム:
     context.json.tmp → context.json
```

- POSIX環境: `os.replace()` はアトミック操作。読み込み側は常に完全なJSONを取得できる
- Windows環境: `os.replace()` は置換先が他プロセスに開かれている場合に `PermissionError` になる可能性がある。ただし、読み込み側（MCPツール）はファイルを短時間だけ開いて即閉じるため、競合確率は極めて低い。万一の `PermissionError` はデーモン側でキャッチし、次のポーリングループで再試行する（エラーハンドリングに包含される）
- ファイルロックによるデッドロックリスクを回避

### 4.5 ライフサイクル

- **作成**: デーモンが初回検索結果を書き出した時
- **更新**: `memories`テーブルに新レコードが入るたびにアトミックリネームで上書き
- **削除**: MCPサーバープロセス終了時にクリーンアップ（best effort）
- OS再起動でも消える（tmpdir）

---

## 5. 監視メカニズム

### 5.1 ポーリング方式

デーモンは `memories` テーブルを定期ポーリングして新規INSERTを検知する。

```
ループ:
  1. memories テーブルの最新レコードID（主キー: id）を取得
  2. 前回チェック時のIDと比較
  3. 変更があれば:
     a. 最新レコードから感情ベクトル（joy〜urgency の10軸）と
        シーンタグ（scenes: JSON文字列 → json.loads() でリストに変換）を取得
     b. recall_for_context() で上位N件を検索（N = DAEMON_RECALL_TOP_K）
     c. 結果をアトミックリネームで一時ファイルに書き出す
  4. スリープ（ポーリング間隔: 設定可能、デフォルト2秒）
```

### 5.2 ポーリング間隔

- デフォルト: 2秒
- `Config` で設定可能にする（`DAEMON_POLL_INTERVAL_SEC`）
- 会話のターン間隔（人間の入力速度）を考えると2秒で十分

### 5.3 ポーリングのスケーラビリティと将来の移行パス

現在のポーリング方式は大半が無駄打ち（実際に変更がある瞬間は全体の1%未満）。SQLiteの読み取り自体は軽量なので現時点では問題にならないが、将来的な改善として`memories`テーブルにINSERTトリガーでフラグを立てる方式を検討できる。

```sql
-- 将来の移行例
CREATE TABLE IF NOT EXISTS memory_change_flag (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_memory_id INTEGER DEFAULT 0
);

CREATE TRIGGER IF NOT EXISTS memories_insert_flag
AFTER INSERT ON memories
BEGIN
    INSERT OR REPLACE INTO memory_change_flag (id, last_memory_id)
    VALUES (1, NEW.id);
END;
```

この場合、デーモンは `memory_change_flag` の1行のみをチェックし、変更があったときだけ検索を実行する。現在の設計ではこのトリガーは実装しないが、移行を阻害しない構造にしておく。

### 5.4 検索件数の設定

recalled_memoriesの件数は `Config.DAEMON_RECALL_TOP_K` で設定可能にする。

- デフォルト: 5件
- `Config.TOP_K_RESULTS`（CLIモード/MCPツールの`recall_memories`で使用）とは独立した設定値
- **将来的な改善案**: importanceやurgencyの値に応じて件数を動的に変える、またはスコア閾値カットオフ（例: `score > 0.3` のみ）を併用する。ただし初期実装では固定値で開始し、運用実績を見て調整する

### 5.5 エラーハンドリング

デーモンが停止すると「言われなくても思い出す」機能が完全に沈黙するため、フェイルセーフな設計とする。

**基本方針**: プロセスを落とさず、次のポーリングループへ進む。

```
エラー発生時:
  1. エラーログ出力（logger.warning）
  2. error_count をインクリメント
  3. 連続エラー時は Exponential Backoff でポーリング間隔を拡大
     - 間隔 = min(base_interval * 2^error_count, max_interval)
     - max_interval: 60秒（DAEMON_MAX_BACKOFF_SEC、設定可能）
  4. 正常復帰したら error_count をリセット、間隔を元に戻す
```

**対象エラー例**:
- DB接続エラー（ファイルロック、ディスクI/O）
- 検索エンジンの例外
- 一時ファイル書き込み失敗（権限、ディスク容量、Windows `PermissionError`）

---

## 6. プロセス管理

### 6.1 起動

MCPサーバー起動時にデーモンを**サブプロセス**として起動する。起動方式は `subprocess.Popen` を使用する。

```python
import subprocess
import sys

daemon_process = subprocess.Popen(
    [sys.executable, "-m", "src.context_daemon",
     "--db-path", config.DB_PATH],
    # 標準出力/エラーをログファイルまたはPIPEに接続
)
```

スレッドではなくサブプロセスを選択する理由:
- 既存の `DatabaseManager` はスレッドセーフ設計ではない（SQLiteの `check_same_thread=False` 未設定）
- サブプロセスなら独立したDB接続を開くため、スレッドセーフ問題を回避できる
- MCPサーバーとデーモンのライフサイクルを独立して管理できる

`multiprocessing.Process` ではなく `subprocess.Popen` を選択する理由:
- Windows環境で `multiprocessing` は `if __name__ == "__main__"` ガードが必須であり、MCPサーバーの起動フロー（`mcp_server.py` の `run()` メソッド内）との整合が複雑になる
- `subprocess` ならデーモンのエントリーポイントが明確に分離され、単体テスト・単体起動も容易

### 6.2 サブプロセス初期化

デーモンのサブプロセスは起動時に独立した環境を構築する。

```python
# context_daemon.py のエントリーポイント（__main__ブロック）
def main(db_path: str | None = None):
    config = Config.from_env()  # 環境変数から設定読み込み
    # DB_PATH優先順位: コマンドライン引数 > config.DB_PATH（環境変数 EMS_DB_PATH またはデフォルト）
    effective_db_path = db_path or config.DB_PATH
    db = DatabaseManager(effective_db_path)
    db.init()  # 独立したDB接続を開く
    daemon = ContextDaemon(config, db)
    daemon.run()  # ポーリングループ開始
```

- `Config.from_env()` で設定を読み込む（親プロセスから引数で渡さない）
- `DatabaseManager` は新しいインスタンスを生成し、独立したSQLite接続を開く
- DB_PATH優先順位: `--db-path` コマンドライン引数 > `config.DB_PATH`（環境変数 `EMS_DB_PATH` またはデフォルト `memory.db`）

### 6.3 ゾンビプロセス対策

MCPサーバーがクラッシュした場合、デーモンがバックグラウンドで生き残り続ける（ゾンビ化する）リスクがある。

**対策**: デーモン側で親プロセスの生存確認を行う。プラットフォームごとに方式を分ける。

```python
import os
import sys

def is_parent_alive(original_ppid: int) -> bool:
    """親プロセスの生存確認。プラットフォーム別に実装。"""
    if sys.platform == "win32":
        # Windowsでは ppid が変わらないケースがあるため、
        # 直接PIDの生存を確認する
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, original_ppid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            # ctypes失敗時のフォールバック
            try:
                os.kill(original_ppid, 0)
                return True
            except OSError:
                return False
    else:
        # Linux/macOS: ppidが変わったら親は死んでいる
        return os.getppid() == original_ppid
```

- チェック頻度: ポーリングループの各イテレーション（追加コストはほぼゼロ）
- Windows: `ctypes` で `OpenProcess` を試み、失敗時は `os.kill(pid, 0)` にフォールバック
- Linux/macOS: `os.getppid()` で親PIDの変化を検出（initに引き取られたら自身も終了）

### 6.4 終了

MCPサーバーの正常終了時: `daemon_process.terminate()` でデーモンに停止を通知する。

- POSIX環境: `SIGTERM` が送信される。デーモン側でシグナルハンドラを設定し、クリーンアップ（一時ファイル削除）を実行
- Windows環境: `TerminateProcess` が呼ばれる。この場合デーモン側の `atexit` ハンドラは**発火しない**（即座にプロセスが強制終了されるため）。そのため、MCPサーバー側の `cleanup_daemon()` で一時ファイルの削除も行う

```python
# mcp_server.py 終了時
import atexit
import os

def cleanup_daemon():
    if daemon_process and daemon_process.poll() is None:
        daemon_process.terminate()
        daemon_process.wait(timeout=5)
    # Windows環境ではデーモンのatexitが発火しないため、
    # MCPサーバー側で一時ファイルを削除する
    context_file = os.path.join(tmpdir, "context.json")
    try:
        os.remove(context_file)
    except FileNotFoundError:
        pass

atexit.register(cleanup_daemon)
```

---

## 7. MCPツール追加

### 7.1 `get_active_context`

既存ツールに1つ追加。デーモンが書き出した一時ファイルを読み、現在のアクティブコンテキストを返す。

```
引数:
  なし

戻り値:
  - recalled_memories: List[Dict]  (上位N件、SearchEngine.search_memoriesの出力形式)
  - updated_at: str (最終更新日時)
  - trigger_emotion: Dict (検索に使った感情ベクトル、感情8軸+メタ2軸)
  - trigger_scenes: List[str] (検索に使ったシーンタグ)
  - source_memory_id: int (トリガーとなったmemoryのID)
```

ファイルが存在しない場合（デーモン未起動、初回store前等）は空リストを返す。

---

## 8. 既存コードへの影響

### 8.1 変更が必要なファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/config.py` | `DAEMON_POLL_INTERVAL_SEC` (デフォルト2), `DAEMON_MAX_BACKOFF_SEC` (デフォルト60), `DAEMON_RECALL_TOP_K` (デフォルト5) 追加。`from_env()` でも環境変数（`EMS_DAEMON_POLL_INTERVAL` 等）から読み込めるようにする |
| `src/mcp_server.py` | `get_active_context` ツール追加、デーモン起動処理（`subprocess.Popen`）追加、`atexit` でデーモン停止処理追加、`store_memory` の `scenes` INSERT漏れ修正（既存バグ） |

### 8.2 新規ファイル

| ファイル | 内容 |
|---------|------|
| `src/context_daemon.py` | デーモン本体（検索インターフェース層含む）。単体起動可能（モジュールパスは `pyproject.toml` のパッケージ構成に合わせて調整） |

### 8.3 変更しないファイル

- `src/db.py` — 変更なし（DBスキーマ変更なし）
- `src/backman.py` — 変更なし
- `src/frontman.py` — 変更なし
- `src/memory_system.py` — 変更なし（CLIモードのパイプラインは触らない）
- `src/search_engine.py` — 変更なし（デーモンからインターフェース経由で呼ぶだけ）
- `src/relational_graph.py` — 変更なし

### 8.4 既存バグ修正（デーモン実装の前提条件）

`src/mcp_server.py` の `store_memory` メソッドにおいて、`scenes` パラメータの処理が欠落している。具体的には:
- `scenes_input` パラメータのパース処理（JSON文字列/リスト対応、最大3件制限）が未実装
- パース結果の `scenes` カラムへのDB INSERT が未実装

CLIモード（`memory_system.py`）では正しくパースおよびINSERTされている。デーモンが `memories` テーブルの `scenes` カラムに依存するため、デーモン実装前にこのバグを修正する必要がある。

---

## 9. 実装ステップ

### Step 0: 既存バグ修正
- `src/mcp_server.py` の `store_memory` メソッドで `scenes_input` のパース処理（JSON文字列/リスト対応、最大3件制限）を追加
- パース結果を `scenes` カラムとしてDBにINSERTするよう修正
- 修正後、MCPモード経由で保存したメモリのシーンタグが正しく保存されることを確認

### Step 1: 基盤
- `src/config.py` に `DAEMON_POLL_INTERVAL_SEC` (デフォルト2), `DAEMON_MAX_BACKOFF_SEC` (デフォルト60), `DAEMON_RECALL_TOP_K` (デフォルト5) 追加
- `Config.from_env()` にデーモン用環境変数の読み込みを追加
- 既存テストが壊れないことを確認

### Step 2: デーモン本体
- `src/context_daemon.py` を新規作成
- `__main__` ブロック（`python -m src.context_daemon --db-path ...` で単体起動可能）
- サブプロセス初期化（`Config.from_env()` + 独立した `DatabaseManager`）
- 検索インターフェース層（`recall_for_context`）
- `memories`テーブルのポーリングループ
- アトミックリネーム書き出し、クリーンアップ
- ディレクトリセキュリティ（`create_secure_tmpdir`）
- エラーハンドリング（Exponential Backoff）
- ゾンビプロセス対策（プラットフォーム別親PID監視）
- 単体テスト

### Step 3: MCP統合
- `src/mcp_server.py` にデーモン起動処理（`subprocess.Popen`）を追加
- `atexit` でデーモン停止・クリーンアップ処理を追加
- `get_active_context` ツールを追加（`@self.mcp.tool()` デコレータ、既存パターンに準拠）
- 統合テスト
