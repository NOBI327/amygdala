#!/usr/bin/env python3
"""
Amygdala フィードバック判定精度テストツール

使い方:
  1. recall_log を CSV でエクスポート:
     sqlite3 -header -csv amygdala.db "SELECT * FROM recall_log" > recall_log.csv

  2. ラベリング実行:
     python label_tool.py recall_log.csv

  3. 出力:
     - recall_log_labeled.csv (ラベル付きデータ)
     - accuracy_report.txt (精度レポート)
"""

import csv
import sys
import os
from datetime import datetime
from collections import Counter


def load_recall_log(filepath):
    """recall_log CSV を読み込み"""
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    return records


def load_memory_content(db_path, memory_id):
    """memory_id から記憶の content を取得 (sqlite3 が使える場合)"""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT content, raw_input FROM memories WHERE id = ?", (memory_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0] or row[1] or "(内容不明)"
        return "(記憶が見つかりません)"
    except Exception:
        return "(DB接続不可)"


def label_session(records, db_path=None):
    """対話形式で各 recall にゴールドラベルを付与"""
    labeled = []

    print("\n" + "=" * 60)
    print("  Amygdala フィードバック判定 手動ラベリング")
    print("=" * 60)
    print(f"\n  対象: {len(records)} 件の recall ログ")
    print("  各 recall に対して以下を入力してください:\n")
    print("    u = used    (ユーザーがこの記憶を実際に使った)")
    print("    n = unused  (ユーザーがこの記憶を無視した)")
    print("    ? = neutral (判定不能)")
    print("    s = skip    (この件はスキップ)")
    print("    q = quit    (ここまでで終了)")
    print("-" * 60)

    for i, record in enumerate(records):
        memory_id = record.get("memory_id", "?")
        recalled_at = record.get("recalled_at", "?")
        was_used = record.get("was_used", "?")
        dominant = record.get("dominant_emotion", "?")
        scene = record.get("context_scene", "?")

        # DB があれば記憶の内容も表示
        content = ""
        if db_path:
            content = load_memory_content(db_path, memory_id)

        print(f"\n[{i+1}/{len(records)}]")
        print(f"  memory_id:  {memory_id}")
        print(f"  recalled:   {recalled_at}")
        print(f"  dominant:   {dominant}")
        print(f"  scene:      {scene}")
        print(f"  sys判定:    {'used' if was_used in ('1', 'True', 'true') else 'unused'}")
        if content:
            # 長すぎる場合は切り詰め
            display = content[:120] + "..." if len(content) > 120 else content
            print(f"  内容:       {display}")

        while True:
            answer = input("  あなたの判定 [u/n/?/s/q]: ").strip().lower()
            if answer in ("u", "n", "?", "s", "q"):
                break
            print("  → u, n, ?, s, q のいずれかを入力してください")

        if answer == "q":
            print("\n  ラベリングを終了します。")
            break
        if answer == "s":
            continue

        label_map = {"u": "used", "n": "unused", "?": "neutral"}
        human_label = label_map[answer]

        record_copy = dict(record)
        record_copy["human_label"] = human_label
        record_copy["system_label"] = "used" if was_used in ("1", "True", "true") else "unused"
        labeled.append(record_copy)

    return labeled


