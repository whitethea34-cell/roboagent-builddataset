#!/usr/bin/env python3
"""
LLM 自动标注脚本：将 v3 测试集的粗粒度阶段标注转换为子任务标注。
调用 Qwen3-8B (port 8084) 对每题进行子任务分解 + action 重映射。

输出: data/task_plan_testset_subtask.json
"""

import json
import time
import sys
import requests
from pathlib import Path

ANNOTATOR_PORT = 8084
INPUT_TESTSET = "data/task_plan_testset_v3.json"
OUTPUT_TESTSET = "data/task_plan_testset_subtask.json"

ANNOTATION_PROMPT = """你是机器狗任务规划标注专家。请将以下任务从"粗粒度阶段"重新标注为"细粒度子任务"。

【子任务命名规范】
- 格式：动词+对象（如"查找快递"、"导航至301室"、"与小王交互"、"确认环境安全"）
- 禁止使用调度词：准备、阶段一、执行中
- 每个子任务必须可执行、可验证、服务于总任务目标
- 3-6个子任务为宜

【任务信息】
- 任务ID: {test_id}
- 指令: {instruction}
- 场景: {scene}
- HRI: {hri}
- 原阶段: {old_stages}

【Gold Actions（带原阶段标注）】
{gold_actions_text}

【要求】
1. 读懂指令和场景，按子任务命名规范拆解为3-6个子任务
2. 将每个 gold action 分配到对应的子任务（不要修改 action 本身）
3. 只输出一个 JSON 对象，不要任何其他文字：

{{
  "subtasks_expected": ["子任务1", "子任务2", ...],
  "actions_remapped": {{"1": "子任务X", "2": "子任务Y", ...}}
}}"""


def load_testset(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_gold_actions(case: dict) -> str:
    lines = []
    for a in case["actions_gold"]:
        phase = a.get("phase", a.get("subtask", ""))
        lines.append(f"  {a['id']}. {a['action']}|{a['target']}  [原阶段: {phase}]")
    return "\n".join(lines)


def annotate_case(case: dict, port: int) -> dict:
    """调用 LLM 标注单个 case，返回更新后的 case"""
    prompt = ANNOTATION_PROMPT.format(
        test_id=case["test_id"],
        instruction=case["instruction"],
        scene=case.get("scene", ""),
        hri="是" if case["hri_expected"] else "否",
        old_stages=case.get("stages_expected", []),
        gold_actions_text=format_gold_actions(case),
    )

    for attempt in range(3):
        try:
            resp = requests.post(
                f"http://localhost:{port}/v1/chat/completions",
                json={
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1024,
                    "stop": ["```"],
                },
                timeout=120,
            )
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()

            # Extract JSON block
            if "```json" in reply:
                reply = reply.split("```json")[1].split("```")[0].strip()
            elif "```" in reply:
                reply = reply.split("```")[1].split("```")[0].strip()

            annotation = json.loads(reply)

            # Validate
            if "subtasks_expected" not in annotation:
                raise ValueError("Missing subtasks_expected")
            if "actions_remapped" not in annotation:
                raise ValueError("Missing actions_remapped")

            # Build new case
            new_case = {
                "test_id": case["test_id"],
                "category": case["category"],
                "task_type": case.get("task_type", ""),
                "difficulty": case.get("difficulty", ""),
                "instruction": case["instruction"],
                "scene": case.get("scene", ""),
                "hri_expected": case["hri_expected"],
                "subtasks_expected": annotation["subtasks_expected"],
            }

            # Copy optional fields
            for key in ["objects_required", "locations_required",
                        "scoring_rules", "branch_paths"]:
                if key in case:
                    new_case[key] = case[key]

            # Remap actions
            new_actions = []
            remap = annotation["actions_remapped"]
            for a in case["actions_gold"]:
                aid = str(a["id"])
                new_a = dict(a)
                new_a["subtask"] = remap.get(aid, new_a.get("phase", ""))
                new_a.pop("phase", None)
                new_a.pop("phase_original", None)
                new_actions.append(new_a)

            new_case["actions_gold"] = new_actions
            return new_case

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            if attempt < 2:
                time.sleep(1)
                continue
            print(f"  !! LLM 输出解析失败 (尝试{attempt+1}/3): {e}")
            print(f"  !! 原始回复: {reply[:300]}")
            raise


def main():
    print("=" * 60)
    print("  子任务标注脚本 — Qwen3-8B (port {})".format(ANNOTATOR_PORT))
    print("=" * 60)

    # Check annotator is alive
    try:
        r = requests.get(f"http://localhost:{ANNOTATOR_PORT}/health", timeout=5)
        print(f"  Annotator health: {r.status_code}")
    except Exception:
        print(f"  [ERROR] 无法连接 annotator port {ANNOTATOR_PORT}，请先启动 Qwen3-8B")
        sys.exit(1)

    testset = load_testset(INPUT_TESTSET)
    print(f"  输入: {INPUT_TESTSET} ({len(testset)} 题)")

    new_testset = []
    for i, case in enumerate(testset):
        tid = case["test_id"]
        old_stages = case.get("stages_expected", [])
        print(f"\n[{i+1}/{len(testset)}] {tid}: {case['instruction'][:40]}...")
        print(f"  原阶段: {old_stages}")

        try:
            new_case = annotate_case(case, ANNOTATOR_PORT)
            new_subtasks = new_case["subtasks_expected"]
            print(f"  新子任务: {new_subtasks}")

            # Print diff
            for a in new_case["actions_gold"]:
                old_phase = next(
                    (ga.get("phase", "") for ga in case["actions_gold"]
                     if ga["id"] == a["id"]), ""
                )
                if old_phase != a["subtask"]:
                    print(f"    [{a['id']}] {a['action']}|{a['target']}: "
                          f"{old_phase} → {a['subtask']}")

            new_testset.append(new_case)
            time.sleep(0.2)

        except Exception as e:
            print(f"  [FAIL] {e}")
            # Fallback: keep original with coarse subtask = original phase
            fallback = dict(case)
            fallback["subtasks_expected"] = case.get("stages_expected", [])
            for a in fallback["actions_gold"]:
                a["subtask"] = a.get("phase", "")
                a.pop("phase", None)
                a.pop("phase_original", None)
            fallback.pop("stages_expected", None)
            new_testset.append(fallback)

    # Save
    output_path = Path(OUTPUT_TESTSET)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(new_testset, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  标注完成: {len(new_testset)} 题")
    print(f"  输出: {OUTPUT_TESTSET}")
    print(f"  请人工审核后使用。")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
