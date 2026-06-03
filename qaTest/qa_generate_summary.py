#!/usr/bin/env python3
"""
Q&A 评测 — 多轮结果累积汇总器
扫描 outputs/ 下所有 qa_results_*.json → 生成 Markdown 汇总表格

用法:
  python qa_generate_summary.py                          # 扫描 outputs/
  python qa_generate_summary.py --dir ./outputs/         # 指定目录
  python qa_generate_summary.py --output QASUMMARY.md    # 指定输出
"""

import json
import argparse
from pathlib import Path
from datetime import datetime


DIMENSION_NAMES = {
    "D1": "任务拆解",
    "D2": "时序理解",
    "D3": "物体/动作依赖",
    "D4": "场景/条件判断",
    "D5": "安全与社交",
}


def find_result_files(directory: str) -> list:
    """扫描目录下所有 qa_results_*.json"""
    files = sorted(Path(directory).glob("qa_results_*.json"))
    files = [f for f in files if "_summary" not in f.name]
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
            print(f"  [跳过] {fp.name} — 无数据")
            continue

        dim_scores = meta.get("dimension_scores", {})

        row = {
            "timestamp": meta.get("timestamp", "unknown"),
            "timestamp_display": meta.get("timestamp_display", meta.get("timestamp", "")),
            "tag": meta.get("tag", "-"),
            "total": meta.get("total_questions", len(details)),
            "overall_accuracy": meta.get("overall_accuracy", 0),
            "d1_acc": dim_scores.get("D1", {}).get("accuracy", 0),
            "d1_n": dim_scores.get("D1", {}).get("total", 0),
            "d2_acc": dim_scores.get("D2", {}).get("accuracy", 0),
            "d2_n": dim_scores.get("D2", {}).get("total", 0),
            "d3_acc": dim_scores.get("D3", {}).get("accuracy", 0),
            "d3_n": dim_scores.get("D3", {}).get("total", 0),
            "d4_acc": dim_scores.get("D4", {}).get("accuracy", 0),
            "d4_n": dim_scores.get("D4", {}).get("total", 0),
            "d5_acc": dim_scores.get("D5", {}).get("accuracy", 0),
            "d5_n": dim_scores.get("D5", {}).get("total", 0),
            "prompt_md5": meta.get("prompt_md5", "-"),
            "prompt": meta.get("prompt", ""),
            "port": meta.get("port", ""),
            "temperature": meta.get("temperature", ""),
            "filename": fp.name,
        }
        rows.append(row)

    rows.sort(key=lambda r: r["timestamp"])
    return rows


def generate_summary_md(rows: list, output_path: str):
    """生成 Markdown 汇总文件"""
    lines = []
    sep = "---"

    lines.append("# 机器狗 Q&A 诊断评测 — 多轮结果汇总")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 总版本数: {len(rows)}")
    lines.append(f"> 评测维度: D1(任务拆解) D2(时序理解) D3(物体/动作依赖) D4(场景/条件判断) D5(安全与社交)")
    lines.append("")

    # ============ 一、汇总表格 ============
    lines.append("## 一、各版本分数汇总")
    lines.append("")
    lines.append(
        "| 时间 | 标签 | 题数 | 总准确率 | D1 任务拆解 | D2 时序理解 | D3 物体依赖 | D4 场景判断 | D5 安全社交 | MD5 | 备注 |"
    )
    lines.append(
        "|------|------|------|----------|-------------|-------------|-------------|-------------|-------------|-----|------|"
    )

    for r in rows:
        ts_short = r["timestamp_display"][:16] if r["timestamp_display"] else r["timestamp"]
        note_parts = []
        if r.get("port"):
            note_parts.append(f"port={r['port']}")
        if r.get("temperature"):
            note_parts.append(f"T={r['temperature']}")
        note = " ".join(note_parts)

        lines.append(
            f"| {ts_short} | {r['tag']} | {r['total']} | {r['overall_accuracy']:.4f} | "
            f"{r['d1_acc']:.4f} | {r['d2_acc']:.4f} | {r['d3_acc']:.4f} | "
            f"{r['d4_acc']:.4f} | {r['d5_acc']:.4f} | "
            f"{r['prompt_md5']} | {note} |"
        )
    lines.append("")
    lines.append(f"*满分参考：离线 gold 自测准确率 = 1.0000*")
    lines.append(f"*随机猜测基线 (4选1): ~25%*")
    lines.append("")
    lines.append("")

    # ============ 二、维度对比柱状图（文本版） ============
    lines.append("## 二、各版本维度对比")
    lines.append("")
    for r in rows:
        lines.append(f"### {r['tag']} ({r['timestamp_display'][:16]})")
        lines.append(f"总准确率: {r['overall_accuracy']:.4f}")
        lines.append("```")
        for dim, name in DIMENSION_NAMES.items():
            acc = r.get(f"{dim.lower()}_acc", 0)
            bar_len = int(acc * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            lines.append(f"  {dim} {name:<14} {bar} {acc:.4f}")
        lines.append("```")
        lines.append("")
    lines.append("")

    # ============ 三、各版本提示词全文 ============
    lines.append("## 三、各版本提示词全文")
    lines.append("")

    for i, r in enumerate(rows):
        lines.append(f"### {i+1}. {r['tag']}  ({r['timestamp']})")
        lines.append(f"")
        lines.append(f"- MD5: `{r['prompt_md5']}`")
        lines.append(f"- 总准确率: {r['overall_accuracy']:.4f}")
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
    parser = argparse.ArgumentParser(description="Q&A 评测多轮汇总器")
    parser.add_argument("--dir", default="outputs", help="结果 JSON 所在目录")
    parser.add_argument("--output", default="QASUMMARY.md", help="输出 Markdown 路径")
    args = parser.parse_args()

    files = find_result_files(args.dir)
    if not files:
        print(f"在 {args.dir}/ 中未找到 qa_results_*.json 文件")
        return

    print(f"找到 {len(files)} 个结果文件:")
    for fp in files:
        print(f"  - {fp.name}")

    rows = build_table_rows(files)
    if not rows:
        print("无有效数据可汇总")
        return

    generate_summary_md(rows, args.output)


if __name__ == "__main__":
    main()
