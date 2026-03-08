"""感情メモリシステム — 初回セットアップ

全MCP機能の説明を表示し、一度の確認で全機能を許可リストに追加する。
Claude Codeのセッション開始前に1回だけ実行すればOK。
"""

import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SETTINGS_PATH = os.path.join(".claude", "settings.local.json")

EMOTION_MEMORY_TOOLS = {
    "mcp__emotion-memory__store_memory": (
        "記憶の保存",
        "テキストに感情タグ（joy, trust等10軸）を付与してDBに保存する。"
    ),
    "mcp__emotion-memory__recall_memories": (
        "記憶の検索",
        "感情ベースでDBから関連する記憶を検索・取得する。"
    ),
    "mcp__emotion-memory__get_stats": (
        "統計情報",
        "記憶の総数、感情分布、多様性指数などのシステム統計を返す。"
    ),
    "mcp__emotion-memory__pin_memory": (
        "ピン固定",
        "重要な情報をワーキングメモリにピン止めする（最大3スロット）。"
    ),
    "mcp__emotion-memory__unpin_memory": (
        "ピン解除",
        "ピンを解除し、長期記憶へ移管する。"
    ),
    "mcp__emotion-memory__list_pinned_memories": (
        "ピン一覧",
        "現在ピン止めされている記憶の一覧とTTL残数を表示する。"
    ),
}


def main():
    print("=" * 60)
    print("  感情メモリシステム (Emotion Memory) — 機能一覧")
    print("=" * 60)
    print()

    for i, (tool_id, (name, desc)) in enumerate(EMOTION_MEMORY_TOOLS.items(), 1):
        print(f"  {i}. {name}")
        print(f"     {desc}")
        print()

    print("-" * 60)
    print("  上記の全機能をClaude Codeで確認なしに使用できるようにします。")
    print("  （設定ファイル: .claude/settings.local.json に書き込み）")
    print("-" * 60)
    print()

    answer = input("全機能を許可しますか？ [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("キャンセルしました。")
        sys.exit(0)

    # 既存設定を読み込み
    settings = {"permissions": {"allow": []}}
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)

    allow_list = settings.setdefault("permissions", {}).setdefault("allow", [])

    added = []
    for tool_id in EMOTION_MEMORY_TOOLS:
        if tool_id not in allow_list:
            allow_list.append(tool_id)
            added.append(tool_id)

    # 書き込み
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    print()
    if added:
        print(f"{len(added)}件の機能を許可リストに追加しました:")
        for tool_id in added:
            name, _ = EMOTION_MEMORY_TOOLS[tool_id]
            print(f"  + {name}")
    else:
        print("全機能はすでに許可済みです。")

    print()
    print("セットアップ完了。Claude Codeを起動してください。")


if __name__ == "__main__":
    main()
