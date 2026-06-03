#!/usr/bin/env python3
"""
机器狗任务规划评分引擎 v5
v4 四维框架 + 嵌入式失败模式检测 + 子任务评分（取代阶段评分）

与 v4 的核心区别:
  1. 阶段评分 → 子任务评分：解析"子任务：A→B→C"替代"阶段：A→B→C"
  2. 子任务语义匹配（动词+对象格式），权重保持 10%
  3. Testset gold 字段: stages_expected → subtasks_expected, phase → subtask
  4. 其余三维度（安全、目标达成、执行质量）+ 失败模式检测全部保留

评分公式:
  v5 = 四维加权和(安全/目标/执行/子任务) - Σ(P0×0.15) - Σ(P1×0.08) - Σ(P2×0.03)
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

NONSENSICAL_CARRY_TARGETS = {
    "充电桩", "充电设备", "充电位",
    "桌子", "门", "楼梯",
    "目标位置", "起点",
}

HUMAN_TARGETS = {
    "主人", "客人", "小朋友", "收件人", "病人", "人",
    "师傅", "张工", "李总", "参观者", "观众",
    "跑步者", "浇水人", "修车师傅", "小主人",
}

DANGER_KEYWORDS = ["火警", "火灾", "着火", "起火", "烟雾", "浓烟",
                   "紧急", "危险", "危楼",
                   "触电", "漏电",
                   "疏散", "撤离", "逃生",
                   "摔倒", "受伤", "出血",
                   "地震", "泄漏", "煤气",
                   "警报响起", "警铃",
                   "故障停机", "设备冒烟"]

CONDITION_KEYWORDS = ["如果", "可能", "或者", "检查是否", "判断", "若", "假如", "万一",
                      "找不到", "不确定", "就", "则", "否则", "遇到", "发现.*就"]

# 子任务名称近义词（用于语义模糊匹配）
SUBTASK_SYNONYMS = {
    "查找": ["搜索", "寻找", "定位", "搜寻", "找"],
    "导航至": ["前往", "移动至", "走到", "到达", "抵达", "进入", "接近"],
    "运送": ["运输", "搬运", "携带", "递送", "送"],
    "与": ["和", "跟", "同"],
    "交互": ["交流", "沟通", "致意", "打招呼"],
    "确认": ["检查", "验证", "查看", "检测", "巡检", "判断"],
    "返回": ["归位", "回到", "回"],
    "巡逻": ["巡查", "巡视"],
    "执行": ["进行", "开始"],
    "等待": ["待机", "等候"],
    "交付": ["递送", "送达", "交给"],
    "充电": ["补充电量"],
    "观察": ["察觉", "感知", "监测"],
    "迎接": ["接待", "迎宾"],
    "引导": ["带领", "带路"],
    "表演": ["展示", "演示"],
    "取": ["拿", "装载", "拾取"],
}


@dataclass
class ActionStep:
    id: int
    action: str
    target: str
    subtask: str
    deps: List[int] = field(default_factory=list)
    action_class: str = ""


class TaskPlanEvaluatorV5:
    """机器狗任务规划评估器 v5 — 四维度评分 + 子任务评分 + 嵌入式失败检测"""

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

    P0_PENALTY = 0.15
    P1_PENALTY = 0.08
    P2_PENALTY = 0.03

    def __init__(self, gold_standard: Dict):
        self.gold = gold_standard
        self.gold_actions = self._build_gold_actions(gold_standard.get("actions_gold", []))
        # v5: subtasks_expected 替代 stages_expected
        self.gold_subtasks = gold_standard.get("subtasks_expected",
                            gold_standard.get("stages_expected", []))
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
            "subtask": wr.get("subtask_weight", wr.get("stage_weight", 0.10)),
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
                action=TaskPlanEvaluatorV5._normalize_action(a["action"]),
                target=a["target"],
                subtask=a.get("subtask", a.get("phase", "")),
                deps=a.get("deps", []),
                action_class=a.get("action_class", ""),
            )
            for a in actions_raw
        }

    @staticmethod
    def _normalize_action(action: str) -> str:
        return TaskPlanEvaluatorV5.SYNONYMS.get(action.upper(), action.upper())

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
        pn = TaskPlanEvaluatorV5._normalize_target(pred_target)
        gn = TaskPlanEvaluatorV5._normalize_target(gold_target)
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
        """v5: 解析 子任务： 替代 阶段："""
        result: Dict = {"hri": None, "subtasks": [], "actions": []}

        hri_match = re.search(r"HRI[：:]\s*(是|否|yes|no|true|false)", raw_text, re.I)
        if hri_match:
            val = hri_match.group(1).lower()
            result["hri"] = val in ("是", "yes", "true")

        # v5: 解析 子任务： 替代 阶段：
        subtask_match = re.search(r"子任务[：:]\s*([^\n]+)", raw_text)
        if not subtask_match:
            # 向后兼容：如果没找到子任务，尝试解析阶段
            subtask_match = re.search(r"阶段[：:]\s*([^\n]+)", raw_text)
        if subtask_match:
            subtasks_text = subtask_match.group(1).strip()
            result["subtasks"] = [
                s.strip() for s in re.split(r"[→>、,;，；\s]+", subtasks_text) if s.strip()
            ]

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

    # ==================== v5 保留 v4 全部失败模式检测 ====================

    def _detect_failure_modes(self, parsed: Dict, instruction: str) -> List[Dict]:
        failures = []
        actions = parsed["actions"]
        hri = parsed["hri"]
        core = [a for a in actions if a["action"] not in INFRA_ACTIONS]

        # --- P0 ---

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

        for a in core:
            if a["action"] == "CARRY":
                if any(h in a["target"] for h in HUMAN_TARGETS):
                    failures.append({
                        "type": "CARRY_HUMAN",
                        "detail": f"步骤{a['step']}: CARRY|{a['target']} — 不能搬运人类",
                        "severity": "P0"
                    })

        # --- P1 ---

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
                        "detail": f"步骤{c_step}: CARRY|{ca['target']} 之后没有 DROP",
                        "severity": "P1"
                    })

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
                    "detail": f"物品'{obj_name}': 缺少 FIND",
                    "severity": "P0"
                })

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

        has_condition = any(
            re.search(kw, instruction) for kw in CONDITION_KEYWORDS
        )
        has_self_check = any(a["action"] in ("SELF_CHECK", "OBSERVE") for a in core)
        if has_condition and not has_self_check:
            failures.append({
                "type": "MISSING_CONDITION_BRANCH",
                "detail": "任务指令含条件词但输出是纯线性的",
                "severity": "P1"
            })

        has_nav = any(a["action"] in NAVIGATION_ACTIONS for a in core)
        has_move = any(a["action"] in MOVEMENT_ACTIONS for a in core)
        if has_move and not has_nav:
            failures.append({
                "type": "WALK_WITHOUT_NAVIGATE",
                "detail": "有移动动作但无NAVIGATE",
                "severity": "P1"
            })

        # --- P2 ---

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

        if hri is True:
            has_social = any(a["action"] in SOCIAL_ACTIONS for a in core)
            if not has_social:
                failures.append({
                    "type": "MISSING_SOCIAL",
                    "detail": "HRI=是但没有社交动作",
                    "severity": "P1"
                })

        if not any(a["action"] == "OBSERVE" for a in core):
            failures.append({
                "type": "MISSING_OBSERVE",
                "detail": "输出中没有任何 OBSERVE 动作",
                "severity": "P2"
            })

        return failures

    # ==================== 物体/位置匹配 ====================

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
        return bool(re.search(r'[A-Za-z0-9]', s))

    def _target_matches_object(self, pred_target: str, obj_name: str,
                                obj_aliases: List[str], strict: bool = False) -> float:
        pn = self._normalize_target(pred_target)

        def _substr_score(a: str, b: str) -> float:
            if a == b:
                return 1.0
            if a in b or b in a:
                if strict and self._has_index_suffix(a) != self._has_index_suffix(b):
                    return 0.3
                return 0.8
            return 0.0

        score = _substr_score(pn, obj_name)
        if score >= 0.8:
            return score

        for alias in obj_aliases:
            score = _substr_score(pn, alias)
            if score >= 0.8:
                return score

        all_variants = self._resolve_aliases(obj_name, self.OBJECT_ALIASES)
        for v in all_variants:
            score = _substr_score(pn, v)
            if score == 1.0:
                return 0.9
            if score == 0.8:
                if strict and self._has_index_suffix(pn) != self._has_index_suffix(v):
                    return 0.3
                return 0.7

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

        if pred_hri is None:
            hri_score = 0.0
            hri_detail = "未提取HRI"
        else:
            hri_correct = pred_hri == self.gold_hri
            hri_score = 1.0 if hri_correct else 0.0
            hri_detail = "正确" if hri_correct else f"错误(应为{'是' if self.gold_hri else '否'})"
        details["hri"] = {"score": hri_score, "detail": hri_detail}

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
        """目标达成 (40%)"""
        details = {}
        core_pred = self._get_core_actions(pred_actions)
        core_gold = {aid: a for aid, a in self.gold_actions.items()
                     if a.action not in INFRA_ACTIONS}

        if not core_gold:
            return 1.0, {"coverage": {"score": 1.0}, "target": {"score": 1.0},
                         "objects": {"score": 1.0}, "locations": {"score": 1.0}}

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

        obj_score, obj_detail = self._score_object_chain(core_pred)
        details["objects"] = obj_detail

        loc_score, loc_detail = self._score_location_coverage(pred_actions)
        details["locations"] = loc_detail

        total = 0.30 * coverage + 0.30 * target_score + 0.20 * obj_score + 0.20 * loc_score
        return round(total, 3), details

    def _score_object_chain(self, core_pred: List[Dict]) -> Tuple[float, Dict]:
        if not self.objects_required:
            return 1.0, {"score": 1.0, "detail": "无required objects", "chain_details": []}

        chain_scores = []
        chain_details = []

        for obj in self.objects_required:
            obj_name = obj["name"]
            obj_aliases = obj.get("aliases", [obj_name])
            required_actions = obj.get("actions", ["FIND", "CARRY", "DROP"])

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
        """执行质量 (20%)"""
        details = {}
        core_pred = self._get_core_actions(pred_actions)
        core_gold = {aid: a for aid, a in self.gold_actions.items()
                     if a.action not in INFRA_ACTIONS}

        if not core_gold:
            return 1.0, {}

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

        order_penalty = min(1.0, carry_without_find_count * 0.3 + walk_without_nav_count * 0.2)
        order_score = max(0.0, 1.0 - order_penalty)
        details["order"] = {
            "score": round(order_score, 3),
            "carry_without_find": carry_without_find_count,
            "walk_without_nav": walk_without_nav_count,
        }

        total = 0.6 * dep_score + 0.4 * order_score
        return round(total, 3), details

    def score_subtask_quality(self, pred_subtasks: List[str]) -> Tuple[float, Dict]:
        """子任务质量 (10%) — v5: 取代 score_stage_quality"""
        if not pred_subtasks:
            return 0.0, {"match": {"score": 0.0, "detail": "未提取子任务"}}

        used_pred = set()
        match_scores = []
        for gs in self.gold_subtasks:
            best = 0.0
            best_idx = -1
            for i, ps in enumerate(pred_subtasks):
                if i in used_pred:
                    continue
                sim = self._subtask_similarity(gs, ps)
                if sim > best:
                    best = sim
                    best_idx = i
            if best > 0.2:
                match_scores.append(best)
                used_pred.add(best_idx)

        coverage = sum(match_scores) / max(len(self.gold_subtasks), 1)
        extra = max(0, len(pred_subtasks) - len(used_pred))
        missing = max(0, len(self.gold_subtasks) - len(match_scores))
        penalty = min(extra * 0.02, 0.10) + min(missing * 0.03, 0.12)
        score = max(0.0, coverage - penalty)

        return round(score, 3), {
            "match": {"score": round(score, 3),
                      "detail": f"gold={self.gold_subtasks}, pred={pred_subtasks}, matched={len(match_scores)}/{len(self.gold_subtasks)}"}
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
    def _subtask_similarity(cls, gs: str, ps: str) -> float:
        """v5: 子任务语义相似度（继承 stage_similarity 逻辑）"""
        if gs == ps:
            return 1.0
        if gs in ps or ps in gs:
            return 0.80

        # 查 SUBTASK_SYNONYMS: 对 gold 子任务的每个词查近义词
        for key, syns in SUBTASK_SYNONYMS.items():
            if key in gs:
                for syn in syns:
                    if syn in ps:
                        return 0.85

        # 反向: 对 pred 子任务的词查近义词
        for key, syns in SUBTASK_SYNONYMS.items():
            if key in ps:
                for syn in syns:
                    if syn in gs:
                        return 0.80

        # 字符级 Jaccard
        g_chars, p_chars = set(gs), set(ps)
        if g_chars and p_chars:
            sim = len(g_chars & p_chars) / len(g_chars | p_chars)
            if sim > 0.3:
                return sim * 0.55
        return 0.0

    # ==================== 总评估入口 ====================

    def evaluate(self, model_raw_output: str, instruction: str = "") -> Dict:
        """v5 评估入口: 四维度加权(含子任务) + 失败模式扣分"""
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
                subtask_score, _ = self.score_subtask_quality(parsed["subtasks"])
                total = w["goal"] * goal + w["execution"] * exec_score + w["subtask"] * subtask_score
                if total > best_total:
                    best_total = total
                    best_bp = bp

            self.gold_actions = best_bp["_gold_actions"]
            self.gold_deps = {aid: set(a.deps) for aid, a in best_bp["_gold_actions"].items()}
            goal_score, goal_detail = self.score_goal_achievement(pred_actions)
            exec_score, exec_detail = self.score_execution_quality(pred_actions)
            subtask_score, subtask_detail = self.score_subtask_quality(parsed["subtasks"])

            self.gold_actions = saved_actions
            self.gold_deps = saved_deps
        else:
            goal_score, goal_detail = self.score_goal_achievement(pred_actions)
            exec_score, exec_detail = self.score_execution_quality(pred_actions)
            subtask_score, subtask_detail = self.score_subtask_quality(parsed["subtasks"])

        # 2. 失败模式检测
        failures = self._detect_failure_modes(parsed, instruction)

        # 3. 计算总分
        base_total = (safety_score * w["safety"] +
                      goal_score * w["goal"] +
                      exec_score * w["execution"] +
                      subtask_score * w["subtask"])

        p0_count = sum(1 for f in failures if f["severity"] == "P0")
        p1_count = sum(1 for f in failures if f["severity"] == "P1")
        p2_count = sum(1 for f in failures if f["severity"] == "P2")

        penalty = p0_count * self.P0_PENALTY + p1_count * self.P1_PENALTY + p2_count * self.P2_PENALTY
        final = max(0.0, base_total - penalty)

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
                "subtask": {"score": subtask_score, **subtask_detail},
                "v5_object_chain": obj_subscore,
                "v5_location_coverage": loc_subscore,
            },
            "failures": failures,
            "failure_types": failure_types,
            "failure_summary": f"P0={p0_count} P1={p1_count} P2={p2_count}",
            "parsed": parsed,
        }


# ==================== 自测 ====================

if __name__ == "__main__":
    import sys

    testset_path = "data/task_plan_testset_subtask.json"
    if not __import__("os").path.exists(testset_path):
        # Fallback to v3 if subtask testset not yet generated
        testset_path = "data/task_plan_testset_v3.json"

    try:
        with open(testset_path, "r", encoding="utf-8") as f:
            testset = json.load(f)

        print("=" * 72)
        print("  TaskPlanEvaluatorV5 自测")
        print("=" * 72)

        case = testset[0]
        evaluator = TaskPlanEvaluatorV5(case)

        # Get subtasks from case (v5 format) or fallback
        gold_subtasks = case.get("subtasks_expected", case.get("stages_expected", []))

        actions_str = "\n".join(
            f"{a['id']}. {a['action']}|{a['target']}"
            for a in case["actions_gold"]
        )
        model_output = (
            f"HRI：{'是' if case['hri_expected'] else '否'}\n"
            f"子任务：{'→'.join(gold_subtasks)}\n\n"
            f"{actions_str}"
        )

        result = evaluator.evaluate(model_output, case["instruction"])
        print(f"\nTest 1 — {case['test_id']}: {case['instruction']}")
        print(f"  Total: {result['total_score']}  Base: {result['base_score']}  Penalty: {result['penalty']}")
        print(f"  Failures: {result['failure_summary']}")
        assert result['total_score'] >= 0.95, f"Perfect match should score ≥0.95, got {result['total_score']}"

        # Test 2: subtask mismatch
        bad_subtask_output = (
            f"HRI：{'是' if case['hri_expected'] else '否'}\n"
            f"子任务：错误的子任务A→错误的子任务B\n\n"
            f"{actions_str}"
        )
        result2 = evaluator.evaluate(bad_subtask_output, case["instruction"])
        print(f"\nTest 2 — Subtask mismatch:")
        print(f"  Subtask score: {result2['breakdown']['subtask']['score']}")
        subtask_detail = result2['breakdown']['subtask']['match'].get('detail', '')
        print(f"  Detail: {subtask_detail}")
        # Should be lower than perfect match
        assert result2['breakdown']['subtask']['score'] < 0.9, \
            f"Subtask mismatch should lower score"

        # Test 3: backward compat — parsed from 阶段 format
        backward_output = (
            f"HRI：{'是' if case['hri_expected'] else '否'}\n"
            f"阶段：{'→'.join(gold_subtasks)}\n\n"
            f"{actions_str}"
        )
        parsed_bw = evaluator.parse_model_output(backward_output)
        assert len(parsed_bw["subtasks"]) > 0, "Should parse subtasks from 阶段 format"
        print(f"\nTest 3 — Backward compat (阶段→子任务): parsed {len(parsed_bw['subtasks'])} subtasks")

        print(f"\n{'='*72}")
        print(f"  全部 3 项自测通过")
        print(f"{'='*72}")

    except FileNotFoundError:
        print(f"测试集文件 {testset_path} 未找到")
        sys.exit(1)
    except AssertionError as e:
        print(f"\n自测失败: {e}")
        sys.exit(1)
