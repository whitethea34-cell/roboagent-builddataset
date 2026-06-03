# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Machine dog (机器狗) task-planning evaluation dataset for the RoboAgent SCHEDULER module. Converts task decomposition and ordering into structured, auto-gradable test items.

Three evaluation systems, from fastest/cheapest to most semantic:
- **Rule-based (v1/v3/v4)** — heuristic scoring. v4 adds embedded failure mode detection + object chain checks. `task_plan_evaluator.py` / `task_plan_evaluator_v3.py` / `task_plan_evaluator_v4.py`
- **LLM-as-Judge** — Qwen3-8B on :8084 scores holistically across 5 dimensions. `judge_evaluator.py`
- **Q&A diagnostics** — ~400 deterministic binary-choice questions across 5 reasoning dimensions. `qaTest/`

Temperature 参数: 控制 LLM token 采样随机性 — 0.1≈确定性输出(适合测试一致性), 0.7-1.0≈创造性输出(适合探索变体)

## Model Port Map (4090 / llama-server)

| Port | Model | Role |
|------|-------|------|
| 8081 | Qwen3.5-2B | Main test model (中型) |
| 8082 | Qwen3.5-4B | Main test model (大型) |
| 8083 | Qwen3.5-0.8B | Main test model (小型/快速迭代) |
| 8084 | Qwen3-8B-Q8_0 | LLM-as-Judge 评分模型 |

## Directory Structure

```
builddataset/
├── batch_test_scheduler.py      # Main runner — calls llama-server API
├── task_plan_evaluator.py       # v1 rule-based scoring engine
├── task_plan_evaluator_v3.py    # v3 scoring engine (object/location validation)
├── judge_evaluator.py           # LLM-as-Judge semantic scorer (5-dim, total-first)
├── annotate_testset.py          # Test set annotation (objects/locations)
├── batch_rescore_v3.py          # Batch re-score with v3 evaluator
├── generate_summary.py          # Multi-run summary generator
│
├── config/                      # Prompt templates
│   ├── prompt_default_baseline.txt
│   └── prompt_v8.txt ~ v14.txt
│
├── data/                        # Datasets
│   ├── task_plan_testset.json   # 50 test cases (A-E × 10)
│   └── task_plan_testset_v3.json# v3 annotated (objects/locations)
│
├── docs/                        # Documentation & analysis
├── results/                     # batch_test_scheduler output
├── summary/                     # Cross-run comparison reports
├── outputs/                     # qaTest output
├── scripts/                     # One-off utility scripts
├── qaTest/                      # Diagnostic Q&A evaluation subsystem
├── failure_mode_detector.py     # Deterministic failure pattern detector (15 modes)
├── batch_redetect.py            # Batch re-detect + multi-version trend comparison
└── judge_evaluator.py           # LLM-as-Judge (Qwen3-8B, port 8084)
```

## Common Commands

### 开放生成评测 (batch_test_scheduler.py)

```bash
# 离线自测（验证评分引擎 + 生成 summary）
python batch_test_scheduler.py --offline --tag test

# 调用不同规模模型
python batch_test_scheduler.py --port 8083 --tag prompt-v1   # 0.8B
python batch_test_scheduler.py --port 8081 --tag prompt-v1   # 2B
python batch_test_scheduler.py --port 8082 --tag prompt-v1   # 4B

# 使用外部提示词文件
python batch_test_scheduler.py --port 8083 --prompt-file config/prompt_v14.txt --tag v14

# 只测几题快速验证
python batch_test_scheduler.py --port 8083 --ids DOG-A01,DOG-B01,DOG-E01

# 调低温度 + 自定义输出路径
python batch_test_scheduler.py --port 8083 --temperature 0.1 --output results/my_test.json
```

### LLM-as-Judge 语义评分 (judge_evaluator.py)

对 batch_test_scheduler 的结果进行 5 维语义重评分（需先启动 8084 judge 模型）:

