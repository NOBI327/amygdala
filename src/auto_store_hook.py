"""Stop hook スクリプト。

Claude Code の Stop hook から呼び出され、
会話内容を自動的に amygdala の記憶として保存する。

依存: stdlib のみ（json, sqlite3, tempfile, getpass, argparse）。
amygdala の src パッケージには一切依存しない。

使用例:
  echo '{"session_id":"abc","transcript_path":"/tmp/transcript.jsonl"}' | \
    python C:/claude_pj/amygdala/src/auto_store_hook.py --db-path C:/claude_pj/amygdala/memory.db
"""

import argparse
import getpass
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

# Windows で stdout が CP932 になる問題を回避
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# キーワード辞書
# ---------------------------------------------------------------------------

EMOTION_KEYWORDS = [
    "思う", "思った", "感じ", "好き", "嫌い", "嫌だ",
    "困った", "困って", "嬉しい", "嬉し", "辛い", "辛く",
    "迷う", "迷って", "悩む", "悩んで", "不安", "心配",
    "楽しい", "楽し", "怖い", "怖く", "疲れ", "ストレス",
    "悔しい", "寂しい", "驚い", "ショック", "感動",
    "ありがとう", "すまん", "ごめん", "申し訳",
]

DECISION_KEYWORDS = [
    "決めた", "決めよう", "やめる", "やめよう", "やめた",
    "方針", "方向性", "計画", "進める", "進めよう",
    "やろう", "やっていこう", "行こう", "始めよう",
    "採用", "却下", "選ぶ", "選んだ",
    "変更", "切り替え", "移行", "リファクタ",
    "優先", "後回し", "保留",
]

QUESTION_KEYWORDS = [
    "？", "?", "どう思う", "どうする", "どうしよう",
    "相談", "アドバイス", "意見", "提案",
    "教えて", "わからない", "わからん",
]

# scenes 推定用キーワード
SCENE_KEYWORDS = {
    "work": ["仕事", "タスク", "プロジェクト", "デプロイ", "リリース", "PR", "コミット",
             "実装", "設計", "バグ", "テスト", "コード", "レビュー", "ミーティング"],
    "hobby": ["趣味", "ゲーム", "アニメ", "映画", "音楽", "バイブコーディング",
              "個人プロジェクト", "遊び", "楽しむ"],
    "health": ["体調", "睡眠", "疲れ", "休む", "健康", "運動", "ストレス"],
    "learning": ["学ぶ", "勉強", "調べ", "理解", "なるほど", "知らなかった"],
    "relationship": ["子供", "家族", "友達", "同僚", "チーム"],
    "philosophy": ["哲学", "意味", "本質", "なぜ", "考え方", "価値観", "AI"],
    "daily": ["今日", "明日", "昨日", "週末", "朝", "夜", "食事"],
}


# ---------------------------------------------------------------------------
# Transcript パース
# ---------------------------------------------------------------------------

def read_hook_input() -> dict:
    """stdin から hook 入力 JSON を読み取る。"""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def parse_transcript(transcript_path: str) -> list[dict]:
    """JSONL transcript ファイルをパースしてメッセージリストを返す。"""
    messages = []
    if not transcript_path or not os.path.exists(transcript_path):
        return messages
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    messages.append(msg)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return messages


def extract_text_content(msg: dict) -> str:
    """メッセージから純粋なテキストコンテンツを抽出する。

    content が文字列の場合はそのまま返す。
    content がリスト（content blocks）の場合は type=text のみ結合する。
    """
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
    return ""


