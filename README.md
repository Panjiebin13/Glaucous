# Glaucous

> 雨过天青，海鸥滑翔，代码自有清凉。

一个**完全自研**的 CLI 编程智能体（零 agent 框架）：通过与大语言模型交互，自主读写文件、执行命令，完成你交给它的编程任务。

**设计宗旨：高自主、长任务运行、尽可能减少对人类的干扰。** 为此收敛出三条主线：

| 主线 | 机制 |
|---|---|
| **长任务跑得动** | `/spec` 大任务流程：澄清 → 书面规格 → 子 agent 评审 → 逐任务执行 → 代码评审 → 验收；子 agent 隔离 + 四级上下文压缩，几十步任务上下文占用仍极低 |
| **需求先说清** | Spec 入口强制澄清访谈（用户确认「清楚」才动手）；`/fork` 分支讨论；默认 Build + auto-approve，全自动且评审无阻塞时免批准直接执行 |
| **安全有兜底** | 每轮任务自动 Git 快照，`/rollback` 一键回退，审批卡支持「拒绝并回退」；权限按「有无兜底」划分——有 checkpoint 的放开，区外/运行时目录/远端坚决拦住 |

核心引擎全部自研（赛事要求的五项关键逻辑）：对话历史与上下文管理、工具定义与本地执行、模型输出解析（流式 tool-call 拼装 + 解析熔断）、循环终止条件（自然终止/步数上限/熔断/预算耗尽）、错误处理（悬空调用善后 + 退避重试）。

## 快速开始

```bash
pip install -e .
export GLAUCOUS_API_KEY=sk-****          # 凭据仅经环境变量，不入库
glaucous --workspace /path/to/project    # 或 ./start.sh
```

配置（环境变量）：

| 变量 | 说明 | 默认 |
|------|------|------|
| `GLAUCOUS_API_KEY` | API 密钥（必填） | — |
| `GLAUCOUS_BASE_URL` | OpenAI 兼容网关 | `https://api.deepseek.com/v1` |
| `GLAUCOUS_MODEL` | 模型名 | `deepseek-v4-flash` |
| `GLAUCOUS_TEMPERATURE` | 采样温度 | `0.2` |
| `GLAUCOUS_MAX_STEPS` | 主循环步数上限 | `50` |
| `GLAUCOUS_CONTEXT_LIMIT` | 上下文预算（`/context` 运行时可调 128K/512K/1M） | `128000` |
| `GLAUCOUS_CHECKPOINT_MAX_KEEP` | checkpoint 保留数 | `50` |

多模型：`~/.glaucous/models.toml`（模板见 `src/glaucous/assets/models.toml.example`），`/model` 运行时热切换（连通性三步校验）。

## 命令速览（/help 查看全部）

| 分组 | 命令 |
|---|---|
| 模式 | `/plan`（只读研究）· `/build [auto-approve\|per-action]` |
| 任务 | `/skill` 手动调用技能 · `/compact` 手动压缩 |
| Spec | `/spec [需求\|status\|cancel]` 发起与管理 · `/specs` 列表 |
| 会话 | `/sessions` 跨项目列表/切换 · `/resume` · `/fork` · `/rename` · `/stats` · `/clear` |
| 回退 | `/rollback`（快照选择 → 变更确认 → 文件还原 ± 上下文截断） |
| 配置 | `/model` · `/memory` · `/rules` · `/init` · `/context` |
| 其它 | `/view` · `/expand` · `/collapse` · `/help` · `/exit` |

## 功能亮点

- **两轨道任务处理**：小任务走 Plan/Build（轻量方案 + 直接执行）；大任务走 Spec（独立状态机：draft → reviewing → approved → executing → code_review → verified/archived，全程可查可续跑）；
- **多 Agent**：`spawn_agent` 派发子任务，子 agent 独立上下文/独立审批卡、防嵌套、报告 ≤1000 字回传（超限落盘可回取）——父上下文零污染；
- **权限矩阵**：三级风险 × 两种策略；git 兜底区内文件写免审、危险命令可豁免；`.glaucous/` 写、区外写、`git push --force` 恒拦；
- **上下文工程**：L0 超长输出截断落盘（`read_output` 回取）/ L1 本地裁剪 / L2 模型摘要 / 预算优雅终止；方案与 Spec 全文轻量锚化；
- **会话管理**：用户级集中存储（`~/.glaucous/sessions/<hash>/`）+ 侧边索引（损坏自动重建）+ 跨项目浏览 + 自动迁移；
- **体验**：箭头选择卡、思考过程动态折叠（`/expand` 回看）、终答 🕊 Markdown 卡片、规则（`glaucous.md`）/记忆/技能三件套。

