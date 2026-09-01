# Glaucous 评测集（v1.0 vs v1.1 对照实验）

构建方法借鉴 SWE-bench：任务模板 + 基于测试的客观判定 + pass@1 口径。
方案详见 [docs/M6评测方案与报告模板.md](../docs/M6评测方案与报告模板.md)。

## 目录结构

```
eval/
├── run_case.sh              # 运行器：复制模板 → git init → 下达任务 → 判定（v1.1）
├── run_v10.sh               # v1.0 对照运行器：方案卡选② + 审批兜底应答（见评测报告 §五）
├── judge_ragas.py           # RAGAS AspectCritic 裁判评分（三准则 × 双版本，见评测报告 §九）
└── cases/
    ├── e1-small-task/       # 小任务回归（两版本都应成功）
    ├── e2-engineering/      # 大任务全流程（核心对照）
    └── e5-rollback/         # 改坏回退（主线三对照，半自动）
```

每个用例目录：

```
<case>/
├── task.md                  # 任务文本（两个版本使用同一文本）
├── workspace/               # 初始工作区模板（每次评测复制全新副本）
├── check.sh                 # 客观判定脚本：输出 PASS / FAIL:<原因>
└── expected.md              # 预期行为与执行说明（半自动用例的人工步骤）
```

## 使用方法

```bash
# v1.1（当前安装）跑 e1
bash eval/run_case.sh e1-small-task

# v1.0 对照（独立 venv 安装，见评测方案 §3.1）
bash eval/run_case.sh e1-small-task /tmp/venv-v10/bin/glaucous

# 结果：脚本末尾打印 PASS/FAIL；运行实例保留在 /tmp/eval-<case>-<时间> 供复查
```

**记录**：每次运行把 结果/干预次数/上下文峰值占用/总 token/耗时 记入
`docs/M6评测报告.md` 的结果总表。

## 判据自验（构建时已完成）

每个 check.sh 在「人工标准答案」状态下验证过输出 PASS（对应 SWE-bench
gold patch 验证）：

```bash
# 自验示例（e1）：手工修正 calc.py + 补测试后
bash eval/cases/e1-small-task/check.sh <人工修正后的目录>   # 应输出 PASS
```

## 用例清单与判定方式

| 用例 | 判定 | 自动化程度 |
|---|---|---|
| e1-small-task | pytest 全绿 + `add(2,3)==5` 断言 | 全自动 |
| e2-engineering | pytest 全绿 + 四文件齐备 + pyproject 可解析 | 全自动 |
| e5-rollback | 回退后 `git diff` 初始提交为空 + 测试恢复全绿 | 半自动（人工触发 /rollback） |
| e3~e9 | 见评测方案 §四（E3 复用视频预演；E4 行为判定：会话中 ask_user 先于首次写操作） | 半自动 |
