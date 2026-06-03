#!/usr/bin/env python3
"""Batch re-evaluate all results_*.json with fixed parser."""
import json, glob, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from task_plan_evaluator import TaskPlanEvaluator

base = Path(__file__).parent.parent / "results"
with open(Path(__file__).parent.parent / "data" / "task_plan_testset.json", encoding='utf-8') as f:
    gold_map = {c['test_id']: c for c in json.load(f)}

for fp in sorted(base.glob('results_*.json')):
    if fp.name.endswith('_fixed.json'):
        continue
    with open(fp, encoding='utf-8') as f:
        orig = json.load(f)
    fixed = 0
    details = []
    for r in orig['details']:
        gold = gold_map.get(r['test_id'])
        if not gold:
            continue
        ev = TaskPlanEvaluator(gold).evaluate(r['raw_output'])
        details.append({
            **r,
            'score': ev['total_score'],
            'breakdown': ev['breakdown'],
            'parsed_actions': ev['parsed']['actions'],
        })
        fixed += ev['total_score']
    avg = fixed / len(details)
    out = base / fp.name.replace('.json', '_fixed.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({**orig, 'details': details, 'fixed_score_avg': avg}, f, ensure_ascii=False, indent=2)
    old = orig['meta']['avg_score']
    print(f'{fp.name}: {old:.4f} -> {avg:.4f} ({avg - old:+.4f}) -> {out.name}')
