#!/usr/bin/env python3
"""
机器狗任务规划评分引擎 v4
v3 四维框架 + 嵌入式失败模式检测 + 物品操作链完整性检查

与 v3 的核心区别:
  1. 物体验证从"名字出现"升级为"FIND→CARRY→DROP 完整操作链"检查
  2. 内嵌荒谬搬运目标检测（CARRY|充电桩/桌子/目标位置等）
  3. 内嵌 DROP 缺失检测（CARRY 后无 DROP）
  4. 内嵌冗余循环检测（动作块重复≥3次）
  5. 内嵌 EMERGENCY 缺失检测（危险任务缺紧急制动）
  6. alias 匹配加反向约束（防止"包裹"误匹配"包裹A和B"）
  7. P0/P1/P2 失败模式直接扣分

评分公式:
  v4 = v3四维加权和 - Σ(P0×0.15) - Σ(P1×0.08) - Σ(P2×0.03)
  floor = 0.0
"""

import json
import re
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict, Counter


# ============================================================
# 常量
# ============================================================

SOCIAL_ACTIONS = {"SPEAK", "WAVE", "BOW", "SHAKE", "NOD", "SHAKE_HEAD", "DANCE"}
MOVEMENT_ACTIONS = {"WALK", "TROT", "CLIMB", "CROUCH", "BACK"}
NAVIGATION_ACTIONS = {"NAVIGATE", "RETURN"}
INFRA_ACTIONS = {"LED_BLUE", "LED_GREEN", "LED_RED", "LED_WHITE"}

# 不可 CARRY 的物体（语义荒谬）
NONSENSICAL_CARRY_TARGETS = {
    "充电桩", "充电设备", "充电位",
    "桌子", "门", "楼梯",
    "目标位置", "起点",
}

# 人类称谓
HUMAN_TARGETS = {
    "主人", "客人", "小朋友", "收件人", "病人", "人",
    "师傅", "张工", "李总", "参观者", "观众",
    "跑步者", "浇水人", "修车师傅", "小主人",
}

# 危险/异常关键词（触发 EMERGENCY 需求）
DANGER_KEYWORDS = ["火警", "火灾", "着火", "起火", "烟雾", "浓烟",
                   "紧急", "危险", "危楼",
                   "触电", "漏电",
                   "疏散", "撤离", "逃生",
                   "摔倒", "受伤", "出血",
                   "地震", "泄漏", "煤气",
                   "警报响起", "警铃",
                   "故障停机", "设备冒烟"]

# 条件关键词
CONDITION_KEYWORDS = ["如果", "可能", "或者", "检查是否", "判断", "若", "假如", "万一",
                      "找不到", "不确定", "就", "则", "否则", "遇到", "发现.*就"]


@dataclass
class ActionStep:
    id: int
    action: str
    target: str
    phase: str
    deps: List[int] = field(default_factory=list)
    action_class: str = ""


