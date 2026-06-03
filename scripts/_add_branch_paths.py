#!/usr/bin/env python3
"""Extract branch paths from E类 test cases' note field and add branch_paths to testset."""
import json, re

from pathlib import Path

base = Path(__file__).parent.parent

with open(base / "data" / "task_plan_testset.json", encoding='utf-8') as f:
    testset = json.load(f)

def parse_branches(note: str):
    """Parse '三路分支：畅通走[5-7]；有障可绕走[8-11]；有障不可绕走[12-15]'
    or '分支题：找到走[6-10]，找不到走[11-14]'"""
    m = re.match(r'(?:\d|[一二三四五])路分支[：:]\s*(.+)', note)
    if not m:
        m = re.match(r'分支题[：:]\s*(.+)', note)
    if not m:
        return None
    body = m.group(1)
    branches = []
    for part in re.split(r'[；;，,]', body):
        part = part.strip()
        if not part:
            continue
        # Match: "畅通走[5-7]" or "畅通走[5]" or "畅通走[5-7][10]" (two ranges)
        ranges = re.findall(r'\[(\d+)-(\d+)\]|\[(\d+)\]', part)
        ids = set()
        for r in ranges:
            if r[0] and r[1]:
                ids.update(range(int(r[0]), int(r[1]) + 1))
            elif r[2]:
                ids.add(int(r[2]))
        # Extract name before the first bracket
        name = re.sub(r'\s*\[.*$', '', part).strip()
        branches.append({'name': name, 'ids': sorted(ids)})
    return branches

def build_branch_paths(all_actions, branches):
    """For each branch, build a complete action list (shared prefix + branch exclusive)."""
    all_exclusive = set()
    for b in branches:
        all_exclusive.update(b['ids'])
    # Shared prefix: all actions not exclusive to any branch
    shared_ids = sorted(set(a['id'] for a in all_actions) - all_exclusive)

    paths = []
    for b in branches:
        path_ids = shared_ids + b['ids']
        path_ids.sort()
        id_map = {old: new for new, old in enumerate(path_ids, 1)}
        path_actions = []
        for old_id in path_ids:
            src = next(a for a in all_actions if a['id'] == old_id)
            path_actions.append({
                'id': id_map[old_id],
                'action': src['action'],
                'target': src['target'],
                'phase': src['phase'],
                'deps': [id_map[d] for d in src.get('deps', []) if d in id_map],
            })
        paths.append({'name': b['name'], 'actions': path_actions})
    return paths

updated = 0
for case in testset:
    note = case.get('note', '')
    if '路分支' not in note and '分支题' not in note:
        continue
    branches = parse_branches(note)
    if not branches:
        continue
    bp = build_branch_paths(case['actions_gold'], branches)
    case['branch_paths'] = bp
    updated += 1
    print(f"{case['test_id']}: {note} -> {len(bp)} branches")

with open(base / "data" / "task_plan_testset.json", 'w', encoding='utf-8') as f:
    json.dump(testset, f, ensure_ascii=False, indent=2)

print(f"\nUpdated {updated} E类 cases with branch_paths")
