#!/usr/bin/env python3
"""
多轮测试结果汇总器
扫描当前目录下所有 results_*.json → 生成汇总表格 (Markdown) + 各版本提示词

用法:
  python generate_summary.py                        # 汇总当前目录所有 results_*.json
  python generate_summary.py --dir ./results_v2/    # 指定目录
  python generate_summary.py --output SUMMARY.md    # 指定输出
"""

import json
import os
import argparse
from pathlib import Path
from datetime import datetime


def find_result_files(directory: str, fixed: bool = False) -> list:
    """扫描目录下所有 results_*.json，按时间戳排序。fixed=True时只取_fixed.json"""
    pattern = "results_*_fixed.json" if fixed else "results_*.json"
    files = sorted(Path(directory).glob(pattern))
    if not fixed:
        files = [f for f in files if "_summary" not in f.name and "_fixed" not in f.name]
    return files


def build_table_rows(result_files: list) -> list:
    """从每个 JSON 提取汇总行"""
    rows = []
    for fp in result_files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, KeyError):
            print(f"  [跳过] {fp.name} — JSON 格式错误")
            continue

        meta = data.get("meta", {})
        details = data.get("details", [])

        if not details:
            print(f"  [跳过] {fp.name} — 无测试数据")
            continue

        # 各类别分数
        by_cat = {}
        for r in details:
            by_cat.setdefault(r["category"], []).append(r["score"])

        cat_cols = {}
        for cat in ["A", "B", "C", "D", "E"]:
            scores = by_cat.get(cat, [])
            if scores:
                cat_cols[cat] = f"{sum(scores)/len(scores):.3f}"
            else:
                cat_cols[cat] = "-"

        avg_score = sum(r["score"] for r in details) / len(details)

        rows.append({
            "timestamp": meta.get("timestamp", "unknown"),
            "timestamp_display": meta.get("timestamp_display", meta.get("timestamp", "")),
            "tag": meta.get("tag", "-"),
            "total": len(details),
            "avg_score": avg_score,
            "cat_a": cat_cols["A"],
            "cat_b": cat_cols["B"],
            "cat_c": cat_cols["C"],
            "cat_d": cat_cols["D"],
            "cat_e": cat_cols["E"],
            "prompt_md5": meta.get("prompt_md5", "-"),
            "prompt": meta.get("prompt", ""),
            "port": meta.get("port", ""),
            "temperature": meta.get("temperature", ""),
            "filename": fp.name,
        })

    # 按时间排序
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def generate_summary_md(rows: list, output_path: str):
    """生成 Markdown 汇总文件"""
    lines = []
    sep = "---"
    is_fixed = "fixed" in output_path or "_fixed" in rows[0]["filename"] if rows else False
    suffix = " (新评估器 v2)" if is_fixed else ""

    lines.append(f"# 机器狗任务规划 — 提示词版本测试汇总{suffix}")
    lines.append(f"")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 总版本数: {len(rows)}")
    lines.append(f"")

    # ============ 一、汇总表格 ============
    lines.append("## 一、各版本分数汇总")
    lines.append("")
    lines.append(
        "| 时间 | 标签 | 题数 | 平均分 | A类 | B类 | C类 | D类 | E类 | 提示词MD5 | 备注 |"
    )
    lines.append(
        "|------|------|------|--------|-----|-----|-----|-----|-----|-----------|------|"
    )

    for r in rows:
        ts_short = r["timestamp_display"][:16] if r["timestamp_display"] else r["timestamp"]
        note = f"port={r['port']} T={r['temperature']}" if r.get("port") else ""
        if "_fixed" in r["filename"]:
            note += " ✦新评估器" if note else "✦新评估器"
        lines.append(
            f"| {ts_short} | {r['tag']} | {r['total']} | {r['avg_score']:.4f} | "
            f"{r['cat_a']} | {r['cat_b']} | {r['cat_c']} | {r['cat_d']} | {r['cat_e']} | "
            f"{r['prompt_md5']} | {note} |"
        )

    lines.append("")
    lines.append(f"*满分参考：离线 gold-mock 自测平均分 = 1.0000*")
    lines.append("")
    lines.append("")

    # ============ 二、各版本提示词全文 ============
    lines.append("## 二、各版本提示词全文")
    lines.append("")

    for i, r in enumerate(rows):
        lines.append(f"### {i+1}. {r['tag']}  ({r['timestamp']})")
        lines.append(f"")
        lines.append(f"- MD5: `{r['prompt_md5']}`")
        lines.append(f"- 平均分: {r['avg_score']:.4f}")
        lines.append(f"- JSON: `{r['filename']}`")
        lines.append(f"")
        lines.append("```")
        lines.append(r["prompt"])
        lines.append("```")
        lines.append("")
        lines.append(sep)
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n汇总已保存: {output_path}")
    print(f"共 {len(rows)} 个版本的测试结果")


def main():
    parser = argparse.ArgumentParser(description="多轮测试结果汇总器")
    parser.add_argument("--dir", default="results", help="结果 JSON 所在目录")
    parser.add_argument("--output", default="summary/SUMMARY.md", help="输出 Markdown 文件路径")
    parser.add_argument("--fixed", action="store_true", help="使用新评估器重评分的 _fixed.json 文件")
    args = parser.parse_args()

    files = find_result_files(args.dir, fixed=args.fixed)
    if not files:
        print("未找到任何 results_*.json 文件")
        return

    print(f"找到 {len(files)} 个结果文件:")
    for fp in files:
        print(f"  - {fp.name}")

    rows = build_table_rows(files)
    generate_summary_md(rows, args.output)


if __name__ == "__main__":
    main()
