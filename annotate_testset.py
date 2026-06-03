#!/usr/bin/env python3
"""
为测试集标注 objects_required 和 locations_required 字段
从 gold actions 中自动提取 + 人工精细修正

用法: python annotate_testset.py
输出: task_plan_testset_v3.json
"""
import json
import re
from pathlib import Path

# ============================================================
# 人工修正规则：自动提取不准确时手工指定
# ============================================================

# 部分案例的物体需要合并同义词
OBJECT_ALIASES = {
    "快递": ["快递", "快递盒", "包裹", "快递包裹"],
    "水": ["水", "瓶装水", "水杯"],
    "文件": ["文件", "文件夹", "文件袋"],
    "药": ["药", "药品", "药箱", "药和水"],
    "球": ["球", "玩具球", "红球", "彩色球"],
    "飞盘": ["飞盘", "黄色飞盘"],
    "钥匙": ["钥匙"],
    "玩具": ["玩具", "玩具球", "毛绒玩具"],
    "工具箱": ["工具箱"],
    "毛巾": ["毛巾"],
    "防晒霜": ["防晒霜"],
    "毛绒熊": ["毛绒熊"],
    "花瓶": ["花瓶", "红色花瓶", "彩色花瓶"],
    "扳手": ["扳手"],
    "拖鞋": ["拖鞋"],
    "包裹A": ["包裹A", "包裹A和包裹B"],
    "包裹B": ["包裹B"],
}

# 需要从物体/位置列表中排除的人称/社交目标
SOCIAL_EXCLUDE = {"客人", "主人", "小朋友", "陌生人", "参观者", "收件人", "跑步者", "浇水人", "你", "我", "来访者", "李总"}

# 需要排除的泛化环境感知目标
ENV_EXCLUDE = {"环境异常", "自身状态", "前方路况", "周围环境", "地面", "货架异常", "异常"}


def extract_objects_and_locations(case: dict) -> tuple:
    """从 gold actions 提取物体和位置列表"""
    action_map = {}  # target -> list of actions performed on it
    nav_targets = set()

    for a in case.get("actions_gold", []):
        act = a["action"].upper()
        tgt = a["target"].strip()

        if act in ("FIND", "CARRY", "DROP"):
            # Skip social/environmental targets
            if tgt in SOCIAL_EXCLUDE or tgt in ENV_EXCLUDE:
                continue
            # Skip if target is clearly a person (contains human indicators)
            if any(h in tgt for h in ["人", "师傅", "张工", "师傅", "小李", "老王"]):
                continue
            if tgt not in action_map:
                action_map[tgt] = set()
            action_map[tgt].add(act)

        elif act == "NAVIGATE":
            # Skip social/environmental targets (persons are not locations)
            if tgt in SOCIAL_EXCLUDE or tgt in ENV_EXCLUDE:
                continue
            # Only filter exact person-name matches for NAVIGATE,
            # not compound locations like "主人位置" or "客厅客人位置"
            if tgt in {"张工", "小李", "老王"}:
                continue
            nav_targets.add(tgt)

    # Build objects_required
    objects_required = []
    for obj_name, actions in sorted(action_map.items()):
        # Normalize: prefer canonical name
        canonical = obj_name
        for alias_group in OBJECT_ALIASES.values():
            if obj_name in alias_group:
                canonical = alias_group[0]
                break

        # Merge with existing canonical entry
        existing = next((o for o in objects_required if o["name"] == canonical), None)
        if existing:
            existing["aliases"].add(obj_name)
            existing["actions"].update(actions)
        else:
            objects_required.append({
                "name": canonical,
                "aliases": {obj_name},
                "actions": set(actions),
            })

    # Convert sets to sorted lists for JSON output
    for o in objects_required:
        o["aliases"] = sorted(o["aliases"])
        o["actions"] = sorted(o["actions"])

    # Build locations_required
    locations_required = []
    for loc in sorted(nav_targets):
        locations_required.append({
            "name": loc,
            "aliases": [loc],
            "actions": ["NAVIGATE"],
        })

    return objects_required, locations_required


def main():
    path = Path(__file__).parent / "data" / "task_plan_testset.json"
    with open(path, "r", encoding="utf-8") as f:
        testset = json.load(f)

    total_objects = 0
    total_locations = 0

    for case in testset:
        objects_req, locations_req = extract_objects_and_locations(case)
        case["objects_required"] = objects_req
        case["locations_required"] = locations_req
        total_objects += len(objects_req)
        total_locations += len(locations_req)

        obj_str = ", ".join(f"{o['name']}({','.join(o['actions'])})" for o in objects_req)
        loc_str = ", ".join(l["name"] for l in locations_req)
        print(f"{case['test_id']}: obj=[{obj_str}] loc=[{loc_str}]")

    # Write output
    output_path = Path(__file__).parent / "data" / "task_plan_testset_v3.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(testset, f, ensure_ascii=False, indent=2)

    print(f"\n共 {len(testset)} 题")
    print(f"含物体的题: {sum(1 for c in testset if c['objects_required'])}")
    print(f"含位置的题: {sum(1 for c in testset if c['locations_required'])}")
    print(f"总物体数: {total_objects}, 总位置数: {total_locations}")
    print(f"输出: {output_path}")


if __name__ == "__main__":
    main()
