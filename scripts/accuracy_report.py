#!/usr/bin/env python3
"""
フィードバック判定精度レポート自動生成スクリプト（標準ライブラリのみ）

CSVファイルを入力として混同行列・precision/recall/F1・改善ポイントを計算する。

usage:
    python scripts/accuracy_report.py <csv_file>

    csv_file: predicted_label と gold_label 列を含む CSV ファイル
              (例: label_tool.py の出力から system_label/human_label を
               predicted_label/gold_label にリネームしたもの、または直接同列名を使用)

期待する CSV 列:
    predicted_label  : システムが予測したラベル
    gold_label       : 人間が付与したゴールドラベル
    dominant_emotion : (任意) 感情カテゴリ別分析に使用
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def load_csv(filepath: str) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def detect_columns(rows: list[dict]) -> tuple[str, str]:
    """predicted_label/gold_label または system_label/human_label を自動検出"""
    if not rows:
        return ("predicted_label", "gold_label")
    sample = rows[0]
    if "predicted_label" in sample and "gold_label" in sample:
        return ("predicted_label", "gold_label")
    if "system_label" in sample and "human_label" in sample:
        return ("system_label", "human_label")
    raise ValueError(
        "CSV に predicted_label/gold_label または system_label/human_label 列が必要です"
    )


def compute_metrics(
    rows: list[dict], pred_col: str, gold_col: str
) -> dict:
    """全ラベルの precision/recall/F1 および混同行列を計算"""
    labels = sorted(set(r[gold_col] for r in rows) | set(r[pred_col] for r in rows))

    # 混同行列: confusion[gold][pred] = count
    confusion: dict[str, dict[str, int]] = {
        g: {p: 0 for p in labels} for g in labels
    }
    for row in rows:
        g = row[gold_col]
        p = row[pred_col]
        if g in confusion and p in labels:
            confusion[g][p] += 1

    # precision / recall / F1 per label
    per_label = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[g][label] for g in labels if g != label)
        fn = sum(confusion[label][p] for p in labels if p != label)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        per_label[label] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
        }

    # macro avg
    macro_p = sum(v["precision"] for v in per_label.values()) / len(labels)
    macro_r = sum(v["recall"] for v in per_label.values()) / len(labels)
    macro_f1 = sum(v["f1"] for v in per_label.values()) / len(labels)

    # accuracy
    correct = sum(1 for r in rows if r[pred_col] == r[gold_col])
    accuracy = correct / len(rows) if rows else 0.0

    return {
        "labels": labels,
        "confusion": confusion,
        "per_label": per_label,
        "macro": {"precision": macro_p, "recall": macro_r, "f1": macro_f1},
        "accuracy": accuracy,
        "total": len(rows),
        "correct": correct,
    }


def emotion_breakdown(rows: list[dict], pred_col: str, gold_col: str) -> dict:
    """dominant_emotion 列がある場合、感情カテゴリ別精度を集計"""
    if not rows or "dominant_emotion" not in rows[0]:
        return {}
    by_emo: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    for row in rows:
        emo = row.get("dominant_emotion", "unknown") or "unknown"
        by_emo[emo]["total"] += 1
        if row[pred_col] == row[gold_col]:
            by_emo[emo]["correct"] += 1
    return dict(by_emo)


def find_worst_label(per_label: dict) -> str | None:
    """F1が最も低いラベルを返す"""
    if not per_label:
        return None
    return min(per_label, key=lambda k: per_label[k]["f1"])


def format_report(metrics: dict, emo: dict, pred_col: str, gold_col: str) -> str:
    labels = metrics["labels"]
    per_label = metrics["per_label"]
    confusion = metrics["confusion"]
    macro = metrics["macro"]

    lines = []
    lines.append("=" * 64)
    lines.append("  Amygdala フィードバック判定精度レポート")
    lines.append(f"  生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  列: {pred_col} (予測) vs {gold_col} (正解)")
    lines.append("=" * 64)
    lines.append("")
    lines.append(f"  総件数:      {metrics['total']}")
    lines.append(f"  正解数:      {metrics['correct']}")
    lines.append(f"  Accuracy:    {metrics['accuracy']:.1%}")
    lines.append("")

    # 混同行列
    lines.append("  混同行列 (行=gold, 列=predicted):")
    header = "              " + "".join(f"  {p:<10}" for p in labels)
    lines.append(header)
    for g in labels:
        row_str = f"  gold={g:<8}" + "".join(
            f"  {confusion[g][p]:<10}" for p in labels
        )
        lines.append(row_str)
    lines.append("")

    # ラベル別指標
    lines.append("  ラベル別 Precision / Recall / F1:")
    lines.append(f"  {'Label':<12} {'Prec':>6} {'Recall':>7} {'F1':>6}")
    lines.append("  " + "-" * 36)
    for label in labels:
        m = per_label[label]
        lines.append(
            f"  {label:<12} {m['precision']:>6.1%} {m['recall']:>7.1%} {m['f1']:>6.1%}"
        )
    lines.append("  " + "-" * 36)
    lines.append(
        f"  {'macro avg':<12} {macro['precision']:>6.1%} {macro['recall']:>7.1%} {macro['f1']:>6.1%}"
    )
    lines.append("")

    # 感情カテゴリ別
    if emo:
        lines.append("  感情カテゴリ別精度:")
        for e, stats in sorted(emo.items(), key=lambda x: -x[1]["total"]):
            acc = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
            lines.append(f"    {e:<18} {acc:.0%} ({stats['correct']}/{stats['total']})")
        lines.append("")

    # 改善ポイント
    worst = find_worst_label(per_label)
    lines.append("  改善ポイント:")
    if worst:
        wm = per_label[worst]
        lines.append(f"    → 最低F1カテゴリ: '{worst}' (F1={wm['f1']:.1%})")
        if wm["fp"] > wm["fn"]:
            lines.append(f"      False Positive 多 ({wm['fp']}件): 過剰予測の傾向")
        elif wm["fn"] > wm["fp"]:
            lines.append(f"      False Negative 多 ({wm['fn']}件): 見逃しの傾向")
        else:
            lines.append("      FP/FN 均等。特定の偏りなし。")
    lines.append("")
    lines.append("-" * 64)
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0 if "--help" in sys.argv or "-h" in sys.argv else 1)

    csv_file = sys.argv[1]
    if not Path(csv_file).exists():
        print(f"エラー: ファイルが見つかりません: {csv_file}")
        sys.exit(1)

    rows = load_csv(csv_file)
    if not rows:
        print("CSV が空です。")
        sys.exit(1)

    pred_col, gold_col = detect_columns(rows)
    # neutral ラベルは precision/recall 計算から除外（ラベリング不能）
    eval_rows = [r for r in rows if r.get(gold_col) != "neutral"]
    if not eval_rows:
        print("評価可能な行がありません（全 neutral）。")
        sys.exit(1)

    metrics = compute_metrics(eval_rows, pred_col, gold_col)
    emo = emotion_breakdown(eval_rows, pred_col, gold_col)
    report = format_report(metrics, emo, pred_col, gold_col)

    print(report)


if __name__ == "__main__":
    main()
