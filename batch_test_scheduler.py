#!/usr/bin/env python3
"""
机器狗任务规划批量测试脚本
加载 JSON 测试集 → 调用 4090 llama-server API → TaskPlanEvaluator 评分 → 保存结果

用法:
  # 离线自测
  python batch_test_scheduler.py --offline

  # 调用 0.8B 模型（自动生成带时间戳的结果文件和summary）
  python batch_test_scheduler.py --port 8083

  # 指定标签和输出前缀
  python batch_test_scheduler.py --port 8083 --tag prompt-v2

  # 使用外部提示词文件（方便对比不同版本）
  python batch_test_scheduler.py --port 8083 --prompt-file my_prompt.txt --tag exp1

  # 只测几题
  python batch_test_scheduler.py --port 8083 --ids DOG-A01,DOG-B01,DOG-E01
"""

import json
import time
import argparse
import sys
import hashlib
from datetime import datetime
from pathlib import Path
import requests
from task_plan_evaluator import TaskPlanEvaluator

# ============================================================
# 默认机器狗 SCHEDULER 提示词
# ============================================================
DOG_SKILL_LIB = """【状态灯】LED_BLUE(任务中-蓝灯闪烁) LED_GREEN(完成-绿灯常亮) LED_RED(异常-红灯闪烁) LED_WHITE(待命-白灯呼吸)
【状态感知】OBSERVE(观察环境) SELF_CHECK(自检电量/状态) FIND(搜索目标) RANGE(判断距离/空间)
【导航移动】NAVIGATE(导航到目标点) WALK(标准步态行走) TROT(快速小跑) BACK(倒退) TURN(转向) SPIN(原地旋转) CROUCH(匍匐通过低矮空间) CLIMB(爬楼梯/台阶) AVOID(动态避障) FOLLOW(跟随人员) RETURN(返回起点) STOP(停止待机)
【肢体动作】STAND(起立) LIE_DOWN(趴下) SIT(坐下) JUMP(跳跃) WAVE(招手) BOW(鞠躬) SHAKE(握手) NOD(点头确认) SHAKE_HEAD(摇头否定) ROLL(翻滚) DANCE(舞蹈) KICK(蹬腿)
【交互运载】CARRY(背负/运载物品) DROP(卸下载物) PUSH(推物体/推门) SPEAK(语音播报) BEEP(提示音) CAPTURE(拍照) WAIT(原地等待指令) EMERGENCY(紧急制动)"""

DOG_SUBTASK_LIB = """【任务调度】准备、出发、就位、待机、收尾、充电
【环境感知】观察、检测、检查、搜索、确认、察觉
【导航移动】前往、接近、抵达、通过、绕行、越障、攀爬、匍匐、推门、返回
【物品操作】取物、装载、运送、递送
【社交互动】迎接、互动、致意、告别、引导、表演
【巡逻巡检】巡逻、巡检、汇总
【安全异常】判断、响应警报、恢复、切换"""

