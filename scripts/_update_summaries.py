#!/usr/bin/env python3
"""Regenerate summary TXT files from _fixed.json with corrected scores."""
import json, re
from pathlib import Path
from collections import defaultdict

base = Path(__file__).parent.parent
results_dir = base / "results"

for fp in sorted(results_dir.glob('results_*_fixed.json')):
    with open(fp, encoding='utf-8') as f:
        data = json.load(f)

    meta = data['meta']
    details = data['details']
    tag = meta.get('tag', 'unknown')
    orig_json = meta.get('json_file', fp.name.replace('_fixed.json', '.json'))

    # Summary path in results dir
    summary_path = results_dir / orig_json.replace('.json', '_summary.txt')

    # Build per-category stats
    cats = defaultdict(list)
    for r in details:
        cat = r['test_id'].split('-')[1][0]
        cats[cat].append(r['score'])

    total_avg = sum(r['score'] for r in details) / len(details)

    prompt_text = meta.get('prompt', '')
    if not prompt_text:
        prompt_text = "(提示词未在 JSON 中存档)"

    lines = []
    lines.append("=" * 72)
    lines.append("  机器狗任务规划测试 — 单轮结果（修正评分）")
    lines.append("=" * 72)
    lines.append(f"  时间:         {meta.get('timestamp_display', meta.get('timestamp',''))}")
    lines.append(f"  提示词标签:   {tag}")
    lines.append(f"  提示词 MD5:   {meta.get('prompt_md5','')}")
    lines.append(f"  模型端口:     {meta.get('port','')}")
    lines.append(f"  温度:         {meta.get('temperature','')}")
    lines.append(f"  总题数:       {len(details)}")
    lines.append(f"  JSON 文件:    {orig_json}")
    lines.append(f"  修正 JSON:    {fp.name}")
    lines.append("")
    lines.append("-" * 56)
    lines.append("  【一、分数汇总（修正后）】")
    lines.append("-" * 56)
    lines.append(f"  总分平均(旧): {meta.get('avg_score', 0):.4f}")
    lines.append(f"  总分平均(新): {total_avg:.4f}")
    lines.append(f"  变化:         {total_avg - meta.get('avg_score', 0):+.4f}")
    lines.append("")

    for cat in ['A', 'B', 'C', 'D', 'E']:
        scores = cats.get(cat, [])
        if scores:
            avg = sum(scores) / len(scores)
            lines.append(f"  {cat}类 ({len(scores)}题):  平均 {avg:.4f}  最低 {min(scores):.4f}  最高 {max(scores):.4f}")

    lines.append("")
    lines.append("-" * 56)
    lines.append("  【修正说明】")
    lines.append("-" * 56)
    lines.append("  - 解析器正则修复：支持同一行多动作格式（原贪婪匹配吞后续动作）")
    lines.append("  - 目标归一化：NAVIGATE|门口↔NAVIGATE|大门 等同义表述归一化匹配")
    lines.append("  - 分支路径评分：E类/C类分支题取最优分支路径匹配")
    lines.append("")

    if prompt_text:
        lines.append("-" * 56)
        lines.append("  【二、使用的提示词内容（原文存档）】")
        lines.append("-" * 56)
        lines.append(prompt_text)
        lines.append("")

    lines.append("=" * 72)
    lines.append("  详细每题的修正得分与模型输出请查看同名的 JSON 文件")
    lines.append("  修正工具: python reevaluate_all.py")
    lines.append("  摘要更新: python _update_summaries.py")
    lines.append("=" * 72)

    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"{summary_path.name}: {meta.get('avg_score',0):.4f} -> {total_avg:.4f} ({total_avg-meta.get('avg_score',0):+.4f})")
