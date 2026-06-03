#!/usr/bin/env python3
"""
机器狗任务规划批量测试脚本 (子任务版)
加载子任务版测试集 → 调用 4090 llama-server API → TaskPlanEvaluatorV5 评分 → 保存结果

与 batch_test_scheduler.py 的区别:
  - 使用 TaskPlanEvaluatorV5 (子任务评分引擎)
  - 默认测试集: data/task_plan_testset_subtask.json
  - 显示/解析均使用"子任务"替代"阶段"

用法:
  # 离线自测 (需先有 subtask testset)
  python batch_test_scheduler_subtask.py --offline --tag test

  # 调用 0.8B 模型
  python batch_test_scheduler_subtask.py --port 8083 --tag v15-subtask

  # 使用外部提示词
  python batch_test_scheduler_subtask.py --port 8083 --prompt-file config/prompt_v15_subtask.txt --tag v15-subtask

  # 只测几题
  python batch_test_scheduler_subtask.py --port 8083 --ids DOG-A01,DOG-B01,DOG-E01
"""

import json
import time
import argparse
import sys
import hashlib
from datetime import datetime
from pathlib import Path
import requests
from task_plan_evaluator_v5 import TaskPlanEvaluatorV5


# ============================================================
# 工具函数
# ============================================================

def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_display() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_testset(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _make_mock_output(case: dict) -> str:
    """v5: 用子任务格式生成 mock 输出"""
    hri = "是" if case["hri_expected"] else "否"
    subtasks = case.get("subtasks_expected", case.get("stages_expected", []))
    subtask_str = "→".join(subtasks)
    actions = "\n".join(
        f"{a['id']}. {a['action']}|{a['target']}" for a in case["actions_gold"]
    )
    return f"HRI：{hri}\n子任务：{subtask_str}\n\n{actions}"


# ============================================================
# 摘要 TXT 生成
# ============================================================

def generate_summary_txt(results: list, meta: dict, summary_path: str) -> None:
    lines = []
    sep = "=" * 72

    lines.append(sep)
    lines.append("  机器狗任务规划测试 (v5 子任务评分) — 单轮结果")
    lines.append(sep)
    lines.append(f"  时间:         {meta['timestamp_display']}")
    lines.append(f"  提示词标签:   {meta['tag']}")
    lines.append(f"  提示词 MD5:   {meta['prompt_md5']}")
    lines.append(f"  模型端口:     {meta.get('port', 'N/A')}")
    lines.append(f"  温度:         {meta.get('temperature', 'N/A')}")
    lines.append(f"  总题数:       {meta['total']}")
    lines.append(f"  评分引擎:     TaskPlanEvaluatorV5")
    lines.append(f"  JSON 文件:    {meta['json_file']}")
    lines.append("")

    lines.append("-" * 72)
    lines.append("  【一、分数汇总】")
    lines.append("-" * 72)
    lines.append(f"  总分平均:     {meta['avg_score']:.4f}")
    lines.append("")

    by_category = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["score"])
    for cat in sorted(by_category.keys()):
        scores = by_category[cat]
        lines.append(
            f"  {cat}类 ({len(scores)}题):  "
            f"平均 {sum(scores)/len(scores):.4f}  "
            f"最低 {min(scores):.4f}  "
            f"最高 {max(scores):.4f}"
        )
    lines.append("")

    # P0/P1/P2 summary
    all_failures = {}
    for r in results:
        for f in r.get("failures", []):
            all_failures[f["type"]] = all_failures.get(f["type"], 0) + 1
    if all_failures:
        lines.append("-" * 72)
        lines.append("  【失败模式汇总】")
        lines.append("-" * 72)
        for ft, count in sorted(all_failures.items(), key=lambda x: -x[1]):
            sev = "P0" if "HALLUCINATED" in ft or "MISSING_FIND_FOR" in ft or "CARRY_HUMAN" in ft else \
                  "P1" if "MISSING_DROP" in ft or "MISSING_CARRY" in ft or "REDUNDANT" in ft or \
                  "EMERGENCY" in ft or "CONDITION" in ft or "WALK_WITHOUT" in ft or "MISSING_SOCIAL" in ft else "P2"
            lines.append(f"  [{sev}] {ft}: {count}次")

    lines.append("")
    lines.append("")

    lines.append("-" * 72)
    lines.append("  【二、使用的提示词内容】")
    lines.append("-" * 72)
    for prompt_line in meta["prompt"].split("\n"):
        lines.append(f"  {prompt_line}")

    lines.append("")
    lines.append(sep)
    lines.append(f"  详细每题得分与模型输出请查看同名 JSON 文件")
    lines.append(f"  多轮汇总: python generate_summary.py")
    lines.append(sep)

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n摘要已保存: {summary_path}")


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="机器狗任务规划批量测试 (v5 子任务评分)")
    parser.add_argument("--testset", default="data/task_plan_testset_subtask.json",
                        help="子任务版测试集 JSON 路径")
    parser.add_argument("--port", type=int, default=None,
                        help="llama-server 端口 (4090: 8081=2B, 8082=4B, 8083=0.8B)")
    parser.add_argument("--output", default=None,
                        help="结果输出 JSON 路径（默认自动生成时间戳文件名）")
    parser.add_argument("--tag", default="default",
                        help="提示词版本标签（用于区分不同实验）")
    parser.add_argument("--prompt-file", default=None,
                        help="外部提示词文件路径（不指定则使用内置默认提示词）")
    parser.add_argument("--temperature", type=float, default=0.3, help="模型温度")
    parser.add_argument("--ids", default=None,
                        help="只测试指定题目ID，逗号分隔")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式：用 gold 数据模拟输出，验证评分引擎")
    parser.add_argument("--no-summary", action="store_true",
                        help="不生成 summary TXT 文件")
    args = parser.parse_args()

    # ---- 加载提示词 ----
    if args.prompt_file:
        prompt = load_prompt_file(args.prompt_file)
        print(f"加载外部提示词: {args.prompt_file}")
    else:
        print("[WARN] 未指定 --prompt-file，请使用 --prompt-file 指定提示词文件")
        print("  例如: --prompt-file config/prompt_v15_subtask.txt")
        prompt = ""
        if args.offline:
            prompt = "(offline mode — no prompt needed)"

    prompt_md5 = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8] if prompt else "no_prompt"

    # ---- 确定输出文件名 ----
    ts = now_str()
    if args.output:
        json_path = args.output
    else:
        json_path = f"results/results_{args.tag}_{ts}.json"

    summary_path = json_path.rsplit(".", 1)[0] + "_summary.txt"

    # ---- 加载测试集 ----
    testset = load_testset(args.testset)
    if args.ids:
        id_set = set(args.ids.split(","))
        testset = [c for c in testset if c["test_id"] in id_set]
        print(f"筛选题目: {len(testset)} 题")
    print(f"加载测试集: {len(testset)} 题 ({args.testset})")
    print(f"评分引擎: TaskPlanEvaluatorV5")
    print(f"提示词标签: {args.tag}  (MD5: {prompt_md5})")
    if args.port:
        print(f"模型端口: localhost:{args.port}  |  温度: {args.temperature}")

    # ---- 逐题测试 ----
    results = []
    for case in testset:
        print(f"\n{'=' * 50}")
        print(f"测试: {case['test_id']} [{case['category']}类] | {case['instruction']}")

        if args.offline:
            reply = _make_mock_output(case)
            elapsed = 0.0
            usage = {}
        elif args.port:
            try:
                t0 = time.time()
                api_resp = requests.post(
                    f"http://localhost:{args.port}/v1/chat/completions",
                    json={
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": f"【场景】{case['scene']}\n【指令】{case['instruction']}"},
                        ],
                        "temperature": args.temperature,
                        "max_tokens": 384,
                        "stop": ["【", "\n\n\n"],
                    },
                    timeout=120,
                )
                elapsed = round(time.time() - t0, 2)
                data = api_resp.json()
                reply = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
            except Exception as e:
                reply = f"[模型调用失败: {e}]"
                elapsed = 0.0
                usage = {}
        else:
            reply = _make_mock_output(case)
            elapsed = 0.0
            usage = {}
            print("  (未指定 --port，使用离线模拟)")

        evaluator = TaskPlanEvaluatorV5(case)
        score_result = evaluator.evaluate(reply)

        record = {
            "test_id": case["test_id"],
            "category": case["category"],
            "instruction": case["instruction"],
            "raw_output": reply,
            "elapsed": elapsed,
            "usage": usage,
            "score": score_result["total_score"],
            "breakdown": score_result["breakdown"],
            "failures": score_result.get("failures", []),
            "failure_types": score_result.get("failure_types", []),
        }
        results.append(record)

        print(f"  总分: {score_result['total_score']:.3f} | 耗时: {elapsed:.2f}s")
        bd = score_result["breakdown"]
        safety_s = bd["safety"]["score"]
        goal_s = bd["goal"]["score"]
        exec_s = bd["execution"]["score"]
        subtask_s = bd["subtask"]["score"]
        penalty = score_result.get("penalty", 0)
        print(f"  安全:{safety_s:.2f} 目标:{goal_s:.2f} 执行:{exec_s:.2f} 子任务:{subtask_s:.2f} 罚分:{penalty:.3f}")
        if score_result.get("failures"):
            print(f"  失败模式: {score_result['failure_summary']}")
        if score_result["total_score"] < 0.8:
            hri_detail = bd["safety"]["hri"].get("detail", "")
            goal_detail = bd["goal"]["coverage"].get("detail", "")
            subtask_detail = bd["subtask"]["match"].get("detail", "")
            print(f"  [!] HRI:{hri_detail} | 目标:{goal_detail[:60]} | 子任务:{subtask_detail[:60]}")
            print(f"  输出片段:\n{reply[:200]}...")

        time.sleep(0.3)

    # ---- 控制台汇总 ----
    if results:
        avg_score = sum(r["score"] for r in results) / len(results)
        by_category = {}
        for r in results:
            by_category.setdefault(r["category"], []).append(r["score"])
        print(f"\n{'=' * 60}")
        print(f"测试完成: {len(results)} 题 | 总分平均: {avg_score:.4f}  (v5 子任务评分)")
        for cat in sorted(by_category.keys()):
            scores = by_category[cat]
            print(f"  {cat} 类: {len(scores)}题 | 平均: {sum(scores)/len(scores):.4f}")
        print(f"{'=' * 60}")

    # ---- 保存 JSON ----
    meta = {
        "timestamp": ts,
        "timestamp_display": now_display(),
        "tag": args.tag,
        "prompt_md5": prompt_md5,
        "port": args.port,
        "temperature": args.temperature,
        "total": len(results),
        "avg_score": sum(r["score"] for r in results) / len(results) if results else 0,
        "evaluator": "TaskPlanEvaluatorV5",
        "prompt": prompt,
        "testset": args.testset,
        "json_file": json_path,
    }

    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "details": results}, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {json_path}")

    # ---- 生成 Summary TXT ----
    if not args.no_summary:
        generate_summary_txt(results, meta, summary_path)


if __name__ == "__main__":
    main()