DOG_SCHEDULER_PROMPT = (
    "你是机器狗任务规划器。将指令拆解为分阶段动作序列。\n\n"
    "规则：\n"
    "1. 先判断 HRI：指令涉及\"我/你/客人/递给/告知/引导/陪伴/打招呼\""
    "→HRI=是；纯物体操作/巡逻/检查/充电→HRI=否。"
    "注意：\"送给XX/人\"→HRI=是，\"送到XX/地点\"→HRI=否\n"
    "2. HRI=是：最终阶段必须含 NAVIGATE(靠近人)+社交动作(SPEAK/WAVE/BOW)\n"
    "3. 物体依赖：拿物品前必须先FIND再CARRY。人≠物品，对人只能用SPEAK/WAVE/BOW，不能用CARRY\n"
    "4. 每个任务必须包含环境感知动作（OBSERVE 观察环境 和/或 SELF_CHECK 自检状态），不可跳过\n"
    "5. 每个子任务开始前亮 LED_BLUE，结束后亮 LED_GREEN，异常亮 LED_RED\n"
    "6. 阶段名称必须严格从子任务标签中选词。多点场景可加字母/数字后缀（如巡逻R1、前往A）。禁止使用类别名（如\"社交互动\"）或自创词\n"
    "7. 输出格式：HRI：是/否 | 阶段：A→B→C\n"
    "   序号. 动作|目标\n\n"
    "【状态灯】LED_BLUE(任务中-蓝灯闪烁) LED_GREEN(完成-绿灯常亮) "
    "LED_RED(异常-红灯闪烁) LED_WHITE(待命-白灯呼吸)\n"
    "【状态感知】OBSERVE(观察环境) SELF_CHECK(自检电量/状态) FIND(搜索目标) RANGE(判断距离/空间)\n"
    "【导航移动】NAVIGATE(导航到目标点) WALK(标准步态行走) TROT(快速小跑) "
    "BACK(倒退) TURN(转向) SPIN(原地旋转) CROUCH(匍匐通过低矮空间) "
    "CLIMB(爬楼梯/台阶) AVOID(动态避障) FOLLOW(跟随人员) RETURN(返回起点) STOP(停止待机)\n"
    "【肢体动作】STAND(起立) LIE_DOWN(趴下) SIT(坐下) JUMP(跳跃) WAVE(招手) "
    "BOW(鞠躬) SHAKE(握手) NOD(点头确认) SHAKE_HEAD(摇头否定) ROLL(翻滚) DANCE(舞蹈) KICK(蹬腿)\n"
    "【交互运载】CARRY(背负/运载物品) DROP(卸下载物) PUSH(推物体/推门) "
    "SPEAK(语音播报) BEEP(提示音) CAPTURE(拍照) WAIT(原地等待指令) EMERGENCY(紧急制动)\n\n"
    "【任务调度】准备、出发、就位、待机、收尾、充电\n"
    "【环境感知】观察、检测、检查、搜索、确认、察觉\n"
    "【导航移动】前往、接近、抵达、通过、绕行、越障、攀爬、匍匐、推门、返回\n"
    "【物品操作】取物、装载、运送、递送\n"
    "【社交互动】迎接、互动、致意、告别、引导、表演\n"
    "【巡逻巡检】巡逻、巡检、汇总\n"
    "【安全异常】判断、响应警报、恢复、切换\n\n"
    "【示例1：巡逻】\n"
    "指令：绕仓库巡逻一圈\n"
    "HRI：否 | 阶段：准备→巡逻→收尾\n"
    "1. LED_BLUE|状态灯 2. NAVIGATE|巡逻起点 3. WALK|沿巡逻路线\n"
    "4. OBSERVE|环境异常 5. SELF_CHECK|自身状态 6. LED_GREEN|状态灯\n"
    "7. RETURN|起点 8. STOP|待机\n\n"
    "【示例2：递送】\n"
    "指令：把门口的快递取来给我\n"
    "HRI：是 | 阶段：取物→运送→递送\n"
    "1. LED_BLUE|状态灯 2. NAVIGATE|玄关 3. WALK|前往玄关\n"
    "4. FIND|快递 5. SELF_CHECK|自身状态 6. CARRY|快递\n"
    "7. NAVIGATE|主人位置 8. WALK|返回 9. LED_GREEN|状态灯\n"
    "10. SPEAK|您的快递 11. DROP|快递放下 12. BOW|致意\n\n"
    "【示例3：社交】\n"
    "指令：有客人来了，去迎接一下\n"
    "HRI：是 | 阶段：察觉→迎接→致意\n"
    "1. LED_BLUE|状态灯 2. NAVIGATE|门口 3. WALK|前往门口\n"
    "4. FIND|客人 5. WAVE|打招呼 6. SPEAK|欢迎\n"
    "7. BOW|鞠躬 8. LED_GREEN|状态灯\n\n"
    "【示例4：充电】\n"
    "指令：电量低了，自己去充电\n"
    "HRI：否 | 阶段：检查→前往→就位\n"
    "1. SELF_CHECK|电量 2. LED_BLUE|状态灯 3. NAVIGATE|充电桩\n"
    "4. WALK|前往充电桩 5. BACK|对准充电桩 6. LED_GREEN|状态灯\n"
    "7. STOP|开始充电"
)


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
    hri = "是" if case["hri_expected"] else "否"
    stages = "→".join(case["stages_expected"])
    actions = "\n".join(
        f"{a['id']}. {a['action']}|{a['target']}" for a in case["actions_gold"]
    )
    return f"HRI：{hri}\n阶段：{stages}\n\n{actions}"


