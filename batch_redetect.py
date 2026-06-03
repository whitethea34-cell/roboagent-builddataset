#!/usr/bin/env python3
"""
批量失败模式重检测 — 对旧结果 JSON 用新检测器重新分析

用法:
  # 单个文件
  python batch_redetect.py --input results/results_q8_0-08b-v9_xxx.json

  # 多个文件对比（趋势追踪）
  python batch_redetect.py --inputs results/run1.json results/run2.json results/run3.json --compare

  # 指定输出
  python batch_redetect.py --input results/xxx.json --output results/xxx_failures.json

输出:
  - 终端: 失败模式汇总表格 + 趋势对比（--compare 模式）
  - JSON: 每题详细失败列表 + 聚合统计
"""

import json
import argparse
import sys
from pathlib import Path
from collections import defaultdict, Counter

from failure_mode_detector import FailureModeDetector


def load_testset(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cases = json.load(f)
    return {c["test_id"]: c for c in cases}


def detect_file(results_path: str, testset: dict, detector: FailureModeDetector) -> dict:
    """对一个结果文件运行全部检测"""
    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    details = data.get("details", [])
    meta = data.get("meta", {})
    tag = meta.get("tag", Path(results_path).stem)

    case_results = []
    for d in details:
        test_id = d.get("test_id", "")
        raw_output = d.get("raw_output", d.get("model_output", ""))
        v3_score = d.get("score", d.get("overall_score", None))

        if test_id not in testset:
            continue

        case = testset[test_id]
        r = detector.detect(
            test_id=test_id,
            raw_output=raw_output,
            instruction=case.get("instruction", ""),
            hri_expected=case.get("hri_expected", None),
            objects_required=case.get("objects_required", []),
        )
        r["v3_score"] = v3_score
        r["category"] = d.get("category", test_id.split("-")[1][0] if "-" in test_id else "?")
        case_results.append(r)

    agg = detector.aggregate(case_results)

    return {
        "tag": tag,
        "file": str(results_path),
        "meta": meta,
        "aggregate": agg,
        "details": case_results,
    }


def print_summary(result: dict):
    """打印单个文件的失败模式汇总"""
    agg = result["aggregate"]
    fc = agg["failure_counts"]
    tag = result["tag"]

    print(f"\n{'='*72}")
    print(f"  失败模式检测: {tag}")
    print(f"{'='*72}")
    print(f"  总 case 数: {agg['total_cases']}")
    print(f"  有失败的 case: {agg['cases_with_failures']} ({agg['cases_with_failures']/agg['total_cases']*100:.0f}%)")
    print(f"  完全通过: {agg['cases_clean']} ({agg['cases_clean']/agg['total_cases']*100:.0f}%)")
    print(f"  HRI 正确: {agg['hri_stats']['correct']}  错误: {agg['hri_stats']['wrong']}  未知: {agg['hri_stats']['unknown']}")

    if not fc:
        print(f"\n  ✓ 未检测到任何失败模式")
        return

    print(f"\n  {'失败模式':<35} {'次数':>5} {'占比':>6} {'严重度'}")
    print(f"  {'-'*55}")
    for mode, count in fc.items():
        pct = f"{count/agg['total_cases']*100:.0f}%"
        from failure_mode_detector import P0_FAILURES, P1_FAILURES, P2_FAILURES
        if mode in P0_FAILURES:
            sev = "P0 严重"
        elif mode in P1_FAILURES:
            sev = "P1 重要"
        elif mode in P2_FAILURES:
            sev = "P2 轻微"
        else:
            sev = "?"
        bar = "█" * min(count, 20)
        print(f"  {mode:<35} {count:>5} {pct:>6} {sev}  {bar}")

    # 按类别
    print(f"\n  --- 按类别 ---")
    cats = sorted(agg["per_category"].keys())
    headers = list(fc.keys())[:7]  # 最多显示7个模式
    header_str = "".join(f"{h.split('_')[0]:>6}" for h in headers)
    print(f"  {'类别':<6} {header_str}")
    print(f"  {'-'*50}")
    for cat in cats:
        cat_counts = agg["per_category"][cat]
        vals = "".join(f"{cat_counts.get(h, 0):>6}" for h in headers)
        print(f"  {cat:<6} {vals}")

    # P0 详情
    p0_modes = [k for k in fc if k in __import__('failure_mode_detector').P0_FAILURES]
    if p0_modes:
        print(f"\n  --- P0 严重失败详情 ---")
        for d in result["details"]:
            p0_fails = [f for f in d["failures"] if f["severity"] == "P0"]
            if p0_fails:
                for f in p0_fails:
                    print(f"  [{d['test_id']}] {f['detail']}")


def print_comparison(results_list: list[dict]):
    """打印多版本对比"""
    if len(results_list) < 2:
        return

    print(f"\n{'='*72}")
    print(f"  趋势对比: 失败模式变化")
    print(f"{'='*72}")

    # 收集所有出现过的失败模式
    all_modes = set()
    for r in results_list:
        all_modes.update(r["aggregate"]["failure_counts"].keys())

    if not all_modes:
        print("\n  所有版本均未检测到失败模式")
        return

    # 按严重度排序
    from failure_mode_detector import P0_FAILURES, P1_FAILURES, P2_FAILURES
    def severity_order(mode):
        if mode in P0_FAILURES: return 0
        if mode in P1_FAILURES: return 1
        return 2
    ordered_modes = sorted(all_modes, key=severity_order)

    # 表头
    tags = [r["tag"] for r in results_list]
    tag_str = "".join(f"{t:>10}" for t in tags)
    print(f"\n  {'失败模式':<35} {tag_str} {'趋势'}")
    print(f"  {'-'*80}")

    for mode in ordered_modes:
        counts = []
        for r in results_list:
            c = r["aggregate"]["failure_counts"].get(mode, 0)
            counts.append(c)

        count_str = "".join(f"{c:>10}" for c in counts)
        # 趋势箭头
        if len(counts) >= 2 and counts[0] > 0:
            if counts[-1] < counts[0]:
                trend = "↓ 改善"
            elif counts[-1] > counts[0]:
                trend = "↑ 恶化"
            else:
                trend = "→ 不变"
        else:
            trend = ""

        print(f"  {mode:<35} {count_str}  {trend}")

    # 通过率对比
    print(f"\n  --- 通过率对比 ---")
    for r in results_list:
        agg = r["aggregate"]
        clean_rate = agg["cases_clean"] / agg["total_cases"] * 100
        bar = "█" * int(clean_rate / 5)
        print(f"  {r['tag']:<25} {agg['cases_clean']:>3}/{agg['total_cases']} clean ({clean_rate:.0f}%) {bar}")


def main():
    parser = argparse.ArgumentParser(
        description="批量失败模式重检测 — 确定性规则引擎，替代聚合评分"
    )
    parser.add_argument("--input", default=None,
                        help="单个结果 JSON 路径")
    parser.add_argument("--inputs", nargs="+", default=None,
                        help="多个结果 JSON 路径（用于对比）")
    parser.add_argument("--testset", default="data/task_plan_testset_v3.json",
                        help="DOG 测试集路径")
    parser.add_argument("--output", default=None,
                        help="输出 JSON 路径（单个文件模式）")
    parser.add_argument("--compare", action="store_true",
                        help="强制输出对比模式（与 --inputs 一起使用）")
    parser.add_argument("--all", action="store_true",
                        help="扫描 results/ 目录下所有结果文件并对比")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent

    # 加载测试集
    testset_path = script_dir / args.testset
    print(f"加载测试集: {testset_path}")
    testset = load_testset(str(testset_path))
    print(f"  共 {len(testset)} 个 case")

    detector = FailureModeDetector(testset)

    # 收集输入文件
    input_files = []
    if args.input:
        input_files.append(args.input)
    if args.inputs:
        input_files.extend(args.inputs)

    if args.all:
        results_dir = script_dir / "results"
        for f in sorted(results_dir.glob("results_*.json")):
            if "judge" not in f.name and "fixed" not in f.name:
                input_files.append(str(f))
        print(f"\n扫描 results/ 目录，找到 {len(input_files)} 个结果文件")

    if not input_files:
        print("错误: 请指定 --input、--inputs 或 --all")
        print("示例: python batch_redetect.py --input results/results_q8_0-08b-v9_xxx.json")
        print("      python batch_redetect.py --inputs results/v1.json results/v2.json --compare")
        sys.exit(1)

    # 运行检测
    results_list = []
    for fpath in input_files:
        resolved = Path(fpath)
        if not resolved.is_absolute():
            resolved = script_dir / resolved
        if not resolved.exists():
            print(f"  [跳过] 文件不存在: {resolved}")
            continue
        print(f"\n检测: {resolved.name}")
        r = detect_file(str(resolved), testset, detector)
        results_list.append(r)
        print_summary(r)

    # 保存（单文件模式）
    if len(results_list) == 1 and not args.compare:
        r = results_list[0]
        output_path = args.output
        if not output_path:
            p = Path(input_files[0])
            output_path = p.parent / f"{p.stem}_failures.json"
        else:
            output_path = Path(args.output)
            if not output_path.is_absolute():
                output_path = script_dir / output_path

        output_data = {
            "detector_version": "v1",
            "total_modes_detected": len(r["aggregate"]["failure_counts"]),
            **r,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n详细结果已保存: {output_path}")

    # 对比模式
    if args.compare or len(results_list) >= 2:
        print_comparison(results_list)

    # P0 汇总
    has_p0 = False
    for r in results_list:
        from failure_mode_detector import P0_FAILURES
        for mode, count in r["aggregate"]["failure_counts"].items():
            if mode in P0_FAILURES and count > 0:
                has_p0 = True
                break
    if has_p0:
        print(f"\n{'!'*72}")
        print(f"  !! P0 严重失败: 物品完全遗漏 / 幻觉搬运 / 搬运人类")
        print(f"  这些是 prompt 需要重点修复的方向")
        print(f"{'!'*72}")


if __name__ == "__main__":
    main()