def generate_report(labeled):
    """精度レポートを生成"""
    if not labeled:
        return "ラベル付きデータがありません。"

    total = len(labeled)
    match = sum(1 for r in labeled if r["human_label"] == r["system_label"])
    neutral_count = sum(1 for r in labeled if r["human_label"] == "neutral")
    non_neutral = [r for r in labeled if r["human_label"] != "neutral"]

    # neutral を除外した精度
    if non_neutral:
        non_neutral_match = sum(1 for r in non_neutral if r["human_label"] == r["system_label"])
        accuracy_strict = non_neutral_match / len(non_neutral)
    else:
        accuracy_strict = 0.0

    # 全体精度 (neutral は「正解なし」扱い)
    accuracy_all = match / total if total > 0 else 0.0

    # 混同行列
    confusion = {
        ("used", "used"): 0,
        ("used", "unused"): 0,
        ("unused", "used"): 0,
        ("unused", "unused"): 0,
    }
    for r in non_neutral:
        key = (r["system_label"], r["human_label"])
        if key in confusion:
            confusion[key] += 1

    # 感情カテゴリ別の精度
    by_emotion = {}
    for r in non_neutral:
        emo = r.get("dominant_emotion", "unknown")
        if emo not in by_emotion:
            by_emotion[emo] = {"total": 0, "correct": 0}
        by_emotion[emo]["total"] += 1
        if r["human_label"] == r["system_label"]:
            by_emotion[emo]["correct"] += 1

    lines = []
    lines.append("=" * 60)
    lines.append("  Amygdala フィードバック判定 精度レポート")
    lines.append(f"  生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  総 recall 件数:       {total}")
    lines.append(f"  うち neutral 判定:    {neutral_count}")
    lines.append(f"  評価対象 (used/unused): {len(non_neutral)}")
    lines.append("")
    lines.append(f"  全体一致率:           {accuracy_all:.1%} ({match}/{total})")
    lines.append(f"  neutral除外精度:      {accuracy_strict:.1%} ({non_neutral_match if non_neutral else 0}/{len(non_neutral)})")
    lines.append("")
    lines.append("  混同行列 (system → human):")
    lines.append(f"                    human=used  human=unused")
    lines.append(f"    sys=used        {confusion[('used','used')]:>5}       {confusion[('used','unused')]:>5}")
    lines.append(f"    sys=unused      {confusion[('unused','used')]:>5}       {confusion[('unused','unused')]:>5}")
    lines.append("")

    if by_emotion:
        lines.append("  感情カテゴリ別精度:")
        for emo, stats in sorted(by_emotion.items(), key=lambda x: -x[1]["total"]):
            acc = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
            lines.append(f"    {emo:<16} {acc:.0%} ({stats['correct']}/{stats['total']})")
        lines.append("")

    # 改善ポイントの自動検出
    lines.append("  改善ポイント:")
    fp = confusion[("used", "unused")]  # システムが used と言ったが実際は unused
    fn = confusion[("unused", "used")]  # システムが unused と言ったが実際は used
    if fp > fn:
        lines.append(f"    → False Positive 多 ({fp}件): システムが「使われた」と過剰判定する傾向")
        lines.append("      対策: 明示的参照の閾値を上げる or 暗黙判定を保守的に")
    elif fn > fp:
        lines.append(f"    → False Negative 多 ({fn}件): システムが「使われてない」と誤判定する傾向")
        lines.append("      対策: 暗黙的参照（同一主題の継続）の検知を強化")
    else:
        lines.append("    → FP/FN が均等。特定の偏りなし。")

    if neutral_count > total * 0.4:
        lines.append(f"    → neutral 比率が高い ({neutral_count}/{total}): 判定が曖昧なケースが多い")
        lines.append("      対策: Phase 2 暗黙フィードバック判定の精度向上が優先")

    lines.append("")
    lines.append("-" * 60)
    return "\n".join(lines)


def save_labeled_csv(labeled, output_path):
    """ラベル付き CSV を保存"""
    if not labeled:
        return
    fieldnames = list(labeled[0].keys())
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(labeled)


def main():
    if len(sys.argv) < 2:
        print("使い方: python label_tool.py <recall_log.csv> [amygdala.db]")
        print("")
        print("  recall_log.csv: recall_log テーブルの CSV エクスポート")
        print("  amygdala.db:    (任意) 記憶内容を表示するためのDBパス")
        sys.exit(1)

    csv_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(csv_path):
        print(f"エラー: {csv_path} が見つかりません")
        sys.exit(1)

    records = load_recall_log(csv_path)
    if not records:
        print("recall_log が空です。テストを実行してからエクスポートしてください。")
        sys.exit(1)

    print(f"\n  {len(records)} 件の recall ログを読み込みました。")

    labeled = label_session(records, db_path)

    if labeled:
        # CSV 保存
        output_csv = csv_path.replace(".csv", "_labeled.csv")
        save_labeled_csv(labeled, output_csv)
        print(f"\n  ラベル付きデータ保存: {output_csv}")

        # レポート生成
        report = generate_report(labeled)
        report_path = csv_path.replace(".csv", "_report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

        print(f"  精度レポート保存:     {report_path}")
        print("\n" + report)
    else:
        print("\n  ラベル付きデータがありません。")


if __name__ == "__main__":
    main()
