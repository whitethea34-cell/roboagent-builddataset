#!/usr/bin/env python3
"""Clean up dataset: merge stage names, remove LEDs, add action_type_class."""
import json
import copy
import sys

STAGE_MAP = {
    # === 任务调度 ===
    "准备": "准备", "巡逻准备": "准备", "出发": "准备",
    "初始任务": "准备",
    "出发": "准备",
    "就位": "就位", "待机": "就位",
    "复位": "就位", "等待": "就位",
    "充电": "充电",
    "收尾": "收尾", "完成": "收尾", "正常完成": "收尾",
    "汇总": "收尾", "汇总返回": "收尾", "回报": "收尾", "报告": "收尾",
    "中断充电": "中断",

    # === 环境感知 ===
    "察觉": "察觉",
    "观察": "观察", "持续监测": "观察", "监听": "观察", "检测可达性": "观察",
    "搜索": "搜索", "寻找": "搜索", "定位": "搜索",
    "寻找人员": "搜索", "寻找小朋友": "搜索",
    "寻找师傅": "搜索", "寻找收件人": "搜索",
    "检测": "检查",  # 检测→检查（短时感知归类到检查）
    "确认": "回应", "回应": "回应", "致谢": "回应",
    "确认清场": "检查",
    "遇障判断": "判断", "遇人判断": "判断",
    "P1巡查": "巡查", "P2巡查": "巡查", "P3巡查": "巡查",
    "送A": "运送", "送B": "运送",
    "带回/回报": "带回", "反馈": "带回",

    # === 检查类（同动作模式） ===
    "检查": "检查",
    "大门检查": "检查", "窗户检查": "检查", "电源检查": "检查",
    "照明检查": "检查", "消防检查": "检查", "卫生检查": "检查",
    "检查A": "检查", "检查B": "检查", "检查C": "检查",
    "检查房间": "检查",
    "巡逻+检测": "检查",  # OBSERVE+SELF_CHECK → 检查

    # === 导航移动 ===
    "前往": "前往", "导航": "前往", "前进": "前往",
    "前往A": "前往", "前往B": "前往", "前往C": "前往",
    "前往主目标": "前往", "前往花园": "前往", "前往车库": "前往",
    "接近": "接近",
    "抵达": "抵达", "到达": "抵达", "到位": "抵达",
    "进入": "进入",
    "通过": "通过", "穿越": "通过",
    "推门": "推门",
    "匍匐": "匍匐",
    "越障": "越障", "攀爬": "越障", "尝试楼梯": "越障",
    "绕行": "绕行",
    "返回": "返回",

    # === 物品操作 ===
    "取物": "取物", "取件": "取物", "取水": "取物",
    "取药": "取物", "取工具": "取物", "取毛巾": "取物", "取球": "取物",
    "装载": "取物",
    "运送": "运送",
    "递送": "递送", "送达": "递送", "互动递送": "递送",
    "递送/存放": "递送",
    "带回": "带回", "存放": "带回",

    # === 社交互动 ===
    "迎接": "迎接", "接待": "迎接",
    "互动": "互动",
    "致意": "致意", "谢幕": "致意", "告别": "致意",
    "表演": "表演",
    "引导": "引导",
    "引导展区1": "引导", "引导展区2": "引导", "引导展区3": "引导",
    "引导撤离": "引导", "引导离场": "引导",
    "退后": "退后",

    # === 巡逻巡检 ===
    "巡逻": "巡逻",
    "巡逻R1": "巡逻", "巡逻R2": "巡逻", "巡逻R3": "巡逻",
    "巡逻准备": "准备",  # 已映射到准备

    # === 安全异常 ===
    "判断": "判断",
    "响应警报": "响应", "响应": "响应",
    "切换": "切换",
    "恢复": "继续", "重新装载": "继续", "继续": "继续",
    "紧急停止": "紧急停止",

    # === 其他 ===
    "等待": "就位",
    "监听": "观察",
    "引导离场": "引导",
    "引导撤离": "引导",
    "引导展区1": "引导",
    "引导展区2": "引导",
    "引导展区3": "引导",
    "互动递送": "递送",
    "正常完成": "收尾",
    "汇总返回": "收尾",
    "确认清场": "确认清场",
    "完成": "收尾",
}

# Action type classification
ACTION_TYPE = {
    # 状态灯（不参与核心评分）
    "LED_BLUE": "infra_light", "LED_GREEN": "infra_light",
    "LED_RED": "infra_light", "LED_WHITE": "infra_light",
    # 感知
    "OBSERVE": "perception", "SELF_CHECK": "perception",
    "FIND": "perception", "RANGE": "perception",
    # 导航移动-基础
    "NAVIGATE": "movement_nav", "WALK": "movement_gait",
    "TROT": "movement_gait", "BACK": "movement_gait",
    "TURN": "movement_gait", "SPIN": "movement_gait",
    "STOP": "movement_stop", "RETURN": "movement_nav",
    "FOLLOW": "movement_nav",
    # 导航移动-越障
    "CROUCH": "movement_terrain", "CLIMB": "movement_terrain",
    "AVOID": "movement_terrain", "JUMP": "movement_terrain",
    # 肢体动作
    "STAND": "posture", "LIE_DOWN": "posture", "SIT": "posture",
    "WAVE": "social", "BOW": "social", "SHAKE": "social",
    "NOD": "social", "SHAKE_HEAD": "social",
    "ROLL": "entertain", "DANCE": "entertain", "KICK": "entertain",
    # 交互运载
    "CARRY": "object_handle", "DROP": "object_handle",
    "PUSH": "movement_interaction",
    "SPEAK": "social_comm", "BEEP": "social_comm",
    "CAPTURE": "perception_capture",
    "WAIT": "standby", "EMERGENCY": "safety",
}