# ============================================================
# 摘要 TXT 生成
# ============================================================

def generate_summary_txt(results: list, meta: dict, summary_path: str) -> None:
    """生成单轮结果摘要 TXT（仅分数汇总 + 提示词全文）"""
    lines = []
    sep = "=" * 72

    lines.append(sep)
    lines.append("  机器狗任务规划测试 — 单轮结果")
    lines.append(sep)
    lines.append(f"  时间:         {meta['timestamp_display']}")
    lines.append(f"  提示词标签:   {meta['tag']}")
    lines.append(f"  提示词 MD5:   {meta['prompt_md5']}")
    lines.append(f"  模型端口:     {meta.get('port', 'N/A')}")
    lines.append(f"  温度:         {meta.get('temperature', 'N/A')}")
    lines.append(f"  总题数:       {meta['total']}")
    lines.append(f"  JSON 文件:    {meta['json_file']}")
    lines.append("")

    # ---- 一、分数汇总 ----
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
    lines.append("")

    # ---- 二、提示词内容 ----
    lines.append("-" * 72)
    lines.append("  【二、使用的提示词内容】")
    lines.append("-" * 72)
    for prompt_line in meta["prompt"].split("\n"):
        lines.append(f"  {prompt_line}")

    lines.append("")
    lines.append(sep)
    lines.append(f"  详细每题的得分与模型输出请查看同名的 JSON 文件")
    lines.append(f"  多轮汇总: python generate_summary.py")
    lines.append(sep)

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n摘要已保存: {summary_path}")


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="机器狗任务规划批量测试")
    parser.add_argument("--testset", default="data/task_plan_testset.json", help="测试集 JSON 路径")
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
        prompt = DOG_SCHEDULER_PROMPT
        print("使用内置默认提示词")

    prompt_md5 = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]

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
    print(f"加载测试集: {len(testset)} 题")
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

        evaluator = TaskPlanEvaluator(case)
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
        }
        results.append(record)

        print(f"  总分: {score_result['total_score']:.3f} | 耗时: {elapsed:.2f}s")
        bd = score_result["breakdown"]
        safety_s = bd["safety"]["score"]
        goal_s = bd["goal"]["score"]
        exec_s = bd["execution"]["score"]
        stage_s = bd["stage"]["score"]
        print(f"  安全:{safety_s:.2f} 目标:{goal_s:.2f} 执行:{exec_s:.2f} 阶段:{stage_s:.2f}")
        if score_result["total_score"] < 0.8:
            hri_detail = bd["safety"]["hri"].get("detail", "")
            goal_detail = bd["goal"]["coverage"].get("detail", "")
            stage_detail = bd["stage"]["match"].get("detail", "")
            print(f"  [!] HRI:{hri_detail} | 目标:{goal_detail[:60]} | 阶段:{stage_detail[:60]}")
            print(f"  输出片段:\n{reply[:200]}...")

        time.sleep(0.3)

    # ---- 打印控制台汇总 ----
    if results:
        avg_score = sum(r["score"] for r in results) / len(results)
        by_category = {}
        for r in results:
            by_category.setdefault(r["category"], []).append(r["score"])
        print(f"\n{'=' * 60}")
        print(f"测试完成: {len(results)} 题 | 总分平均: {avg_score:.4f}")
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
