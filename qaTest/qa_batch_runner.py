#!/usr/bin/env python3
"""
Q&A 诊断评测批量测试脚本
加载 qa_testset.json → 逐题调用 llama-server API → 评分 → 按维度聚合

用法:
  # 冒烟测试（5题快速验证）
  python qa_batch_runner.py --port 8083 --limit 5 --tag smoke-test

  # 完整测试
  python qa_batch_runner.py --port 8083 --tag prompt-v1

  # 离线自测
  python qa_batch_runner.py --offline --tag self-test

  # 使用外部提示词
  python qa_batch_runner.py --port 8083 --prompt-file prompts/prompt_v1.txt --tag v1

  # 筛选维度
  python qa_batch_runner.py --port 8083 --dimension D2 --tag d2-test

  # 筛选来源题型
  python qa_batch_runner.py --port 8083 --source DOG-B01 --tag b01-test
"""

import json
import time
import argparse
import hashlib
import random
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import requests
from qa_evaluator import QAEvaluator

# ============================================================
# 默认提示词
# ============================================================

DEFAULT_QA_PROMPT = """你是机器狗任务规划诊断评测的答题助手。根据给定的场景和问题，选择最正确的答案。

【答题格式】
- 单选题：输出"答案：X"（X为选项字母A/B/C/D）
- 判断题：输出"答案：是"或"答案：否"
- 多选题：输出"答案：A,C"（多个选项用逗号分隔）

【领域知识】
机器狗具有以下能力：
- 状态感知：OBSERVE(观察)、FIND(搜索)、SELF_CHECK(自检)、RANGE(测距)
- 导航移动：NAVIGATE(导航)、WALK(行走)、TROT(小跑)、CLIMB(爬楼梯)、AVOID(避障)
- 肢体动作：WAVE(招手)、BOW(鞠躬)、NOD(点头)、SHAKE_HEAD(摇头)、JUMP(跳跃)、DANCE(舞蹈)
- 交互运载：CARRY(搬运)、DROP(卸货)、PUSH(推)、SPEAK(语音)、BEEP(提示音)
- 状态灯：LED_BLUE(执行中)、LED_GREEN(完成)、LED_RED(异常)、LED_WHITE(待命)

【核心规则】
- 搬运物品前必须先 FIND（找到）再 CARRY（搬运）
- 绝对不能 CARRY（搬运）人类
- HRI=是 的任务需要社交动作（SPEAK/WAVE/BOW）
- 检测到危险时需 EMERGENCY（紧急制动）+ LED_RED
- NAVIGATE 是导航规划，WALK/TROT 是实际移动执行

请只输出答案，不要解释。"""


# ============================================================
# 工具函数
# ============================================================

def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def now_display() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def load_prompt_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ============================================================
# 摘要生成
# ============================================================

