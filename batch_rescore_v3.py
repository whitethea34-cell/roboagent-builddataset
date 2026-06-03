#!/usr/bin/env python3
"""
批量用 v3 评估器重新评分所有已有结果，生成 SUMMARY_v3.md

用法: python batch_rescore_v3.py
输出:
  - results_v3/ 目录下每个版本的 v3 评分文件
  - SUMMARY_v3.md 汇总表格
"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from task_plan_evaluator_v3 import TaskPlanEvaluatorV3


def load_testset(path="data/task_plan_testset_v3.json"):
    with open(path, "r", encoding="utf-8") as f:
        return {c["test_id"]: c for c in json.load(f)}


def find_result_files(base_dir: Path):
    """找到所有结果文件（排除 _fixed 和 validate）"""
    files = []
    for f in sorted(base_dir.glob("results/results_*.json")):
        if "_fixed" in f.stem or "validate" in f.stem or "v3" in f.stem:
            continue
        # 提取 tag 和时间戳
        m = re.match(r"results_(.+?)_(\d{8}_\d{6})\.json", f.name)
        if m:
            tag = m.group(1)
            ts = m.group(2)
            files.append((tag, ts, f))
    return files


def rescore_file(result_path: Path, testset: dict, cases_dict: dict) -> dict:
    """对单个结果文件用 v3 重新评分"""
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("meta", {})
    details_in = data.get("details", [])

    v3_scores = []
    v3_details = []
    category_scores = defaultdict(list)
    v3_obj_scores = []
    v3_loc_scores = []

    for d in details_in:
        test_id = d["test_id"]
        gold = cases_dict.get(test_id)
        if not gold:
            cat = test_id.split("-")[0] if "-" in test_id else "?"
            v3_details.append({
                "test_id": test_id,
                "category": cat,
                "v3_score": None,
                "error": f"test_id {test_id} not in v3 testset",
            })
            continue

        evaluator = TaskPlanEvaluatorV3(gold)
        result = evaluator.evaluate(d["raw_output"])
        bd = result["breakdown"]

        # 从 test_id "DOG-A01" 提取类别 "A"
        cat = test_id.split("-")[1][0] if "-" in test_id and len(test_id.split("-")) > 1 else "?"
        obj_sub = bd.get("v3_object_coverage", 1.0)
        loc_sub = bd.get("v3_location_coverage", 1.0)

        v3_details.append({
            "test_id": test_id,
            "category": cat,
            "v3_score": result["total_score"],
            "breakdown": bd,
            "v3_object_coverage": obj_sub,
            "v3_location_coverage": loc_sub,
        })
        v3_scores.append(result["total_score"])
        category_scores[cat].append(result["total_score"])
        v3_obj_scores.append(obj_sub)
        v3_loc_scores.append(loc_sub)

    avg_score = sum(v3_scores) / len(v3_scores) if v3_scores else 0.0
    avg_obj = sum(v3_obj_scores) / len(v3_obj_scores) if v3_obj_scores else 0.0
    avg_loc = sum(v3_loc_scores) / len(v3_loc_scores) if v3_loc_scores else 0.0

    cat_avgs = {}
    for cat, scores in sorted(category_scores.items()):
        cat_avgs[cat] = round(sum(scores) / len(scores), 4)

    v3_meta = {
        "tag": meta.get("tag", ""),
        "prompt_md5": meta.get("prompt_md5", ""),
        "v2_avg_score": meta.get("avg_score", None),
        "v3_avg_score": round(avg_score, 4),
        "v3_avg_object_coverage": round(avg_obj, 4),
        "v3_avg_location_coverage": round(avg_loc, 4),
        "v3_category_averages": cat_avgs,
        "n_cases": len(v3_scores),
    }

    return {
        "meta": v3_meta,
        "details": v3_details,
    }


def generate_summary_v3(all_results: list, output_dir: Path):
    """生成 SUMMARY_v3.md"""
    lines = [
        "# SUMMARY_v3 — v3 评估器重评分汇总\n",
        "> 生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "> 评估器: task_plan_evaluator_v3.py (v2四权重 + 物体/位置验证)",
        "> 说明: 原分为原始 evaluator(v1/scorable) 评分，V3分为 v3 evaluator 评分",
        "",
        "| 版本 | 原分 | V3分 | 物体 | 位置 | A | B | C | D | E | 提示词MD5 |",
        "|------|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:--------:|",
    ]

    for r in sorted(all_results, key=lambda x: x["meta"].get("tag", "")):
        m = r["meta"]
        tag = m.get("tag", "?")
        orig = f"{m.get('v2_avg_score', '?'):.4f}" if m.get("v2_avg_score") is not None else "?"
        v3 = f"{m['v3_avg_score']:.4f}"
        obj = f"{m['v3_avg_object_coverage']:.3f}"
        loc = f"{m['v3_avg_location_coverage']:.3f}"
        md5 = m.get("prompt_md5", "?")[:8]
        cats = m.get("v3_category_averages", {})
        a = f"{cats.get('A', 0):.3f}"
        b = f"{cats.get('B', 0):.3f}"
        c = f"{cats.get('C', 0):.3f}"
        d = f"{cats.get('D', 0):.3f}"
        e = f"{cats.get('E', 0):.3f}"

        lines.append(f"| {tag:20s} | {orig} | {v3} | {obj} | {loc} | {a} | {b} | {c} | {d} | {e} | {md5} |")

    lines.extend([
        "",
        "## 解读",
        "",
        "- **原分**: 原始 v1/scorable 评估器评分（供参考）",
        "- **V3分**: v3 评估器总分 = safety(30%) + goal(40%) + execution(20%) + stage(10%)",
        "- **物体**: v3_object_coverage 平均值（模型输出覆盖所有 required objects 的比例）",
        "- **位置**: v3_location_coverage 平均值（模型输出覆盖所有 required locations 的比例）",
        "- goal(40%)内部 = 动作类覆盖率(30%) + 目标精确度(30%) + 物体验证(20%) + 位置验证(20%)",
        "- 四维度顶层权重与 v2 完全相同，仅 goal 内部子维度结构调整",
        "",
    ])

    return "\n".join(lines)


def main():
    base_dir = Path(__file__).parent
    testset = load_testset(str(base_dir / "data" / "task_plan_testset_v3.json"))

    files = find_result_files(base_dir)
    print(f"找到 {len(files)} 个结果文件")

    # Deduplicate: keep latest timestamp per tag
    tag_latest = {}
    for tag, ts, fpath in files:
        if tag not in tag_latest or ts > tag_latest[tag][0]:
            tag_latest[tag] = (ts, fpath)

    # Also keep multiple default runs for comparison
    # Group by tag
    tag_groups = defaultdict(list)
    for tag, ts, fpath in files:
        tag_groups[tag].append((ts, fpath))

    # For tags with multiple runs, keep ALL for comparison
    # But for SUMMARY, use the latest of each
    print(f"唯一标签数: {len(tag_latest)}")
    print(f"标签列表: {', '.join(sorted(tag_latest.keys()))}")

    # Create output directories
    results_dir = base_dir / "results"
    v3_dir = results_dir / "v3"
    v3_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    for tag, (ts, fpath) in sorted(tag_latest.items()):
        print(f"\n处理: {tag} ({fpath.name})")
        result = rescore_file(fpath, testset, testset)
        all_results.append(result)

        # Save per-version v3 result
        out_path = v3_dir / f"v3_{tag}_{ts}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        m = result["meta"]
        print(f"  V2={m.get('v2_avg_score', '?'):>8}  V3={m['v3_avg_score']:.4f}  obj={m['v3_avg_object_coverage']:.3f}  loc={m['v3_avg_location_coverage']:.3f}")

    # Generate SUMMARY_v3.md
    summary = generate_summary_v3(all_results, v3_dir)
    summary_dir = base_dir / "summary"
    summary_dir.mkdir(exist_ok=True)
    summary_path = summary_dir / "SUMMARY_v3.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    print(f"\nSUMMARY_v3.md 已生成: {summary_path}")
    print(f"v3 评分文件: {v3_dir}/")


if __name__ == "__main__":
    main()