def has_tool_use(msg: dict) -> bool:
    """メッセージにツール呼び出しが含まれるか判定する。"""
    content = msg.get("content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
    return "tool_use" in msg


def extract_dialogue_pairs(messages: list[dict], start_index: int) -> list[dict]:
    """transcript からユーザー+アシスタント対話ペアを抽出する。

    start_index 以降の新しいメッセージのみ処理する。
    Returns:
        対話ペアのリスト: [{"user": str, "assistant": str, "index": int}, ...]
    """
    pairs = []
    i = start_index
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        if role == "user":
            user_text = extract_text_content(msg)
            # 次のアシスタント応答を探す
            assistant_text = ""
            assistant_has_only_tools = True
            j = i + 1
            while j < len(messages):
                next_msg = messages[j]
                next_role = next_msg.get("role", "")
                if next_role == "assistant":
                    text = extract_text_content(next_msg)
                    if text.strip():
                        assistant_text += text + "\n"
                        assistant_has_only_tools = False
                    elif not has_tool_use(next_msg):
                        assistant_has_only_tools = False
                    j += 1
                elif next_role == "user":
                    break
                else:
                    j += 1

            if user_text.strip():
                pairs.append({
                    "user": user_text.strip(),
                    "assistant": assistant_text.strip(),
                    "assistant_has_only_tools": assistant_has_only_tools and not assistant_text.strip(),
                    "end_index": j,
                })
            i = j
        else:
            i += 1

    return pairs


# ---------------------------------------------------------------------------
# 重複防止
# ---------------------------------------------------------------------------

def get_tracking_dir() -> str:
    """トラッキングファイルのディレクトリパスを返す。"""
    d = os.path.join(
        tempfile.gettempdir(),
        f"amygdala_{getpass.getuser()}",
    )
    os.makedirs(d, exist_ok=True)
    return d


def get_last_processed(session_id: str) -> int:
    """最後に処理した transcript 行数を取得する。"""
    path = os.path.join(get_tracking_dir(), f"last_processed_{session_id}.txt")
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_last_processed(session_id: str, index: int) -> None:
    """最後に処理した transcript 行数を保存する。"""
    path = os.path.join(get_tracking_dir(), f"last_processed_{session_id}.txt")
    try:
        with open(path, "w") as f:
            f.write(str(index))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 重要度フィルタ（緩め）
# ---------------------------------------------------------------------------

def contains_any(text: str, keywords: list[str]) -> bool:
    """テキストにキーワードのいずれかが含まれるか判定する。"""
    return any(kw in text for kw in keywords)


def is_significant(pair: dict) -> bool:
    """対話ペアが保存する価値があるか判定する（緩めフィルタ）。

    以下のいずれかに該当すれば True:
    1. ユーザー発話が一定以上の長さ（>= 30文字）
    2. 感情・意見キーワードを含む
    3. 意思決定キーワードを含む
    4. 質問・相談キーワードを含む
    5. ツール使用なしの対話ターン（純粋な会話）

    スキップ条件:
    - ユーザー発話が極端に短い（< 10文字）かつキーワードなし
    - アシスタント応答がツール呼び出しのみ
    """
    user_text = pair["user"]
    assistant_text = pair["assistant"]
    combined = user_text + " " + assistant_text

    # スキップ: アシスタントがツール呼び出しのみ、かつユーザーも短い指示
    if pair.get("assistant_has_only_tools") and len(user_text) < 30:
        return False

    # スキップ: ユーザー発話が極短かつキーワードなし
    if len(user_text) < 10:
        if not contains_any(combined, EMOTION_KEYWORDS + DECISION_KEYWORDS + QUESTION_KEYWORDS):
            return False

    # 長さベース
    if len(user_text) >= 30:
        return True

    # キーワードベース
    if contains_any(combined, EMOTION_KEYWORDS):
        return True
    if contains_any(combined, DECISION_KEYWORDS):
        return True
    if contains_any(combined, QUESTION_KEYWORDS):
        return True

    # 純粋な会話ターン（ツール使用なし）
    if not pair.get("assistant_has_only_tools") and assistant_text:
        return True

    return False


# ---------------------------------------------------------------------------
# 要約 & 感情推定
# ---------------------------------------------------------------------------

def truncate(text: str, max_len: int = 200) -> str:
    """テキストを max_len 文字に切り詰める。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def summarize_pair(pair: dict) -> str:
    """対話ペアを要約テキストにする。"""
    user_summary = truncate(pair["user"], 200)
    assistant_summary = truncate(pair["assistant"], 200)
    if assistant_summary:
        return f"User: {user_summary} → Assistant: {assistant_summary}"
    return f"User: {user_summary}"


def estimate_importance(pair: dict) -> float:
    """キーワードベースで importance を簡易推定する（0.0-1.0）。"""
    combined = pair["user"] + " " + pair["assistant"]
    score = 0.3  # ベースライン

    if contains_any(combined, DECISION_KEYWORDS):
        score += 0.3
    if contains_any(combined, EMOTION_KEYWORDS):
        score += 0.2
    if contains_any(combined, QUESTION_KEYWORDS):
        score += 0.1
    if len(pair["user"]) >= 50:
        score += 0.1

    return min(score, 1.0)


def estimate_urgency(pair: dict) -> float:
    """キーワードベースで urgency を簡易推定する（0.0-1.0）。"""
    combined = pair["user"] + " " + pair["assistant"]
    urgent_keywords = ["急ぎ", "今すぐ", "至急", "ASAP", "早く", "deadline", "期限"]
    if contains_any(combined, urgent_keywords):
        return 0.6
    return 0.2


def estimate_scenes(pair: dict) -> list[str]:
    """キーワードベースで scenes を推定する（最大3つ）。"""
    combined = pair["user"] + " " + pair["assistant"]
    matched = []
    for scene, keywords in SCENE_KEYWORDS.items():
        if contains_any(combined, keywords):
            matched.append(scene)
    return matched[:3] if matched else ["meta"]


# 感情8軸のキーワード辞書
EMOTION_AXIS_KEYWORDS = {
    "joy": ["嬉しい", "嬉し", "楽しい", "楽し", "よかった", "最高", "素晴らしい",
            "感動", "ありがとう", "完成", "成功", "うまくいった", "いいね", "良い"],
    "sadness": ["悲しい", "寂しい", "残念", "辛い", "辛く", "虚しい", "切ない",
                "落ち込", "がっかり", "悔しい"],
    "anger": ["怒り", "腹立", "イライラ", "ムカつ", "ふざけ", "ひどい",
              "許せ", "最悪", "くそ", "ダメ"],
    "fear": ["怖い", "怖く", "不安", "心配", "恐い", "恐れ", "ヤバい",
             "まずい", "危ない", "リスク"],
    "surprise": ["驚い", "びっくり", "まさか", "予想外", "意外", "ショック",
                 "え？", "えっ", "なんと", "知らなかった"],
    "disgust": ["嫌だ", "嫌い", "気持ち悪い", "うんざり", "飽き", "もういい"],
    "trust": ["信頼", "任せ", "大丈夫", "安心", "確実", "間違いない",
              "頼り", "信じ", "理解", "なるほど"],
    "anticipation": ["楽しみ", "期待", "待ち遠し", "ワクワク", "やろう",
                     "始めよう", "進めよう", "計画", "予定", "これから"],
}


def estimate_emotions(pair: dict) -> dict[str, float]:
    """キーワードベースで感情8軸を推定する（0.0-1.0）。"""
    combined = pair["user"] + " " + pair["assistant"]
    emotions = {}
    for axis, keywords in EMOTION_AXIS_KEYWORDS.items():
        matched_count = sum(1 for kw in keywords if kw in combined)
        if matched_count == 0:
            emotions[axis] = 0.0
        elif matched_count == 1:
            emotions[axis] = 0.4
        elif matched_count == 2:
            emotions[axis] = 0.6
        else:
            emotions[axis] = min(0.3 + matched_count * 0.2, 1.0)
    return emotions


# ---------------------------------------------------------------------------
# SQLite 書き込み
# ---------------------------------------------------------------------------

def resolve_db_path(cli_arg: str | None) -> str:
    """DB_PATH を優先順位付きで解決する。"""
    if cli_arg:
        return cli_arg
    env = os.environ.get("EMS_DB_PATH")
    if env:
        return env
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "memory.db")


def store_to_db(db_path: str, memories: list[dict]) -> int:
    """記憶を memories テーブルに直接 INSERT する。

    Returns:
        保存件数。
    """
    if not memories or not os.path.exists(db_path):
        return 0

    stored = 0
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        for mem in memories:
            try:
                emo = mem.get("emotions", {})
                conn.execute(
                    """INSERT INTO memories
                       (content, raw_input, joy, sadness, anger, fear,
                        surprise, disgust, trust, anticipation,
                        importance, urgency, scenes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mem["content"],
                        mem["raw_input"],
                        emo.get("joy", 0.0),
                        emo.get("sadness", 0.0),
                        emo.get("anger", 0.0),
                        emo.get("fear", 0.0),
                        emo.get("surprise", 0.0),
                        emo.get("disgust", 0.0),
                        emo.get("trust", 0.0),
                        emo.get("anticipation", 0.0),
                        mem["importance"],
                        mem["urgency"],
                        json.dumps(mem["scenes"], ensure_ascii=False),
                    ),
                )
                stored += 1
            except sqlite3.Error as e:
                print(f"[auto_store] INSERT error: {e}", file=sys.stderr)
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"[auto_store] DB error: {e}", file=sys.stderr)

    return stored


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def process(hook_input: dict, db_path_arg: str | None) -> int:
    """メインのロジック。テストから呼びやすいよう分離。

    Returns:
        保存件数。
    """
    session_id = hook_input.get("session_id", "unknown")
    transcript_path = hook_input.get("transcript_path", "")

    if not transcript_path:
        return 0

    # transcript パース
    messages = parse_transcript(transcript_path)
    if not messages:
        return 0

    # 重複防止: 前回処理位置から
    last_idx = get_last_processed(session_id)
    pairs = extract_dialogue_pairs(messages, last_idx)
    if not pairs:
        return 0

    # 最終処理位置を更新
    max_end = max(p["end_index"] for p in pairs)

    # フィルタ & 要約
    to_store = []
    for pair in pairs:
        if not is_significant(pair):
            continue

        raw_text = f"User: {pair['user']}\nAssistant: {pair['assistant']}"
        to_store.append({
            "content": summarize_pair(pair),
            "raw_input": raw_text[:500],
            "emotions": estimate_emotions(pair),
            "importance": estimate_importance(pair),
            "urgency": estimate_urgency(pair),
            "scenes": estimate_scenes(pair),
        })

    # DB 書き込み
    db_path = resolve_db_path(db_path_arg)
    stored = store_to_db(db_path, to_store)

    # トラッキング更新（保存有無に関わらず処理位置を進める）
    save_last_processed(session_id, max_end)

    return stored


def main() -> None:
    """エントリーポイント。常に exit 0 で終了する。"""
    try:
        parser = argparse.ArgumentParser(
            description="amygdala Stop hook: auto-store conversation memories"
        )
        parser.add_argument("--db-path", type=str, default=None)
        args = parser.parse_args()

        hook_input = read_hook_input()
        stored = process(hook_input, args.db_path)

        if stored > 0:
            print(f"[auto_store] Stored {stored} memories", file=sys.stderr)

    except Exception as e:
        print(f"[auto_store error] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