# Actions that should NEVER be performed on humans
NO_CARRY_HUMAN = {"CARRY"}
HUMAN_TARGET_INDICATORS = ["人", "师傅", "张工", "李总", "客人", "主人",
                           "小朋友", "参观者", "观众", "收件人", "跑步者",
                           "浇水人"]


def merge_stage_names(case):
    """Apply stage name mapping to a single test case."""
    # Map stages_expected
    original_stages = case["stages_expected"]
    case["stages_expected"] = list(dict.fromkeys(
        STAGE_MAP.get(s, s) for s in original_stages
    ))

    # Map phases in actions_gold
    for a in case["actions_gold"]:
        orig = a["phase"]
        a["phase"] = STAGE_MAP.get(orig, orig)
        a["phase_original"] = orig  # keep original for reference

    # Also map branch_path stages if present
    for bp in case.get("branch_paths", []):
        for a in bp.get("actions", []):
            orig = a["phase"]
            a["phase"] = STAGE_MAP.get(orig, orig)
            a["phase_original"] = orig


def add_action_type_class(case):
    """Add action_type_class to each gold action."""
    for a in case["actions_gold"]:
        a["action_class"] = ACTION_TYPE.get(a["action"], "unknown")
    for bp in case.get("branch_paths", []):
        for a in bp.get("actions", []):
            a["action_class"] = ACTION_TYPE.get(a["action"], "unknown")


def remove_leds(case):
    """Remove LED actions from actions_gold (they'll be bonus-only in scoring)."""
    orig_count = len(case["actions_gold"])
    case["actions_gold"] = [a for a in case["actions_gold"]
                            if not a["action"].startswith("LED_")]
    led_count = orig_count - len(case["actions_gold"])
    case["led_removed"] = led_count

    # Also remove from branch_paths
    for bp in case.get("branch_paths", []):
        bp["actions"] = [a for a in bp.get("actions", [])
                         if not a["action"].startswith("LED_")]

    return led_count


def renumber_and_relink(case):
    """Renumber action IDs and fix deps after LED removal."""
    new_actions = []
    id_map = {}
    for old_id_str, a in enumerate(case["actions_gold"], 1):
        id_map[a["id"]] = old_id_str
        a["id"] = old_id_str
        new_actions.append(a)
    # Fix deps
    for a in new_actions:
        a["deps"] = [id_map.get(d, d) for d in a["deps"] if d in id_map]
    case["actions_gold"] = new_actions

    # Branch paths
    for bp in case.get("branch_paths", []):
        new_bp_actions = []
        bp_id_map = {}
        for new_id, a in enumerate(bp.get("actions", []), 1):
            bp_id_map[a["id"]] = new_id
            a["id"] = new_id
            new_bp_actions.append(a)
        for a in new_bp_actions:
            a["deps"] = [bp_id_map.get(d, d) for d in a["deps"] if d in bp_id_map]
        bp["actions"] = new_bp_actions


def add_scoring_rules(case):
    """Add new scoring rules config."""
    case["scoring_rules"] = {
        "safety_weight": 0.30,
        "goal_weight": 0.40,
        "execution_weight": 0.20,
        "stage_weight": 0.10,
    }


def analyze_mapping(cases):
    """Print analysis of the mapping."""
    from collections import Counter

    old_stages = set()
    new_stages = set()
    for c in cases:
        old_stages.update(c.get("_old_stages", []))
        new_stages.update(c["stages_expected"])

    action_classes = Counter()
    for c in cases:
        for a in c["actions_gold"]:
            action_classes[a.get("action_class", "?")] += 1

    print(f"Original unique stages: {len([s for c in cases for s in c.get('_old_stages',[])])} "
          f"→  New unique stages: {len(new_stages)}")
    print(f"New stage names ({len(new_stages)}): {sorted(new_stages)}")
    print(f"Action class distribution: {dict(action_classes)}")
    print(f"Total LEDs removed: {sum(c.get('led_removed',0) for c in cases)}")
    print(f"Branch path cases: {sum(1 for c in cases if c.get('branch_paths'))}")


def main():
    from pathlib import Path
    base = Path(__file__).parent.parent
    data_path = base / "data" / "task_plan_testset.json"
    backup_path = base / "data" / "task_plan_testset_backup.json"

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    total_led_removed = 0
    for c in data:
        c["_old_stages"] = list(c["stages_expected"])  # save for analysis
        merge_stage_names(c)
        add_action_type_class(c)
        total_led_removed += remove_leds(c)
        renumber_and_relink(c)
        add_scoring_rules(c)

    analyze_mapping(data)

    if not backup_path.exists():
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\nBackup saved to {backup_path}")

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Wrote {len(data)} cases.")
    print(f"Total LEDs removed: {total_led_removed}")


if __name__ == "__main__":
    main()