def generate_summary_txt(results: dict, meta: dict, summary_path: str) -> None:
    """生成单轮 Q&A 结果摘要 TXT"""
    lines = []
    sep = "=" * 72

    lines.append(sep)
    lines.append("  机器狗 Q&A 诊断评测 — 单轮结果")
    lines.append(sep)
    lines.append(f"  时间:         {meta['timestamp_display']}")
    lines.append(f"  标签:         {meta['tag']}")
    lines.append(f"  提示词 MD5:   {meta['prompt_md5']}")
    lines.append(f"  模型端口:     {meta.get('port', 'N/A')}")
    lines.append(f"  温度:         {meta.get('temperature', 'N/A')}")
    lines.append(f"  总题数:       {meta['total_questions']}")
    lines.append(f"  总准确率:     {meta['overall_accuracy']:.4f}")
    lines.append("")

    # 分维度
    lines.append("-" * 72)
    lines.append("  【分维度准确率】")
    lines.append("-" * 72)
    dim_scores = results.get("dimension_scores", {})
    for dim in ["D1", "D2", "D3", "D4", "D5"]:
        ds = dim_scores.get(dim, {})
        if ds:
            name = ds.get("name", "")
            acc = ds.get("accuracy", 0)
            c = ds.get("correct", 0)
            t = ds.get("total", 0)
            lines.append(f"  {dim} {name:<14}  {acc:.4f}  ({c}/{t})")
    lines.append("")

    # 提示词
    lines.append("-" * 72)
    lines.append("  【提示词全文】")
    lines.append("-" * 72)
    for pl in meta.get("prompt", "").split("\n"):
        lines.append(f"  {pl}")
    lines.append("")
    lines.append(sep)

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"摘要已保存: {summary_path}")


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="机器狗 Q&A 诊断评测批量测试")
    parser.add_argument("--testset", default="qa_testset.json", help="Q&A 测试集路径")
    parser.add_argument("--port", type=int, default=None, help="llama-server 端口")
    parser.add_argument("--output", default=None, help="输出 JSON 路径")
    parser.add_argument("--tag", default="default", help="提示词版本标签")
    parser.add_argument("--prompt-file", default=None, help="外部提示词文件路径")
    parser.add_argument("--temperature", type=float, default=0.3, help="温度")
    parser.add_argument("--limit", type=int, default=0,
                        help="[已废弃] 取前N题（用 --sample 实现分层抽样）")
    parser.add_argument("--sample", type=int, default=0,
                        help="分层抽样题数（按D1-D5各维度占比随机采样，用于快速验证）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（与 --sample 配合）")
    parser.add_argument("--dimension", default=None, help="只测试指定维度(D1-D5)")
    parser.add_argument("--source", default=None, help="只测试指定来源题(如DOG-B01)")
    parser.add_argument("--offline", action="store_true", help="离线自测模式")
    parser.add_argument("--no-summary", action="store_true", help="不生成摘要文件")
    args = parser.parse_args()

    # ---- 加载提示词 ----
    if args.prompt_file:
        prompt = load_prompt_file(args.prompt_file)
        print(f"加载外部提示词: {args.prompt_file}")
    else:
        prompt = DEFAULT_QA_PROMPT
        print("使用内置默认 Q&A 提示词")
    prompt_md5 = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]

    # ---- 加载测试集 ----
    evaluator = QAEvaluator(args.testset)
    questions = evaluator.questions

    if args.dimension:
        questions = [q for q in questions if q["dimension"] == args.dimension]
        print(f"筛选维度 {args.dimension}: {len(questions)} 题")
    if args.source:
        questions = [q for q in questions if q.get("source_test_id") == args.source]
        print(f"筛选来源 {args.source}: {len(questions)} 题")

    # ---- 分层抽样（替代旧的 --limit 取前N题） ----
    if args.sample > 0:
        random.seed(args.seed)
        # 按维度分组
        by_dim = defaultdict(list)
        for q in questions:
            by_dim[q["dimension"]].append(q)

        # 按各维度占比分配配额
        total = len(questions)
        dim_quotas = {}
        remaining = args.sample
        dims_sorted = sorted(by_dim.keys())
        for i, dim in enumerate(dims_sorted):
            if i == len(dims_sorted) - 1:
                dim_quotas[dim] = remaining  # 最后一个维度拿剩余的全部
            else:
                quota = max(1, round(args.sample * len(by_dim[dim]) / total))
                quota = min(quota, len(by_dim[dim]))  # 不超过该维度总题数
                dim_quotas[dim] = quota
                remaining -= quota

        # 从每个维度随机采样
        sampled = []
        for dim in dims_sorted:
            pool = by_dim[dim]
            quota = min(dim_quotas[dim], len(pool))
            sampled.extend(random.sample(pool, quota))

        random.shuffle(sampled)
        questions = sampled[:args.sample]  # 确保恰好 sample 题
        print(f"分层抽样 {args.sample} 题 (seed={args.seed}):")
        for dim in dims_sorted:
            n = sum(1 for q in questions if q["dimension"] == dim)
            total_dim = len(by_dim[dim])
            print(f"  {dim}: {n}/{total_dim}")
    elif args.limit:
        # 兼容旧的 --limit
        print("[!] --limit 已废弃，建议用 --sample 代替。当前取前 N 题。")
        questions = questions[:args.limit]

    total = len(questions)
    print(f"加载测试集: {total} 题")
    print(f"标签: {args.tag}  (MD5: {prompt_md5})")
    if args.port:
        print(f"模型端口: localhost:{args.port}  温度: {args.temperature}")

    # ---- 确定输出路径 ----
    ts = now_str()
    if args.output:
        json_path = args.output
    else:
        json_path = f"outputs/qa_results_{args.tag}_{ts}.json"
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    summary_path = json_path.rsplit(".", 1)[0] + "_summary.txt"

    # ---- 逐题测试 ----
    model_outputs = {}
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    total_elapsed = 0.0
    errors = []

    for i, q in enumerate(questions):
        qid = q["qa_id"]
        q_type = q["type"]

        # 构造用户消息
        scene = q.get("scene_context", "")
        question_text = q["question"]
        if q_type == "single_choice":
            options_text = "\n".join(f"{o['key']}. {o['text']}" for o in q.get("options", []))
            user_msg = f"{scene}\n\n{question_text}\n{options_text}" if scene else f"{question_text}\n{options_text}"
        elif q_type == "true_false":
            user_msg = f"{scene}\n\n{question_text}\n（请回答：是/否）" if scene else f"{question_text}\n（请回答：是/否）"
        else:
            user_msg = f"{scene}\n\n{question_text}" if scene else question_text

        if args.offline:
            # 离线模式：用 gold 答案构造完美回复
            gold = q["answer"]
            if q_type in ("single_choice", "multi_choice"):
                reply = f"答案：{gold}"
            elif q_type == "true_false":
                reply = "是" if gold is True or gold == "true" else "否"
            else:
                reply = str(gold)
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
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": args.temperature,
                        "max_tokens": 64,
                    },
                    timeout=60,
                )
                elapsed = round(time.time() - t0, 2)
                data = api_resp.json()
                reply = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
            except Exception as e:
                reply = f"[调用失败: {e}]"
                elapsed = 0.0
                usage = {}
                errors.append({"qa_id": qid, "error": str(e)})
        else:
            # 既没指定 --port 也没指定 --offline，默认离线
            gold = q["answer"]
            if q_type in ("single_choice", "multi_choice"):
                reply = f"答案：{gold}"
            elif q_type == "true_false":
                reply = "是" if gold is True or gold == "true" else "否"
            else:
                reply = str(gold)
            elapsed = 0.0
            usage = {}

        model_outputs[qid] = reply
        total_elapsed += elapsed
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage_total[k] += usage.get(k, 0)

        # 进度
        if (i + 1) % 20 == 0 or i == total - 1:
            print(f"  进度: {i+1}/{total}  ({(i+1)/total*100:.0f}%)")

        time.sleep(0.15)

    # ---- 评分 ----
    result = evaluator.evaluate_batch(model_outputs)

    # 附加每题原始输出到 details
    for d in result["details"]:
        d["raw_output"] = model_outputs.get(d["qa_id"], "")

    print(f"\n测试完成: {result['total_correct']}/{result['total_questions']}  "
          f"准确率: {result['overall_accuracy']:.4f}")
    for dim in ["D1", "D2", "D3", "D4", "D5"]:
        ds = result["dimension_scores"].get(dim, {})
        if ds:
            print(f"  {dim} {ds['name']}: {ds['accuracy']:.4f}")

    # ---- 保存 JSON ----
    meta = {
        "timestamp": ts,
        "timestamp_display": now_display(),
        "tag": args.tag,
        "prompt_md5": prompt_md5,
        "port": args.port,
        "temperature": args.temperature,
        "total_questions": result["total_questions"],
        "total_correct": result["total_correct"],
        "overall_accuracy": result["overall_accuracy"],
        "dimension_scores": result["dimension_scores"],
        "prompt": prompt,
        "testset": args.testset,
        "json_file": json_path,
        "elapsed_total": round(total_elapsed, 2),
        "usage_total": usage_total,
        "errors": errors,
    }

    output_data = {"meta": meta, "details": result["details"]}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {json_path}")

    # ---- 生成摘要 ----
    if not args.no_summary:
        generate_summary_txt(result, meta, summary_path)


if __name__ == "__main__":
    main()