class TaskPlanEvaluatorV4:
    """机器狗任务规划评估器 v4 — 四维度评分 + 嵌入式失败检测"""

    SYNONYMS = {
        "WALK": "WALK", "TROT": "TROT",
        "BEEP": "SPEAK", "TELL": "SPEAK", "REPORT": "SPEAK", "SAY": "SPEAK",
        "SHAKE": "NOD",
        "POINT": "WAVE", "GESTURE": "WAVE",
        "GIVE": "DROP", "DELIVER": "DROP", "RELEASE": "DROP",
        "PICK": "CARRY", "TAKE": "CARRY", "LIFT": "CARRY", "LOAD": "CARRY",
        "SEARCH": "FIND", "LOCATE": "FIND", "SEEK": "FIND",
    }

    OBJECT_ALIASES = {
        "快递": ["快递", "快递盒", "包裹", "快递包裹", "所有包裹"],
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
        "零件箱": ["零件箱"],
        "包裹A": ["包裹A", "包裹A和包裹B"],
        "包裹B": ["包裹B"],
    }

    LOCATION_ALIASES = {
        "起点": ["起点", "仓库起点", "返回起点", "巡逻起点"],
        "充电桩": ["充电桩", "充电站"],
    }

    STAGE_SYNONYMS = {
        "准备": ["开始", "启动", "出发", "就绪", "初始化"],
        "收尾": ["结束", "完成", "汇总", "回报", "报告"],
        "就位": ["待机", "复位", "等待", "等候"],
        "观察": ["察觉", "感知", "监测", "持续监测", "监听"],
        "搜索": ["寻找", "定位", "查找", "搜寻"],
        "检查": ["确认", "验证", "查看", "检测", "巡检"],
        "前往": ["导航", "前进", "移动"],
        "抵达": ["到达", "到位", "进入"],
        "返回": ["归位"],
        "取物": ["取件", "装载", "拿取"],
        "运送": ["运输", "搬运", "送A", "送B"],
        "递送": ["送达", "交付", "带回"],
        "迎接": ["接待", "迎宾"],
        "引导": ["带领", "带路"],
        "互动": ["致意", "交流"],
        "表演": ["展示", "演示"],
        "巡逻": ["巡查", "巡视"],
        "判断": ["条件", "分支", "尝试"],
        "切换": ["改变"],
        "越障": ["攀爬", "翻越"],
        "继续": ["恢复", "重新装载"],
        "回应": ["确认", "致谢"],
    }

    # v4 penalty config
    P0_PENALTY = 0.15
    P1_PENALTY = 0.08
    P2_PENALTY = 0.03

    def __init__(self, gold_standard: Dict):
        self.gold = gold_standard
        self.gold_actions = self._build_gold_actions(gold_standard.get("actions_gold", []))
        self.gold_stages = gold_standard["stages_expected"]
        self.gold_hri = gold_standard["hri_expected"]
        self.gold_action_set = {a.action for a in self.gold_actions.values()}
        self.gold_deps = {aid: set(self.gold_actions[aid].deps) for aid in self.gold_actions}

        self.objects_required = gold_standard.get("objects_required", [])
        self.locations_required = gold_standard.get("locations_required", [])

        wr = gold_standard.get("scoring_rules", {})
        self.weights = {
            "safety": wr.get("safety_weight", 0.30),
            "goal": wr.get("goal_weight", 0.40),
            "execution": wr.get("execution_weight", 0.20),
            "stage": wr.get("stage_weight", 0.10),
        }

        self.branch_paths = gold_standard.get("branch_paths", None)
        if self.branch_paths:
            for bp in self.branch_paths:
                bp["_gold_actions"] = self._build_gold_actions(bp.get("actions", []))

    # ==================== 工具方法 ====================

    @staticmethod
    def _build_gold_actions(actions_raw):
        return {
            a["id"]: ActionStep(
                id=a["id"],
                action=TaskPlanEvaluatorV4._normalize_action(a["action"]),
                target=a["target"],
                phase=a.get("phase", ""),
                deps=a.get("deps", []),
                action_class=a.get("action_class", ""),
            )
            for a in actions_raw
        }

    @staticmethod
    def _normalize_action(action: str) -> str:
        return TaskPlanEvaluatorV4.SYNONYMS.get(action.upper(), action.upper())

    @staticmethod
    def _normalize_target(target: str) -> str:
        t = target.strip()
        for prefix in ["前往", "走到", "沿", "沿巡逻", "沿路线", "到"]:
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
        norm_map = {
            "主人处": "主人", "主人位置": "主人",
            "仓库起点": "起点", "返回起点": "起点", "巡逻起点": "起点",
            "巡逻路线": "路线",
            "自身状态": "状态", "自身": "状态",
            "大门": "门", "门口": "门",
            "训练场": "场地", "训练区域": "场地",
        }
        for old, new in norm_map.items():
            if t == old:
                return new
        return t

    @staticmethod
    def _char_similarity(a: str, b: str) -> float:
        a_chars, b_chars = set(a), set(b)
        if not a_chars or not b_chars:
            return 0.0
        return len(a_chars & b_chars) / len(a_chars | b_chars)

    @staticmethod
    def _target_similarity(pred_target: str, gold_target: str) -> float:
        pn = TaskPlanEvaluatorV4._normalize_target(pred_target)
        gn = TaskPlanEvaluatorV4._normalize_target(gold_target)
        if pn == gn:
            return 1.0
        if pn in gn or gn in pn:
            return 0.8
        min_len = min(len(pn), len(gn))
        if min_len >= 2:
            common = sum(1 for i in range(min_len) if pn[i] == gn[i])
            if common / max(len(pn), len(gn)) > 0.5:
                return 0.6
        return 0.0

    @staticmethod
    def _infer_action_class(action: str) -> str:
        action_type_map = {
            "OBSERVE": "perception", "SELF_CHECK": "perception",
            "FIND": "perception", "RANGE": "perception",
            "NAVIGATE": "movement_nav", "WALK": "movement_gait",
            "TROT": "movement_gait", "BACK": "movement_gait",
            "TURN": "movement_gait", "SPIN": "movement_gait",
            "STOP": "movement_stop", "RETURN": "movement_nav",
            "FOLLOW": "movement_nav",
            "CROUCH": "movement_terrain", "CLIMB": "movement_terrain",
            "AVOID": "movement_terrain", "JUMP": "movement_terrain",
            "STAND": "posture", "LIE_DOWN": "posture", "SIT": "posture",
            "WAVE": "social", "BOW": "social", "SHAKE": "social",
            "NOD": "social", "SHAKE_HEAD": "social",
            "ROLL": "entertain", "DANCE": "entertain", "KICK": "entertain",
            "CARRY": "object_handle", "DROP": "object_handle",
            "PUSH": "movement_interaction",
            "SPEAK": "social_comm", "BEEP": "social_comm",
            "CAPTURE": "perception_capture",
            "WAIT": "standby", "EMERGENCY": "safety",
        }
        return action_type_map.get(action, "unknown")

    # ==================== 解析 ====================

    def parse_model_output(self, raw_text: str) -> Dict:
        result: Dict = {"hri": None, "stages": [], "actions": []}

        hri_match = re.search(r"HRI[：:]\s*(是|否|yes|no|true|false)", raw_text, re.I)
        if hri_match:
            val = hri_match.group(1).lower()
            result["hri"] = val in ("是", "yes", "true")

        stage_match = re.search(r"阶段[：:]\s*([^\n]+)", raw_text)
        if stage_match:
            stages_text = stage_match.group(1).strip()
            result["stages"] = [
                s.strip() for s in re.split(r"[→>、,;，；\s]+", stages_text) if s.strip()
            ]

        # 使用 lookahead 防止跨行多动作误匹配
        _NO_NEXT_ACTION = r"(?!\d+\.\s*[A-Z_]+\|)"
        action_pattern = re.compile(
            r"(\d+)\.\s*([A-Z_]+)\|((?:" + _NO_NEXT_ACTION + r"[^|\n])+)"
            r"(?:\|((?:" + _NO_NEXT_ACTION + r"[^|\n])*))?",
            re.MULTILINE
        )
        for match in action_pattern.finditer(raw_text):
            step_num = int(match.group(1))
            action_raw = match.group(2).strip().upper()
            target = match.group(3).strip()
            result["actions"].append({
                "step": step_num,
                "action": self._normalize_action(action_raw),
                "target": target,
                "action_raw": action_raw,
            })

        return result

    # ==================== v4 新增：失败模式检测 ====================

    def _detect_failure_modes(self, parsed: Dict, instruction: str) -> List[Dict]:
        """检测所有失败模式，返回 [{type, detail, severity}, ...]"""
        failures = []
        actions = parsed["actions"]
        hri = parsed["hri"]
        core = [a for a in actions if a["action"] not in INFRA_ACTIONS]

        # --- P0 ---

        # P0: HALLUCINATED_CARRY — CARRY 了荒谬目标 (按 target 去重)
        seen_absurd = set()
        for a in core:
            if a["action"] == "CARRY":
                t = self._normalize_target(a["target"])
                if any(nt in t for nt in NONSENSICAL_CARRY_TARGETS):
                    key = ("HALLUCINATED_CARRY", t)
                    if key not in seen_absurd:
                        seen_absurd.add(key)
                        failures.append({
                            "type": "HALLUCINATED_CARRY",
                            "detail": f"步骤{a['step']}: CARRY|{a['target']} — 不可搬运",
                            "severity": "P0"
                        })

        # P0: CARRY_HUMAN
        for a in core:
            if a["action"] == "CARRY":
                if any(h in a["target"] for h in HUMAN_TARGETS):
                    failures.append({
                        "type": "CARRY_HUMAN",
                        "detail": f"步骤{a['step']}: CARRY|{a['target']} — 不能搬运人类",
                        "severity": "P0"
                    })

        # --- P1 ---

        # P1: CARRY_WITHOUT_FIND — CARRY 前无 FIND（局部缺失，按 target 去重）
        carry_actions = [a for a in core if a["action"] == "CARRY"]
        find_actions = [a for a in core if a["action"] == "FIND"]
        seen_carry_no_find = set()
        for ca in carry_actions:
            c_step = ca["step"]
            ct = self._normalize_target(ca["target"])
            has_prior_find = any(
                fa["step"] < c_step and
                self._target_similarity(self._normalize_target(fa["target"]), ct) >= 0.5
                for fa in find_actions
            )
            if not has_prior_find and find_actions:
                key = ("CARRY_WITHOUT_FIND", ct)
                if key not in seen_carry_no_find:
                    seen_carry_no_find.add(key)
                    failures.append({
                        "type": "CARRY_WITHOUT_FIND",
                        "detail": f"步骤{c_step}: CARRY|{ca['target']} 之前没有 FIND",
                        "severity": "P1"
                    })

        # P1: MISSING_DROP — CARRY 了但无 DROP (按 target 去重)
        drop_actions = [a for a in core if a["action"] == "DROP"]
        seen_missing_drop = set()
        for ca in carry_actions:
            c_step = ca["step"]
            ct = self._normalize_target(ca["target"])
            has_subsequent_drop = any(
                da["step"] > c_step and
                self._target_similarity(self._normalize_target(da["target"]), ct) >= 0.5
                for da in drop_actions
            )
            if not has_subsequent_drop:
                key = ("MISSING_DROP", ct)
                if key not in seen_missing_drop:
                    seen_missing_drop.add(key)
                    failures.append({
                        "type": "MISSING_DROP",
                        "detail": f"步骤{c_step}: CARRY|{ca['target']} 之后没有 DROP，物品会一直背在身上",
                        "severity": "P1"
                    })

        # P1: MISSING_FIND_FOR_OBJ — 对 gold 要求的物品，没有 FIND
        for obj in self.objects_required:
            obj_name = obj["name"]
            obj_aliases = obj.get("aliases", [obj_name])
            required_actions = obj.get("actions", ["FIND", "CARRY", "DROP"])
            if "FIND" not in required_actions:
                continue
            found = False
            for a in core:
                if a["action"] == "FIND":
                    if self._target_matches_object(a["target"], obj_name, obj_aliases, strict=True) >= 0.5:
                        found = True
                        break
            if not found:
                failures.append({
                    "type": "MISSING_FIND_FOR_OBJ",
                    "detail": f"物品'{obj_name}': 缺少 FIND——机器狗不知道物品在哪",
                    "severity": "P0"
                })

        # P1: MISSING_DROP_FOR_OBJ — gold 要求但无 DROP
        for obj in self.objects_required:
            obj_name = obj["name"]
            obj_aliases = obj.get("aliases", [obj_name])
            required_actions = obj.get("actions", ["FIND", "CARRY", "DROP"])
            if "DROP" not in required_actions:
                continue
            dropped = False
            for a in core:
                if a["action"] == "DROP":
                    if self._target_matches_object(a["target"], obj_name, obj_aliases, strict=True) >= 0.5:
                        dropped = True
                        break
            if not dropped:
                # Only flag if model actually did CARRY this object
                carried = any(
                    a["action"] == "CARRY" and
                    self._target_matches_object(a["target"], obj_name, obj_aliases, strict=True) >= 0.5
                    for a in core
                )
                if carried:
                    failures.append({
                        "type": "MISSING_DROP_FOR_OBJ",
                        "detail": f"物品'{obj_name}': 有CARRY但无DROP",
                        "severity": "P1"
                    })

        # P1: MISSING_CARRY_FOR_OBJ — gold 要求 CARRY 但模型没有
        for obj in self.objects_required:
            obj_name = obj["name"]
            obj_aliases = obj.get("aliases", [obj_name])
            required_actions = obj.get("actions", ["FIND", "CARRY", "DROP"])
            if "CARRY" not in required_actions:
                continue
            carried = any(
                a["action"] == "CARRY" and
                self._target_matches_object(a["target"], obj_name, obj_aliases, strict=True) >= 0.5
                for a in core
            )
            if not carried:
                # Only flag if model did FIND this object (found but didn't carry)
                found = any(
                    a["action"] == "FIND" and
                    self._target_matches_object(a["target"], obj_name, obj_aliases, strict=True) >= 0.5
                    for a in core
                )
                if found:
                    failures.append({
                        "type": "MISSING_CARRY_FOR_OBJ",
                        "detail": f"物品'{obj_name}': 有FIND但无CARRY",
                        "severity": "P1"
                    })

        # P1: REDUNDANT_LOOP — 动作块重复 ≥3 次
        if len(core) >= 9:
            window = 4
            seq = [(a["action"], self._normalize_target(a["target"])) for a in core]
            block_counts = Counter()
            for i in range(len(seq) - window + 1):
                block = tuple(seq[i:i + window])
                block_counts[block] += 1
            max_repeat = max(block_counts.values()) if block_counts else 0
            if max_repeat >= 3:
                failures.append({
                    "type": "REDUNDANT_LOOP",
                    "detail": f"检测到动作块重复{max_repeat}次，疑似幻觉循环",
                    "severity": "P1"
                })

        # P1: MISSING_EMERGENCY — 危险任务无 EMERGENCY
        has_emergency = any(a["action"] == "EMERGENCY" for a in core)
        is_dangerous = any(
            re.search(kw, instruction) for kw in DANGER_KEYWORDS
        )
        if is_dangerous and not has_emergency:
            failures.append({
                "type": "MISSING_EMERGENCY",
                "detail": "任务涉及危险/异常但无EMERGENCY动作",
                "severity": "P1"
            })

        # P1: MISSING_CONDITION_BRANCH — E类条件任务输出线性
        has_condition = any(
            re.search(kw, instruction) for kw in CONDITION_KEYWORDS
        )
        has_self_check = any(a["action"] in ("SELF_CHECK", "OBSERVE") for a in core)
        if has_condition and not has_self_check:
            failures.append({
                "type": "MISSING_CONDITION_BRANCH",
                "detail": "任务指令含条件词但输出是纯线性的，且无SELF_CHECK/OBSERVE",
                "severity": "P1"
            })

        # P1: WALK_WITHOUT_NAVIGATE
        has_nav = any(a["action"] in NAVIGATION_ACTIONS for a in core)
        has_move = any(a["action"] in MOVEMENT_ACTIONS for a in core)
        if has_move and not has_nav:
            failures.append({
                "type": "WALK_WITHOUT_NAVIGATE",
                "detail": "有移动动作但无NAVIGATE",
                "severity": "P1"
            })

        # --- P2 ---

        # P2: EXTRA_SOCIAL — HRI=否但有社交动作 (按 action 去重)
        if hri is False:
            seen_extra_social = set()
            for a in core:
                if a["action"] in SOCIAL_ACTIONS:
                    key = ("EXTRA_SOCIAL", a["action"])
                    if key not in seen_extra_social:
                        seen_extra_social.add(key)
                        failures.append({
                            "type": "EXTRA_SOCIAL",
                            "detail": f"步骤{a['step']}: HRI=否但出现{a['action']}|{a['target']}",
                            "severity": "P2"
                        })

        # P2: MISSING_SOCIAL — HRI=是但无社交动作
        if hri is True:
            has_social = any(a["action"] in SOCIAL_ACTIONS for a in core)
            if not has_social:
                failures.append({
                    "type": "MISSING_SOCIAL",
                    "detail": "HRI=是但没有社交动作",
                    "severity": "P1"
                })

        # P2: MISSING_OBSERVE — 无 OBSERVE（warning）
        if not any(a["action"] == "OBSERVE" for a in core):
            failures.append({
                "type": "MISSING_OBSERVE",
                "detail": "输出中没有任何 OBSERVE 动作",
                "severity": "P2"
            })

        return failures

    # ==================== 物体/位置匹配（v4 增强） ====================

    @staticmethod
    def _resolve_aliases(name: str, alias_dict: Dict[str, List[str]]) -> List[str]:
        results = {name}
        for canonical, aliases in alias_dict.items():
            if name == canonical or name in aliases:
                results.update(aliases)
                results.add(canonical)
        return list(results)

    @staticmethod
    def _has_index_suffix(s: str) -> bool:
        """检查字符串是否包含编号后缀 (A/B/C/1/2/3 等)"""
        return bool(re.search(r'[A-Za-z0-9]', s))

    def _target_matches_object(self, pred_target: str, obj_name: str,
                                obj_aliases: List[str], strict: bool = False) -> float:
        """
        检查预测目标是否匹配物体。
        strict=True: 防止编号后缀误匹配 ("包裹A"不应视为"包裹"的子类型)
        但允许描述性变体 ("瓶装水"是"水"的子类型)
        """
        pn = self._normalize_target(pred_target)

        # 辅助：子串匹配评分
        def _substr_score(a: str, b: str) -> float:
            if a == b:
                return 1.0
            if a in b or b in a:
                if strict and self._has_index_suffix(a) != self._has_index_suffix(b):
                    # 一个有编号一个没有 → 可能是不同物品
                    return 0.3
                return 0.8
            return 0.0

        # 1. 精确匹配 obj_name
        score = _substr_score(pn, obj_name)
        if score >= 0.8:
            return score

        # 2. 检查用户提供的 aliases
        for alias in obj_aliases:
            score = _substr_score(pn, alias)
            if score >= 0.8:
                return score

        # 3. OBJECT_ALIASES fallback (低优先级)
        all_variants = self._resolve_aliases(obj_name, self.OBJECT_ALIASES)
        for v in all_variants:
            score = _substr_score(pn, v)
            if score == 1.0:
                return 0.9
            if score == 0.8:
                if strict and self._has_index_suffix(pn) != self._has_index_suffix(v):
                    return 0.3
                return 0.7

        # 4. 字符级模糊匹配
        sim = self._char_similarity(pn, obj_name)
        if sim > 0.3:
            return sim * 0.5
        return 0.0

    def _target_matches_location(self, pred_target: str, loc_name: str) -> float:
        pn = self._normalize_target(pred_target)
        if pn == loc_name:
            return 1.0
        if pn in loc_name or loc_name in pn:
            return 0.8
        all_variants = self._resolve_aliases(loc_name, self.LOCATION_ALIASES)
        for v in all_variants:
            if pn == v:
                return 0.9
            if pn in v or v in pn:
                return 0.7
        sim = self._char_similarity(pn, loc_name)
        if sim > 0.3:
            return sim * 0.5
        return 0.0

    # ==================== 四维度评分 ====================

    def _get_core_actions(self, action_list):
        return [a for a in action_list if a.get("action", "") not in INFRA_ACTIONS]

    def score_safety_social(self, pred_hri: Optional[bool],
                            pred_actions: List[Dict]) -> Tuple[float, Dict]:
        """安全与社交适切性 (30%)"""
        details = {}
        core = self._get_core_actions(pred_actions)

        # HRI
        if pred_hri is None:
            hri_score = 0.0
            hri_detail = "未提取HRI"
        else:
            hri_correct = pred_hri == self.gold_hri
            hri_score = 1.0 if hri_correct else 0.0
            hri_detail = "正确" if hri_correct else f"错误(应为{'是' if self.gold_hri else '否'})"
        details["hri"] = {"score": hri_score, "detail": hri_detail}

        # CARRY on human
        carry_on_human = False
        carry_detail = ""
        for a in core:
            if a["action"] == "CARRY":
                if any(h in a["target"] for h in HUMAN_TARGETS):
                    carry_on_human = True
                    carry_detail = f"CARRY|{a['target']}"
                    break
        carry_score = 0.0 if carry_on_human else 1.0
        details["no_carry_human"] = {"score": carry_score, "detail": carry_detail or "无违规"}

        # v4 新增：荒谬搬运直接 0 分
        has_absurd_carry = False
        for a in core:
            if a["action"] == "CARRY":
                t = self._normalize_target(a["target"])
                if any(nt in t for nt in NONSENSICAL_CARRY_TARGETS):
                    has_absurd_carry = True
                    carry_detail = f"CARRY|{a['target']} (荒谬)"
                    break
        if has_absurd_carry:
            carry_score = 0.0

        # Social
        pred_social = {a["action"] for a in core if a["action"] in SOCIAL_ACTIONS}
        if self.gold_hri:
            gold_social = {a.action for a in self.gold_actions.values()
                           if a.action in SOCIAL_ACTIONS}
            if gold_social:
                social_coverage = len(pred_social & gold_social) / len(gold_social)
                social_detail = f"应有={gold_social}, 实有={pred_social}"
            else:
                social_coverage = 1.0 if pred_social else 0.0
                social_detail = f"HRI=是但gold无社交动作, pred含社交={bool(pred_social)}"
        else:
            extra_social = len(pred_social)
            social_coverage = max(0, 1.0 - extra_social * 0.3)
            social_detail = f"HRI=否, 多余社交={pred_social}"
        social_score = max(0, min(1.0, social_coverage))
        details["social"] = {"score": social_score, "detail": social_detail}

        total = 0.333 * details["hri"]["score"] + 0.333 * carry_score + 0.334 * social_score
        return round(total, 3), details

    def score_goal_achievement(self, pred_actions: List[Dict]) -> Tuple[float, Dict]:
        """目标达成 (40%) — v4 增强物体验证"""
        details = {}
        core_pred = self._get_core_actions(pred_actions)
        core_gold = {aid: a for aid, a in self.gold_actions.items()
                     if a.action not in INFRA_ACTIONS}

        if not core_gold:
            return 1.0, {"coverage": {"score": 1.0}, "target": {"score": 1.0},
                         "objects": {"score": 1.0}, "locations": {"score": 1.0}}

        # 动作类覆盖率
        gold_classes = set()
        for a in core_gold.values():
            if a.action_class:
                gold_classes.add(a.action_class)
        pred_classes = set()
        for a in core_pred:
            ac = self._infer_action_class(a["action"])
            if ac:
                pred_classes.add(ac)
        coverage = len(gold_classes & pred_classes) / len(gold_classes) if gold_classes else 0.0
        details["coverage"] = {"score": round(coverage, 3),
                               "detail": f"gold_classes={gold_classes}, pred_classes={pred_classes}"}

        # 目标精确度
        pred_by_class = defaultdict(list)
        for a in core_pred:
            ac = self._infer_action_class(a["action"])
            pred_by_class[ac].append(a)
        target_scores = []
        target_details = []
        for gid, g in core_gold.items():
            matches = pred_by_class.get(g.action_class, [])
            if not matches:
                continue
            best_score = 0.0
            best_match = ""
            for m in matches:
                sim = self._target_similarity(m["target"], g.target)
                if sim > best_score:
                    best_score = sim
                    best_match = m["target"]
            if best_score > 0.3:
                target_scores.append(best_score)
                target_details.append(f"{g.action}|{g.target}~{best_match}={best_score:.2f}")
        target_score = sum(target_scores) / max(len(target_scores), 1) if target_scores else 0.0
        details["target"] = {"score": round(target_score, 3),
                             "detail": "; ".join(target_details[:5])}

        # v4 增强：物体操作链验证
        obj_score, obj_detail = self._score_object_chain(core_pred)
        details["objects"] = obj_detail

        # 位置验证
        loc_score, loc_detail = self._score_location_coverage(pred_actions)
        details["locations"] = loc_detail

        total = 0.30 * coverage + 0.30 * target_score + 0.20 * obj_score + 0.20 * loc_score
        return round(total, 3), details

    def _score_object_chain(self, core_pred: List[Dict]) -> Tuple[float, Dict]:
        """v4 增强：检查 FIND→CARRY→DROP 完整操作链"""
        if not self.objects_required:
            return 1.0, {"score": 1.0, "detail": "无required objects", "chain_details": []}

        chain_scores = []
        chain_details = []

        for obj in self.objects_required:
            obj_name = obj["name"]
            obj_aliases = obj.get("aliases", [obj_name])
            required_actions = obj.get("actions", ["FIND", "CARRY", "DROP"])

            # 对每个 required action 检查模型是否执行了
            action_results = {}
            for req_action in required_actions:
                best = None
                for a in core_pred:
                    if a["action"] != req_action:
                        continue
                    sim = self._target_matches_object(a["target"], obj_name, obj_aliases, strict=True)
                    if sim >= 0.5:
                        if best is None or sim > best[0]:
                            best = (sim, f"{a['action']}|{a['target']}")
                action_results[req_action] = best

            # 计算对象子分数
            checks = len(required_actions)
            passed = sum(1 for v in action_results.values() if v is not None)

            detail_parts = []
            for req in required_actions:
                if action_results[req] is not None:
                    detail_parts.append(f"{req}[OK]({action_results[req][1]})")
                else:
                    detail_parts.append(f"{req}[MISS]")

            obj_score = passed / checks if checks > 0 else 1.0
            chain_scores.append(obj_score)
            chain_details.append(f"{obj_name}: {', '.join(detail_parts)} [{passed}/{checks}]")

        overall = sum(chain_scores) / len(chain_scores) if chain_scores else 1.0
        return round(overall, 3), {
            "score": round(overall, 3),
            "detail": "; ".join(chain_details),
            "chain_details": chain_details,
        }

    def _score_location_coverage(self, pred_actions: List[Dict]) -> Tuple[float, Dict]:
        if not self.locations_required:
            return 1.0, {"score": 1.0, "detail": "无required locations"}

        core = self._get_core_actions(pred_actions)
        matched = 0
        location_matches = []

        for loc in self.locations_required:
            loc_name = loc["name"]
            best_sim = 0.0
            best_action = ""
            for a in core:
                if a["action"] not in ("NAVIGATE", "RETURN"):
                    continue
                sim = self._target_matches_location(a["target"], loc_name)
                if sim > best_sim:
                    best_sim = sim
                    best_action = f"{a['action']}|{a['target']}"
            if best_sim >= 0.5:
                matched += 1
                location_matches.append(f"{loc_name}✓({best_action})")
            else:
                location_matches.append(f"{loc_name}✗")

        score = matched / len(self.locations_required)
        detail = f"{matched}/{len(self.locations_required)}: " + ", ".join(location_matches)
        return round(score, 3), {"score": round(score, 3), "detail": detail}

    def score_execution_quality(self, pred_actions: List[Dict]) -> Tuple[float, Dict]:
        """执行质量 (20%) — v4 强化 CARRY_WITHOUT_FIND 惩罚"""
        details = {}
        core_pred = self._get_core_actions(pred_actions)
        core_gold = {aid: a for aid, a in self.gold_actions.items()
                     if a.action not in INFRA_ACTIONS}

        if not core_gold:
            return 1.0, {}

        # 依赖满足度
        gold_deps = {aid: set(g.deps) for aid, g in core_gold.items()}
        pred_to_gold = self._map_pred_to_gold(core_pred, core_gold, gold_deps)

        dep_violations = 0
        dep_checks = 0
        seen_gold_ids = set()
        for pred_step in core_pred:
            mapped_gold_id = pred_to_gold.get(pred_step["step"])
            if mapped_gold_id is not None:
                mapped_action = core_gold[mapped_gold_id].action
                if mapped_action in ("NAVIGATE",):
                    seen_gold_ids.add(mapped_gold_id)
                    continue
                for dep_id in gold_deps.get(mapped_gold_id, set()):
                    dep_checks += 1
                    if dep_id not in seen_gold_ids:
                        dep_violations += 1
                seen_gold_ids.add(mapped_gold_id)

        dep_score = max(0, 1.0 - (dep_violations / max(dep_checks, 1))) if dep_checks > 0 else 1.0
        details["dep"] = {"score": round(dep_score, 3),
                          "detail": f"violations={dep_violations}/{dep_checks}"}

        # v4 增强：顺序合理性 — CARRY_WITHOUT_FIND 硬惩罚
        seen_actions = []
        carry_without_find_count = 0
        walk_without_nav_count = 0

        for a in core_pred:
            act = a["action"]
            if act == "CARRY":
                has_prior_find = any(
                    s["action"] == "FIND" for s in seen_actions
                )
                if not has_prior_find and any(
                    s["action"] == "FIND" for s in core_pred
                ):
                    carry_without_find_count += 1
            if act in MOVEMENT_ACTIONS:
                has_prior_nav = any(
                    s["action"] in NAVIGATION_ACTIONS for s in seen_actions
                )
                if not has_prior_nav:
                    walk_without_nav_count += 1
            seen_actions.append(a)

        # v4: 每个 CARRY_WITHOUT_FIND 扣 0.3，每个 WALK_WITHOUT_NAV 扣 0.2
        order_penalty = min(1.0, carry_without_find_count * 0.3 + walk_without_nav_count * 0.2)
        order_score = max(0.0, 1.0 - order_penalty)
        details["order"] = {
            "score": round(order_score, 3),
            "carry_without_find": carry_without_find_count,
            "walk_without_nav": walk_without_nav_count,
        }

        total = 0.6 * dep_score + 0.4 * order_score
        return round(total, 3), details

    def score_stage_quality(self, pred_stages: List[str]) -> Tuple[float, Dict]:
        """阶段质量 (10%)"""
        if not pred_stages:
            return 0.0, {"match": {"score": 0.0, "detail": "未提取阶段"}}

        used_pred = set()
        match_scores = []
        for gs in self.gold_stages:
            best = 0.0
            best_idx = -1
            for i, ps in enumerate(pred_stages):
                if i in used_pred:
                    continue
                sim = self._stage_similarity(gs, ps)
                if sim > best:
                    best = sim
                    best_idx = i
            if best > 0.2:
                match_scores.append(best)
                used_pred.add(best_idx)

        coverage = sum(match_scores) / max(len(self.gold_stages), 1)
        extra = max(0, len(pred_stages) - len(used_pred))
        missing = max(0, len(self.gold_stages) - len(match_scores))
        penalty = min(extra * 0.02, 0.10) + min(missing * 0.03, 0.12)
        score = max(0.0, coverage - penalty)

        return round(score, 3), {
            "match": {"score": round(score, 3),
                      "detail": f"gold={self.gold_stages}, pred={pred_stages}, matched={len(match_scores)}/{len(self.gold_stages)}"}
        }

    # ==================== 内部工具 ====================

    def _map_pred_to_gold(self, pred_actions, gold_actions, gold_deps):
        mapping = {}
        used_gold = set()
        for pred in pred_actions:
            best_match = None
            best_score = -1.0
            for gid, gold in gold_actions.items():
                if gid in used_gold:
                    continue
                act_match = 1.0 if pred["action"] == gold.action else 0.0
                tgt_sim = self._target_similarity(pred["target"], gold.target)
                score = act_match * 0.6 + tgt_sim * 0.4
                if score > best_score and score > 0.4:
                    best_score = score
                    best_match = gid
            if best_match is not None:
                mapping[pred["step"]] = best_match
                used_gold.add(best_match)
        return mapping

    @classmethod
    def _stage_similarity(cls, gs: str, ps: str) -> float:
        if gs == ps:
            return 1.0
        if gs in ps or ps in gs:
            return 0.80
        syns = cls.STAGE_SYNONYMS.get(gs, [])
        if ps in syns:
            return 0.85
        ps_syns = cls.STAGE_SYNONYMS.get(ps, [])
        if gs in ps_syns:
            return 0.80
        g_chars, p_chars = set(gs), set(ps)
        if g_chars and p_chars:
            sim = len(g_chars & p_chars) / len(g_chars | p_chars)
            if sim > 0.3:
                return sim * 0.55
        return 0.0

    # ==================== 总评估入口 ====================

    def evaluate(self, model_raw_output: str, instruction: str = "") -> Dict:
        """v4 评估入口: 四维度加权 + 失败模式扣分"""
        parsed = self.parse_model_output(model_raw_output)
        pred_actions = parsed["actions"]
        w = self.weights

        if not instruction:
            instruction = self.gold.get("instruction", "")

        # 1. 四维度评分
        safety_score, safety_detail = self.score_safety_social(parsed["hri"], pred_actions)

        if self.branch_paths:
            best_bp = None
            best_total = -1.0
            saved_actions = self.gold_actions
            saved_deps = self.gold_deps

            for bp in self.branch_paths:
                self.gold_actions = bp["_gold_actions"]
                self.gold_deps = {aid: set(a.deps) for aid, a in bp["_gold_actions"].items()}
                goal, _ = self.score_goal_achievement(pred_actions)
                exec_score, _ = self.score_execution_quality(pred_actions)
                stage_score, _ = self.score_stage_quality(parsed["stages"])
                total = w["goal"] * goal + w["execution"] * exec_score + w["stage"] * stage_score
                if total > best_total:
                    best_total = total
                    best_bp = bp

            self.gold_actions = best_bp["_gold_actions"]
            self.gold_deps = {aid: set(a.deps) for aid, a in best_bp["_gold_actions"].items()}
            goal_score, goal_detail = self.score_goal_achievement(pred_actions)
            exec_score, exec_detail = self.score_execution_quality(pred_actions)
            stage_score, stage_detail = self.score_stage_quality(parsed["stages"])

            self.gold_actions = saved_actions
            self.gold_deps = saved_deps
        else:
            goal_score, goal_detail = self.score_goal_achievement(pred_actions)
            exec_score, exec_detail = self.score_execution_quality(pred_actions)
            stage_score, stage_detail = self.score_stage_quality(parsed["stages"])

        # 2. 失败模式检测
        failures = self._detect_failure_modes(parsed, instruction)

        # 3. 计算带 penalty 的总分
        base_total = (safety_score * w["safety"] +
                      goal_score * w["goal"] +
                      exec_score * w["execution"] +
                      stage_score * w["stage"])

        p0_count = sum(1 for f in failures if f["severity"] == "P0")
        p1_count = sum(1 for f in failures if f["severity"] == "P1")
        p2_count = sum(1 for f in failures if f["severity"] == "P2")

        penalty = p0_count * self.P0_PENALTY + p1_count * self.P1_PENALTY + p2_count * self.P2_PENALTY
        final = max(0.0, base_total - penalty)

        # v3 compat fields
        obj_subscore = goal_detail.get("objects", {}).get("score", 1.0)
        loc_subscore = goal_detail.get("locations", {}).get("score", 1.0)

        failure_types = list(set(f["type"] for f in failures))

        return {
            "total_score": round(final, 3),
            "base_score": round(base_total, 3),
            "penalty": round(penalty, 3),
            "weights": w,
            "breakdown": {
                "safety": {"score": safety_score, **safety_detail},
                "goal": {"score": goal_score, **goal_detail},
                "execution": {"score": exec_score, **exec_detail},
                "stage": {"score": stage_score, **stage_detail},
                "v4_object_chain": obj_subscore,
                "v4_location_coverage": loc_subscore,
            },
            "failures": failures,
            "failure_types": failure_types,
            "failure_summary": f"P0={p0_count} P1={p1_count} P2={p2_count}",
            "parsed": parsed,
        }