```bash
# 基本评分
python judge_evaluator.py --input results/results_q8_0-08b-v9_xxx.json

# 评分 + 与规则评分对比
python judge_evaluator.py --input results/xxx.json --compare

# 自定义 judge 端口/温度
python judge_evaluator.py --input results/xxx.json --port 8084 --temperature 0.1
```

评分方法为 total-first: 先整体判断"任务能否完成"给出总分，再按 D1-D5 五维度归因诊断。

### 失败模式检测 (failure_mode_detector.py)

```bash
# 自测
python failure_mode_detector.py

# 对单个结果文件检测失败模式
python batch_redetect.py --input results/results_q8_0-08b-v9_xxx.json

# 多个版本趋势对比
python batch_redetect.py --inputs results/v14.json results/v15.json results/v16.json --compare

# 扫描全部历史结果并对比
python batch_redetect.py --all --compare
```

输出每种失败模式的出现次数（非聚合总分），直接指向 prompt 修改方向。
严重度: P0(严重/任务失败) P1(重要/常识链断裂) P2(轻微)。

### Q&A 诊断评测 (qaTest/)

```bash
# 冒烟测试（5题快速验证）
python qaTest/qa_batch_runner.py --port 8083 --limit 5 --tag smoke-test

# 完整测试
python qaTest/qa_batch_runner.py --port 8083 --tag prompt-v1

# 分层抽样（按 D1-D5 占比随机采样）
python qaTest/qa_batch_runner.py --port 8083 --sample 50 --tag v1-sample

# 筛选特定维度/来源
python qaTest/qa_batch_runner.py --port 8083 --dimension D2 --tag d2-test
python qaTest/qa_batch_runner.py --port 8083 --source DOG-B01 --tag b01-test

# 离线自测
python qaTest/qa_batch_runner.py --offline --tag self-test

# 评分引擎单元测试
python qaTest/qa_evaluator.py
```

### 评分引擎单元测试

```bash
python task_plan_evaluator.py
python task_plan_evaluator_v3.py
python task_plan_evaluator_v4.py        # v4: 自测 + 与v3对比
```

### v4 评分器 (task_plan_evaluator_v4.py)

v4 = v3 四维框架 + 嵌入式失败模式检测:

```
v4总分 = v3四维加权和 - P0×0.15 - P1×0.08 - P2×0.03
```

v4 新增检测:
- **物品操作链完整性**: FIND→CARRY→DROP 缺一即扣分
- **荒谬搬运目标**: CARRY|充电桩/桌子/目标位置 → P0
- **DROP缺失**: CARRY后无DROP → P1
- **冗余循环**: 动作块重复≥3次 → P1
- **EMERGENCY缺失**: 危险任务无紧急制动 → P1
- **严格物体匹配**: 防止"包裹"误匹配"包裹A和B"

v3 vs v4 对比 (Q8_0 v9, 50题):
- v3 avg: 0.822, v4 avg: 0.672
- v4 检测到 5 个 P0 严重失败 (v3 对这些 case 均分 0.84+)

### 批量重评分 & 累积汇总

```bash
# v3 批量重评分（重算所有结果 → SUMMARY_v3.md）
python batch_rescore_v3.py

# 累积汇总：扫描 results/ 目录 → 生成 summary/SUMMARY.md
python generate_summary.py
python generate_summary.py --dir results --output summary/SUMMARY.md
```

### 4090 前置步骤（在 WSL2 中执行）

```bash
cd /mnt/e/Claude_roboagent/r005_rebuildtest
docker compose -f 4090_test/docker-compose.4090.yaml up -d qwen35-08b
curl -sf http://localhost:8083/health && echo "OK"
```

## Three Evaluation Systems

```
开放生成评测 (batch_test_scheduler)
  ├── 模型输出完整任务规划
  ├── 规则评分 (task_plan_evaluator v1/v3): 快速、确定性、可复现
  └── LLM-Judge (judge_evaluator): 语义级、可解释诊断、需要 8084 模型

Q&A 诊断评测 (qaTest/)
  ├── ~400 道选择题/判断题（5 维度 × 50 题派生）
  ├── 评分: pred == gold → 1/0（零歧义）
  └── 用途: 精确定位模型在哪个推理维度薄弱
```