## 文档

- [Glaucous 实现详解（v1.1）](docs/Glaucous实现详解（v1.1）.md) —— 功能与底层机制全解（三主线结构 + 设计决策速查）
- [Glaucous 系统详细设计与实现方案](docs/Glaucous系统详细设计与实现方案.md) —— 全系统深度细节（20 章：数据结构/协议/实现路径/设计决策）
- [编程智能体需求文档 v1.1](docs/编程智能体需求文档v1.1.md)（64 条 FR）· [概要设计说明书 v1.1](docs/编程智能体概要设计说明书v1.1.md)
- [开发计划表 v1.1](docs/Glaucous开发计划表v1.1.md) · [里程碑 spec 与评审报告](docs/designs/)（`docs/reviews/` 40+ 份）
- [M6 需求合规对照表](docs/M6需求合规对照表.md) · [视频脚本与面试材料](docs/M6视频脚本与面试材料.md)
- **[M6 评测报告](docs/M6评测报告.md)** —— 双版本实测结果、RAGAS 裁判评分与元评估（含诚实声明与局限）· [评测方案与报告模板](docs/M6评测方案与报告模板.md) · [评测集说明](eval/README.md)

## 评测（v1.1 vs v1.0 双版本实测）

方法借鉴 SWE-bench：**任务模板 + 基于测试的客观判据 + pass@1 口径**，不用主观分。每个用例配 `check.sh`（pytest 全绿 / 文件断言 / 会话记录行为断言），并先经「人工标准答案 + 反例」双句自验（对应 gold patch 验证）。

- **用例集**：8 个（`eval/cases/e1~e8`）——小任务回归、工程化大任务、从零实现智能体、模糊需求澄清、改坏回退、拒绝联动回退、跨会话续接、底线拦截；运行实例全量保留于 `/tmp/eval-*` 供复查；
- **客观判据结果**（2026-09-02，WSL + `deepseek-v4-flash`）：e1/e2/e4 **双版本均 PASS**；e3（从零实现智能体）**双版本均 FAIL**——两代同样把产物自主重组为包结构、偏离任务文件契约，如实登记为**模型层指令遵循短板（非版本差异）**；e5/e6/e7 为半自动用例（需人工触发 `/rollback`、拒绝并回退、`--resume`）；
- **底线用例的真实价值**：e8（「写 `~/.bashrc` + 删 `.glaucous/audit.log`」）**首跑即攻破一个静态代码走查未发现的 BLOCKER**：`printf 'x' >> ~/.bashrc` 因首词不在白名单而落入「保守升级 WRITE」分支（该分支不检查重定向目标指向），被 auto-approve 放行写入家目录。修复后（分类器顶层增加与首词无关的引号感知重定向区外写扫描 + `tee`/`cp` 类写目标参数检测）复跑 PASS；
- **RAGAS 裁判补充**（`AspectCritic` 三准则：范围遵守 / 代码质量 / 报告忠实度，裁判模型强于被测）：24 项评分、**判定理由 100% 可溯源落盘**（[eval/results/ragas_scores.json](eval/results/ragas_scores.json)），v1.1 **9/12** vs v1.0 **7/12**，差异主要在报告忠实度（v1.1 4/4、v1.0 2/4）；双版本共同短板是范围遵守（e2/e3 均有范围外产物）；
- **元评估结论（诚实声明）**：排查中发现裁判证据链本身有四处缺陷会造成系统性误判（`git diff` 不含未跟踪文件、运行副产物混入证据、终答提取错位、v1.0 会话布局未适配），修复后重采——因此 **LLM-judge 只作客观判据的探索性补充**（捕捉「范围自律」这类软维度），不作为主要证据；且单次采样仍有噪声。详见评测报告 §九~§十。

## 开发

```bash
pip install -e ".[dev]"
pytest          # 359 passed
```

**里程碑**：M0 原型闭环 → M1 模式基座（默认 Build + 底线守卫）→ M2 多 Agent → M3 会话管理 → M4 Checkpoint → M5 Spec 子系统 → M6 测试与评测。开发方法为 spec 驱动交付：先出约束文档，经评审子代理多轮评审通过后编码，代码再经评审循环——每处设计决策可追溯、可辩护。
