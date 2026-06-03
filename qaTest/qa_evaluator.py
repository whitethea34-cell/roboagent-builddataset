#!/usr/bin/env python3
"""
Q&A 诊断评测引擎 — 确定性评分 + 分维度聚合

与开放生成评测不同，Q&A 评测的评分极其简洁：
  - 选择题：提取模型输出的选项字母(A/B/C/D)，与 gold answer 比对
  - 判断题：提取是/否/true/false，与 gold answer 比对
  - 评分 = pred == gold ? 1 : 0（零歧义）

用法:
  python qa_evaluator.py                              # 自测（用gold答案模拟）
  python qa_evaluator.py --input qa_testset.json       # 验证测试集完整性
"""

import json
import re
import argparse
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


class QAEvaluator:
    """Q&A 诊断评测器 — 按维度独立打分"""

    DIMENSION_NAMES = {
        "D1": "任务拆解",
        "D2": "时序理解",
        "D3": "物体/动作依赖",
        "D4": "场景/条件判断",
        "D5": "安全与社交",
    }

    def __init__(self, testset_path: str = "qa_testset.json"):
        with open(testset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.meta = data["meta"]
        self.questions = data["questions"]
        self._by_id = {q["qa_id"]: q for q in self.questions}

    # ========================
    # 答案提取
    # ========================

    @staticmethod
    def extract_answer(raw_output: str, q_type: str) -> Optional[str]:
        """
        从 LLM 原始输出中提取答案。

        选择题策略（按优先级）：
        1. 匹配 "答案：A" / "Answer: A" 格式
        2. 匹配 "选A" / "选项A" / "A." 格式
        3. 匹配最后一个单独的大写字母 A/B/C/D（防止误匹配文本中的字母）

        判断题策略：
        1. 匹配 "答案：是/否" / "Answer: True/False"
        2. 匹配 "是/否/true/false"
        """
        text = raw_output.strip()

        if q_type in ("single_choice", "multi_choice"):
            # 策略1：显式答案声明
            m = re.search(r"(?:答案|Answer)[：:\s]*([A-E])", text, re.I)
            if m:
                return m.group(1).upper()

            # 策略2：选X / 选项X
            m = re.search(r"(?:选|选项|[（(])\s*([A-E])\s*[）)]?", text)
            if m:
                return m.group(1).upper()

            # 策略3：最后一个独立的选项字母
            # 匹配 "A." 或 "A)" 或 "A ）" 或行首的 "A "
            option_matches = re.findall(r'(?:^|\n|\s)([A-E])[\.\)、)\s]', text)
            if option_matches:
                return option_matches[-1].upper()

            # 策略4：最后一个单独的大写字母
            single_letters = re.findall(r'\b([A-E])\b', text)
            if single_letters:
                return single_letters[-1]

            return None

        elif q_type == "true_false":
            # 匹配中文
            m = re.search(r"(?:答案|Answer)[：:\s]*(是|否)", text, re.I)
            if m:
                return "true" if m.group(1) == "是" else "false"

            if re.search(r"(?:是|对|正确|yes|true)", text, re.I):
                return "true"
            if re.search(r"(?:否|错|错误|不对|no|false)", text, re.I):
                return "false"

            return None

        elif q_type == "ordering":
            # 排序题：提取 → 分隔的序列
            m = re.search(r'([A-E](?:[→>]\s*[A-E])+)', text)
            if m:
                return m.group(1).replace(">", "→").replace(" ", "")
            return None

        return None

    # ========================
    # 评分
    # ========================

    @staticmethod
    def score_single(pred_answer: Optional[str], gold_answer, q_type: str) -> Tuple[float, str]:
        """
        单题评分。

        Returns:
            (score, detail): score ∈ {0.0, 1.0}, detail 为评分说明
        """
        if pred_answer is None:
            return 0.0, "未提取到有效答案"

        gold_str = str(gold_answer).strip().upper() if isinstance(gold_answer, str) else str(gold_answer)

        if q_type in ("single_choice", "multi_choice"):
            pred_norm = pred_answer.strip().upper().replace(" ", "")
            gold_norm = gold_str.replace(" ", "")
            if pred_norm == gold_norm:
                return 1.0, f"正确 ({pred_answer})"
            else:
                return 0.0, f"错误 (pred={pred_answer}, gold={gold_norm})"

        elif q_type == "true_false":
            pred_lower = pred_answer.strip().lower()
            gold_bool = gold_answer if isinstance(gold_answer, bool) else gold_str.lower() == "true"

            if gold_bool:
                if pred_lower in ("true", "是", "对", "正确", "yes"):
                    return 1.0, f"正确 ({pred_answer})"
            else:
                if pred_lower in ("false", "否", "错", "错误", "不对", "no"):
                    return 1.0, f"正确 ({pred_answer})"
            return 0.0, f"错误 (pred={pred_answer}, gold={gold_bool})"

        elif q_type == "ordering":
            if pred_answer.strip().upper().replace("→", "") == gold_str.upper().replace("→", ""):
                return 1.0, f"正确 ({pred_answer})"
            return 0.0, f"错误 (pred={pred_answer}, gold={gold_str})"

        return 0.0, f"未知题型: {q_type}"

    # ========================
    # 批量评估
    # ========================

    def evaluate_batch(self, model_outputs: Dict[str, str]) -> Dict:
        """
        批量评估一组模型输出。

        Args:
            model_outputs: {qa_id: raw_output_text} 字典

        Returns:
            {
                "overall_accuracy": float,
                "dimension_scores": {"D1": 0.85, "D2": 0.72, ...},
                "details": [{qa_id, dimension, score, detail, ...}, ...]
            }
        """
        details = []
        dim_correct = defaultdict(int)
        dim_total = defaultdict(int)

        for qa_id, raw_output in model_outputs.items():
            q = self._by_id.get(qa_id)
            if not q:
                continue

            q_type = q["type"]
            pred = self.extract_answer(raw_output, q_type)
            score, detail = self.score_single(pred, q["answer"], q_type)

            dim = q["dimension"]
            dim_total[dim] += 1
            if score >= 1.0:
                dim_correct[dim] += 1

            details.append({
                "qa_id": qa_id,
                "dimension": dim,
                "sub_dimension": q.get("sub_dimension", ""),
                "type": q_type,
                "difficulty": q["difficulty"],
                "score": score,
                "pred_answer": pred,
                "gold_answer": q["answer"],
                "detail": detail,
                "source_test_id": q.get("source_test_id", ""),
            })

        # 按维度聚合
        dimension_scores = {}
        total_correct = sum(dim_correct.values())
        total_all = sum(dim_total.values())

        for dim in ["D1", "D2", "D3", "D4", "D5"]:
            t = dim_total.get(dim, 0)
            c = dim_correct.get(dim, 0)
            dimension_scores[dim] = {
                "name": self.DIMENSION_NAMES.get(dim, dim),
                "accuracy": round(c / t, 4) if t > 0 else 0.0,
                "correct": c,
                "total": t,
            }

        return {
            "overall_accuracy": round(total_correct / total_all, 4) if total_all > 0 else 0.0,
            "total_correct": total_correct,
            "total_questions": total_all,
            "dimension_scores": dimension_scores,
            "details": details,
        }

    # ========================
    # 自测
    # ========================

    def self_test(self, limit: int = 10) -> Dict:
        """用 gold 答案模拟满分输出，验证评分引擎正确性"""
        mock_outputs = {}
        for q in self.questions[:limit]:
            q_type = q["type"]
            answer = q["answer"]
            if q_type in ("single_choice", "multi_choice"):
                mock_outputs[q["qa_id"]] = f"答案：{answer}"
            elif q_type == "true_false":
                mock_outputs[q["qa_id"]] = "是" if answer else "否"
            elif q_type == "ordering":
                mock_outputs[q["qa_id"]] = answer

        return self.evaluate_batch(mock_outputs)


def print_report(result: Dict):
    """打印评估报告"""
    print("=" * 60)
    print("  Q&A 评测结果")
    print("=" * 60)
    print(f"  总准确率: {result['overall_accuracy']:.4f}  "
          f"({result['total_correct']}/{result['total_questions']})")
    print()
    print("  【分维度准确率】")
    print(f"  {'维度':<8} {'名称':<14} {'准确率':<10} {'正确/总数'}")
    print(f"  {'-'*48}")

    for dim in ["D1", "D2", "D3", "D4", "D5"]:
        ds = result["dimension_scores"].get(dim, {})
        if ds:
            name = ds.get("name", "")
            acc = ds.get("accuracy", 0)
            c = ds.get("correct", 0)
            t = ds.get("total", 0)
            bar = "█" * int(acc * 20)
            print(f"  {dim:<8} {name:<14} {acc:.4f}     {c:>3}/{t:<3}  {bar}")

    print()
    print(f"  * 随机猜测基线 (4选1): ~25%")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Q&A 评测引擎")
    parser.add_argument("--input", default="qa_testset.json", help="Q&A 测试集路径")
    parser.add_argument("--limit", type=int, default=10, help="自测题数")
    args = parser.parse_args()

    evaluator = QAEvaluator(args.input)
    print(f"加载测试集: {len(evaluator.questions)} 题")
    print(f"维度分布: {evaluator.meta['dimensions']}\n")

    result = evaluator.self_test(limit=args.limit)
    print_report(result)

    # 验证满分
    if result["overall_accuracy"] == 1.0:
        print("自测通过：gold答案模拟得满分。")
    else:
        print(f"自测异常：满分期望=1.0，实际={result['overall_accuracy']:.4f}")
        # 打印错误详情
        for d in result["details"]:
            if d["score"] < 1.0:
                print(f"  {d['qa_id']}: {d['detail']}")
