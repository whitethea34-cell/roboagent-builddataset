#!/usr/bin/env python3
"""Generate gap analysis: model output vs gold standard.

Usage: python _gen_analysis.py [results_json]
"""
import json
import re
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))
from task_plan_evaluator import TaskPlanEvaluator

base = Path(__file__).parent.parent

json_path = sys.argv[1] if len(sys.argv) > 1 else None
if json_path and not Path(json_path).is_absolute():
    json_path = str(base / "results" / json_path)
elif not json_path:
    json_path = str(base / "results" / "results_v6-thinking_20260513_182959.json")

with open(json_path, encoding='utf-8') as f:
    results = json.load(f)
with open(base / "data" / "task_plan_testset.json", encoding='utf-8') as f:
    testset_raw = json.load(f)

gold_map = {c['test_id']: c for c in testset_raw}


def parse_actions_from_output(raw: str):
    pairs = []
    for m in re.finditer(r'(\d+)\.\s*([A-Z_]+)\|((?:(?!\d+\.\s*[A-Z_]+\|)[^|\n])+)', raw):
        pairs.append((m.group(2), m.group(3).strip()))
    return pairs


def extract_stages_from_output(raw: str):
    m = re.search(r'阶段[：:]\s*([^\n]+)', raw)
    if m:
        return [s.strip() for s in re.split(r'[→>、,;，；\s]+', m.group(1).strip()) if s.strip()]
    return []


SOCIAL_ACTIONS = {"SPEAK", "WAVE", "BOW", "NOD", "SHAKE", "SHAKE_HEAD"}
NAV_ACTIONS = {"NAVIGATE", "WALK", "TROT", "CLIMB", "AVOID", "RETURN"}
OBJ_ACTIONS = {"FIND", "CARRY", "DROP", "PUSH"}

report = []
case_scores = []

for r in sorted(results["details"], key=lambda x: x["test_id"]):
    test_id = r["test_id"]
    gold = gold_map.get(test_id)
    if not gold:
        continue

    raw = r.get("raw_output", "")
    pred_actions = parse_actions_from_output(raw)
    pred_stages = extract_stages_from_output(raw)
    score = r.get("score", 0)
    case_scores.append(score)

    gold_actions = [(a["action"], a["target"]) for a in gold["actions_gold"]]
    gold_stages = gold["stages_expected"]
    gold_hri = gold["hri_expected"]
    instr = gold["instruction"]

    pred_action_types = Counter(a[0] for a in pred_actions)
    gold_action_types = Counter(a[0] for a in gold_actions)
    missing_actions = gold_action_types - pred_action_types
    extra_actions = pred_action_types - gold_action_types

    pred_hri = "是" if "HRI：是" in raw or "HRI:是" in raw else ("否" if "HRI：否" in raw or "HRI:否" in raw else "?")

    notes = []
    if pred_hri != ("是" if gold_hri else "否"):
        notes.append(f"HRI判定错误(应为{'是'if gold_hri else '否'},预测{pred_hri})")
    if missing_actions:
        notes.append(f"缺失动作: {dict(missing_actions)}")
    if len(pred_stages) != len(gold_stages):
        notes.append(f"阶段数不匹配(gold={len(gold_stages)},pred={len(pred_stages)})")
    soc_in_pred = {a[0] for a in pred_actions if a[0] in SOCIAL_ACTIONS}
    soc_in_gold = {a[0] for a in gold_actions if a[0] in SOCIAL_ACTIONS}
    if gold_hri and soc_in_gold and not (soc_in_pred & soc_in_gold):
        notes.append(f"缺少社交动作: gold={soc_in_gold}")

    report.append({
        "test_id": test_id,
        "instruction": instr,
        "score": score,
        "notes": notes,
        "gold_stages": gold_stages,
        "pred_stages": pred_stages,
        "gold_actions": gold_actions,
        "pred_actions": pred_actions,
    })

lines = []
lines.append(f"# 模型输出分析报告")
lines.append(f"")
lines.append(f"> 来源: {Path(json_path).name}")
lines.append(f"> 总题数: {len(report)}, 平均分: {sum(case_scores)/len(case_scores):.4f}")
lines.append(f"")

# Summary table
lines.append(f"## 一、分数分布")
lines.append(f"")
high = sum(1 for s in case_scores if s >= 0.9)
mid = sum(1 for s in case_scores if 0.6 <= s < 0.9)
low = sum(1 for s in case_scores if s < 0.6)
lines.append(f"- 高分(>=0.9): {high} 题")
lines.append(f"- 中分(0.6~0.9): {mid} 题")
lines.append(f"- 低分(<0.6): {low} 题")
lines.append(f"")

# Gap details for low-scoring cases
lines.append(f"## 二、低分案例分析")
lines.append(f"")
for rpt in report:
    if rpt["score"] >= 0.6:
        continue
    lines.append(f"### {rpt['test_id']} ({rpt['score']:.3f})")
    lines.append(f"指令: {rpt['instruction']}")
    if rpt["notes"]:
        for n in rpt["notes"]:
            lines.append(f"- ⚠ {n}")
    lines.append(f"- gold阶段: {'→'.join(rpt['gold_stages'])}")
    lines.append(f"- pred阶段: {'→'.join(rpt['pred_stages']) if rpt['pred_stages'] else '(未提取)'}")
    lines.append(f"- gold动作数: {len(rpt['gold_actions'])}")
    lines.append(f"- pred动作数: {len(rpt['pred_actions'])}")
    lines.append("")

lines.append(f"## 三、共性问题总结")
lines.append(f"")
all_notes = []
for rpt in report:
    all_notes.extend(rpt["notes"])
common = Counter(all_notes).most_common(10)
for issue, count in common:
    lines.append(f"- {issue} ({count}次)")
lines.append("")

lines.append(f"## 四、各case详细输出 vs Gold")
lines.append(f"")
for rpt in report:
    lines.append(f"### {rpt['test_id']} ({rpt['score']:.3f}) — {rpt['instruction']}")
    lines.append(f"")
    lines.append(f"**Gold阶段**: {'→'.join(rpt['gold_stages'])}")
    lines.append(f"**Pred阶段**: {'→'.join(rpt['pred_stages']) if rpt['pred_stages'] else '(未提取)'}")
    lines.append(f"")
    lines.append("| # | Gold动作 | Gold目标 | Pred动作 | Pred目标 |")
    lines.append("|---|---------|---------|---------|---------|")
    max_len = max(len(rpt['gold_actions']), len(rpt['pred_actions']))
    for i in range(max_len):
        ga, gt = rpt['gold_actions'][i] if i < len(rpt['gold_actions']) else ("", "")
        pa, pt = rpt['pred_actions'][i] if i < len(rpt['pred_actions']) else ("", "")
        lines.append(f"| {i+1} | {ga} | {gt} | {pa} | {pt} |")
    lines.append("")

import os
basename = os.path.splitext(os.path.basename(json_path))[0]
short = basename.replace('results_', '', 1)
out_path = base / "docs" / f'analysis_{short}.md'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f"分析报告已生成: {out_path} ({len(report)} cases)")