# ==================== 自测 ====================

if __name__ == "__main__":
    import sys

    testset_path = "data/task_plan_testset_v3.json"
    try:
        with open(testset_path, "r", encoding="utf-8") as f:
            testset = json.load(f)

        print("=" * 72)
        print("  TaskPlanEvaluatorV4 自测")
        print("=" * 72)

        # Test 1: Perfect gold match
        case = testset[0]  # DOG-A01
        evaluator = TaskPlanEvaluatorV4(case)

        actions_str = "\n".join(
            f"{a['id']}. {a['action']}|{a['target']}"
            for a in case["actions_gold"]
        )
        model_output = (
            f"HRI：{'是' if case['hri_expected'] else '否'}\n"
            f"阶段：{'→'.join(case['stages_expected'])}\n\n"
            f"{actions_str}"
        )

        result = evaluator.evaluate(model_output, case["instruction"])
        print(f"\nTest 1 — {case['test_id']}: {case['instruction']}")
        print(f"  Total: {result['total_score']}  Base: {result['base_score']}  Penalty: {result['penalty']}")
        print(f"  Failures: {result['failure_summary']}")
        print(f"  Failure types: {result['failure_types']}")
        assert result['total_score'] >= 0.95, f"Perfect match should score ≥0.95, got {result['total_score']}"

        # Test 2: B09 with hallucinated objects
        b09 = [c for c in testset if c["test_id"] == "DOG-B09"][0]
        ev = TaskPlanEvaluatorV4(b09)
        # Simulate: model outputs generic "包裹A和B" instead of the actual "快递"
        bad_output = (
            "HRI：是 | 阶段：准备→取件→运送→交付\n"
            "1. LED_BLUE|状态灯 2. NAVIGATE|收发室 3. WALK|前往收发室\n"
            "4. FIND|包裹A和B 5. CARRY|包裹A和B\n"
            "6. NAVIGATE|前台 7. WALK|前往前台\n"
            "8. DROP|包裹A和B 9. SPEAK|包裹送达 10. RETURN|起点"
        )
        result2 = ev.evaluate(bad_output, b09["instruction"])
        print(f"\nTest 2 — B09 物品名不匹配 (strict mode):")
        print(f"  Total: {result2['total_score']}  Base: {result2['base_score']}  Penalty: {result2['penalty']}")
        print(f"  Failures: {result2['failure_summary']}")
        print(f"  Failure types: {result2['failure_types']}")
        print(f"  Object chain: {result2['breakdown']['goal']['objects']['detail']}")
        # Should have MISSING_FIND_FOR_OBJ (P0) for both 快递 and 所有包裹
        assert any(f["type"] == "MISSING_FIND_FOR_OBJ" for f in result2["failures"]), \
            "Should detect MISSING_FIND_FOR_OBJ for B09"
        assert result2["total_score"] < 0.80, \
            f"B09 with wrong objects should score <0.80, got {result2['total_score']}"

        # Test 3: A02 with CARRY|充电设备
        a02 = [c for c in testset if c["test_id"] == "DOG-A02"][0]
        ev = TaskPlanEvaluatorV4(a02)
        bad_output_a02 = (
            "HRI：否 | 阶段：准备→充电→收尾\n"
            "1. LED_BLUE|状态灯 2. NAVIGATE|充电桩 3. WALK|前往充电桩\n"
            "4. FIND|充电桩 5. SELF_CHECK|自身状态\n"
            "6. CARRY|充电设备 7. DROP|充电设备\n"
            "8. RETURN|起点 9. STOP|待机"
        )
        result3 = ev.evaluate(bad_output_a02, a02["instruction"])
        print(f"\nTest 3 — A02 CARRY|充电设备 (荒谬搬运):")
        print(f"  Total: {result3['total_score']}  Base: {result3['base_score']}  Penalty: {result3['penalty']}")
        print(f"  Failures: {result3['failure_summary']}")
        print(f"  Failure types: {result3['failure_types']}")
        assert any(f["type"] == "HALLUCINATED_CARRY" for f in result3["failures"]), \
            "Should detect HALLUCINATED_CARRY for CARRY|充电设备"
        assert result3["total_score"] < 0.75, \
            f"A02 with absurd carry should score <0.75, got {result3['total_score']}"

        # Test 4: CARRY without DROP
        ev4 = TaskPlanEvaluatorV4(case)
        no_drop_output = (
            "HRI：否 | 阶段：准备→巡逻→收尾\n"
            "1. NAVIGATE|巡逻起点 2. WALK|沿巡逻路线\n"
            "3. FIND|包裹 4. CARRY|包裹 5. OBSERVE|环境异常\n"
            "6. RETURN|起点 7. STOP|待机"
        )
        result4 = ev4.evaluate(no_drop_output, "绕仓库巡逻一圈，检查是否有异常")
        print(f"\nTest 4 — CARRY without DROP:")
        print(f"  Total: {result4['total_score']}  Base: {result4['base_score']}  Penalty: {result4['penalty']}")
        print(f"  Failure types: {result4['failure_types']}")
        assert any(f["type"] == "MISSING_DROP" for f in result4["failures"]), \
            "Should detect MISSING_DROP"

        # Test 5: REDUNDANT_LOOP
        ev5 = TaskPlanEvaluatorV4(case)
        loop_output = (
            "HRI：否 | 阶段：准备→巡逻→收尾\n"
            + "\n".join([
                f"{i}. NAVIGATE|点{i % 3 + 1} {i+1}. WALK|前往 {i+2}. OBSERVE|环境 {i+3}. SELF_CHECK|状态"
                for i in range(1, 40, 3)
            ])
        )
        result5 = ev5.evaluate(loop_output, "巡逻检测异常")
        print(f"\nTest 5 — REDUNDANT_LOOP:")
        print(f"  Total: {result5['total_score']}  Base: {result5['base_score']}  Penalty: {result5['penalty']}")
        print(f"  Failure types: {result5['failure_types']}")
        assert any(f["type"] == "REDUNDANT_LOOP" for f in result5["failures"]), \
            "Should detect REDUNDANT_LOOP"

        # Test 6: MISSING_EMERGENCY
        ev6 = TaskPlanEvaluatorV4(case)
        danger_output = (
            "HRI：否 | 阶段：响应→疏散\n"
            "1. NAVIGATE|房间1 2. WALK|前往房间1 3. OBSERVE|是否有人\n"
            "4. SPEAK|请撤离 5. NAVIGATE|安全点 6. RETURN|起点"
        )
        result6 = ev6.evaluate(danger_output, "模拟火警，检测到烟雾后检查房间，疏散人员到安全点")
        print(f"\nTest 6 — MISSING_EMERGENCY:")
        print(f"  Total: {result6['total_score']}  Base: {result6['base_score']}  Penalty: {result6['penalty']}")
        print(f"  Failure types: {result6['failure_types']}")
        assert any(f["type"] == "MISSING_EMERGENCY" for f in result6["failures"]), \
            "Should detect MISSING_EMERGENCY for fire task"

        print(f"\n{'='*72}")
        print(f"  全部 6 项自测通过")
        print(f"{'='*72}")

    except FileNotFoundError:
        print(f"测试集文件 {testset_path} 未找到")
        sys.exit(1)
    except AssertionError as e:
        print(f"\n自测失败: {e}")
        sys.exit(1)