两者的关系: 开放生成是"集成测试"（整体方案质量），Q&A 是"单元测试"（诊断具体推理能力薄弱点）。

## Key Scoring Rules

- Action ordering uses **topological-order consistency** (dependency satisfaction), not exact sequence match
- `NAVIGATE` actions have no inter-dependency — order among them is ignored
- **Stage scoring** uses semantic fuzzy matching: exact=1.0 → suffix-strip match(0.90) → substring containment(0.7+ratio) → STAGE_SYNONYMS dict(0.80) → char unigram Jaccard fallback(×0.55). Missing stages penalty 0.03 each (cap 0.12), extra stages penalty 0.02 each (cap 0.10)
- **Synonym mapping** (dog-specific): WALK↔TROT, SPEAK↔BEEP(also BEEP↔SPEAK), SHAKE↔NOD, PICK→CARRY, GIVE→DROP, SEARCH→FIND. Gold actions are normalized at init time to match predicted actions. Sub-task stage names also mapped: 取物↔取件/取水/取药..., 前往↔前进/导航..., 接近↔抵达/到达..., 判断↔遇障判断/遇人判断...
- **默认提示词** 含子任务标签库（7类~30个核心阶段名）约束模型输出。多点场景允许字母/数字后缀（巡逻R1、前往A）
- **Branching tasks (type E)**: gold contains parallel action paths separated by condition; evaluator matches whichever path the model output follows — partial credit for correct condition judgment
- Model output format: `HRI：是/否` / `阶段：A→B→C` / `序号. ACTION|target`
- State lights (`LED_*`) are independent atomic skills used at each subtask boundary

### LLM-as-Judge 5 维度 (judge_evaluator.py)

| 维度 | 权重 | 核心问题 |
|------|------|----------|
| D1 阶段 | 15% | 阶段覆盖与任务分解是否合理 |
| D2 顺序 | 25% | 动作顺序正确性（拓扑+常识链） |
| D3 物体 | 25% | 物体操作链完整性 (FIND→CARRY→DROP) |
| D4 条件 | 20% | 条件/分支处理 + 场景理解 |
| D5 安全 | 15% | 安全红线 + 社交完整性（一票否决制） |

### Q&A 诊断 5 维度 (qaTest/)

| 维度 | 核心问题 | 题型 |
|------|----------|------|
| D1 任务拆解 | "这个任务应该分几个阶段？" | 单选/多选/排序 |
| D2 时序理解 | "这一步的前置条件是什么？" | 单选 |
| D3 物体/动作依赖 | "搬东西前必须先做什么？" | 单选 |
| D4 场景/条件判断 | "遇到障碍怎么办？在哪找人？" | 单选/分支选择 |
| D5 安全与社交 | "要不要跟人打招呼？" | 判断 |

## 输出文件

每次运行自动生成两个文件（以 `--tag test` 为例）：
```
results/results_test_20260513_143000.json        # 完整 JSON（meta + 每题评分/输出）
results/results_test_20260513_143000_summary.txt # 两段式摘要（分数汇总 + 提示词全文）
```

qaTest 输出:
```
outputs/qa_results_{tag}_{timestamp}.json
outputs/qa_results_{tag}_{timestamp}_summary.txt
```

## 多版本提示词对比工作流

```bash
# 版本1：内置默认提示词
python batch_test_scheduler.py --port 8083 --tag v1-default

# 版本2：修改后提示词（写在 config/ 中）
python batch_test_scheduler.py --port 8083 --prompt-file config/prompt_v2.txt --tag v2-improved

# 版本3：进一步修改
python batch_test_scheduler.py --port 8083 --prompt-file config/prompt_v3.txt --tag v3-final

# 生成累积汇总表格（一键对比所有版本）
python generate_summary.py
```
