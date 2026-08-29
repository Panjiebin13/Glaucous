# Spec 评审报告：202608301000-plan-m2-day4-memory-context.md

- 评审时间：2026-08-28 15:00 / 评审 agent：spec-reviewer
- 基准版本：需求文档 v3.2 / 概设 v4.1
- 对照基准：Glaucous开发计划表 v1.0（M2 任务 2.1~2.8）、前案基线 202608291000-plan-m1-day3-permission.md、现有代码 src/glaucous/（Day3 后）

## 评审结论

**有条件通过** —— 计划表 2.1~2.8 全覆盖、阈值参数（L0 300行/50KB、头200+尾50、L1 >70%、L2 >85%、token 中文/1.5）与概设逐项吻合，对现有代码基线（base.py metadata 五字段、planning.py M2 登记项、prompts 签名）的引用经代码核实准确。存在 2 项阻塞：方案轻量锚未实现概设 §5.2 的"不全量驻留"核心语义（B-01）、§0 禁测约束与 Step 6 回归要求自相矛盾（B-02）。修复后即可进入实施。

## 阻塞事项（必须修复）

- **[B-01] 方案全文经 tool_calls arguments 常驻历史，轻量锚策略"不把完整方案写入上下文"的核心目标未实现** — spec §4.7（行 196-199）— 违反概设 §5.2：「Build 执行期间**不把完整方案写入上下文**，而是注入一条轻量锚……方案全文通常数百行，常驻注入会挤占预算」。
  现状链路：submit_plan 的 `plan` 参数全文保留在该 assistant 消息的 `tool_calls[].function.arguments` 中；spec §4.6 L1 明确"assistant 文本一律保留"（此处概设 §4.2 亦如此规定，非 spec 之过），L2 未触发的会话中方案全文将**全程常驻**。spec 只实现了锚的"回读通道"（落盘 + 锚行回喂 + read_plan），轻量锚的核心收益（省预算）落空，与概设 §5.2 直接冲突，且该偏离未在 §0 裁剪声明或 §5 决策表中出现。
  **修复建议**（三选一，推荐 A）：
  - A：submit_plan 的 confirm 决策返回后，在入史路径上把该 assistant 消息 arguments 中的 plan 字段改写为锚文本（如 `[方案全文已落盘 .glaucous/plans/<id>.md，可调用 read_plan 回读]`）——落盘与 confirm 卡片展示用原文，历史只存锚；JSONL 落盘的是改写后 entry，天然一致；选③继续讨论时模型可经 read_plan 回读旧版再修订（与概设"细节需要时再回读"一致）。需在 §5 补一条决策记录改写时点与③场景的回读引导。
  - B：由 compactor 在 L1 阶段对 submit_plan 轮的 arguments 定向替换（实现更简单，但 Build 前半程全文仍驻留，仅部分达成概设目标，需说明）。
  - C：在 §5 决策表显式声明"方案全文驻留至 L2"的偏离及可辩护理由——但需能通过概设 §5.2 明文与面试辩护检验，不建议。

- **[B-02] §0"不运行任何测试"与 Step 6"既有 61 用例回归"、§8 风险缓解"既有测试回归覆盖"直接矛盾** — spec §0（行 11）vs §6 Step 6（行 249）vs §8（行 256）— 内部矛盾。
  §0 声明"**不产出、不运行任何测试**"，但 Step 6 要求"既有 61 用例回归"，§8 两处缓解措施（`_meta` 泄漏、view() 序列合法性）均依赖该回归。这不是措辞瑕疵：本轮将改写 history.view()（所有请求的必经路径），若执行者严格按 §0 跳过回归，Day3 基线可能被静默破坏且本轮零验证。
  **修复建议**：§0 措辞收敛为"不产出新增测试、不执行新增验证；既有用例回归属基线保护，允许并要求执行"，或反向收敛——Step 6/§8 删除回归要求并声明由 M4 承担（不推荐，风险高）。以前者为佳。

## 建议（建议修复 / 可选优化）

- **[S-01]（高优先）L2 持续失败时不存在终止路径，可能形成压缩调用循环** — spec §4.7 失败降级（行 200）vs §4.8 预算耗尽终止（行 202-205）。§4.8 的终止以"L2 后仍 ≥100%"为前提；L2 失败走 §4.7"降级 L1 加深、下轮重新评估"后，若早期历史中有 L1 裁不掉的大块内容（如超大 user 消息），守卫点将每轮重走 L1（幂等跳过）→ L2 失败 → 再评估，每次 L2 携带最多 4 次退避重试，直到主请求超限报 4xx/超长错误才异常终止。建议：定义"L2 连续失败 N 次（建议 2 次）后按终止条件③终止"，或明确"L2 失败且裁剪后 percent 仍 ≥100% 时直接走 §4.8 终止"。
- **[S-02] config 配置项在步骤间重复登记且依赖顺序错位** — Step 3（行 246）已含"config.py 阈值配置"，Step 6（行 249）又"新增 GLAUCOUS_CONTEXT_LIMIT / GLAUCOUS_MEMORY_TOP_N"。GLAUCOUS_MEMORY_TOP_N 实际被 Step 1 的 load_injection 消费、GLAUCOUS_CONTEXT_LIMIT 被 Step 3 的 budget 消费。建议：MEMORY_TOP_N 归 Step 1、CONTEXT_LIMIT 归 Step 3，Step 6 仅保留汇总校验，避免实施时重复添加或遗漏。
- **[S-03] AgentLoop 依赖注入扩展未列入装配清单** — §4.9（行 207-212）只写了 registry 注册新工具与 build_system_prompt 签名，但按 §1/§4.4~4.8，loop 需新增注入：BudgetReport 构造器（或 budget 实例 + limit）、compactor、outputs 目录、方案锚数据源、`context_limit` 配置透传。现有构造函数（agent/loop.py L45-59）无这些参数。建议 §4.9 补一行 loop 装配说明，避免装配遗漏。
- **[S-04] view() 剥除内部键需明确"拷贝"语义** — §3（行 141-143）。现有 view()（context/history.py L102-104）直接返回 `self._messages` 中 dict 的**原引用**；"输出前剥除 `_` 前缀键"若以原地删除实现将破坏 D2 单一数据源，若不拷贝则内部键泄漏进 API 请求。建议明确：view() 对含内部键的 entry 返回浅拷贝并过滤（无内部键的 entry 可仍走引用以省开销）；同时说明 tool_end 事件携带的 content 为截断后内容（loop 在 push_tool 前替换 result.content，UI 展示与入史一致）。
- **[S-05] plan_anchor 的构造归属未定义** — §3 `compact_history(messages, llm, plan_anchor)`（行 104）与 §4.7"自 plans 文件头部解析，解析失败则仅附路径"（行 199）。锚段由谁构造（compactor 内部读最新 plans 文件，还是 loop/CLI 侧构造后传入）、何时构造未指明。建议补一句归属（推荐 compactor 内部，loop 只注入 plans 目录路径），并说明 resume 后"最新方案"按 id 时间戳取最大。
- **[S-06] ask_user options 的 0–6 个与元素类型校验位置未指明** — §4.3（行 164）。base.py 轻量校验子集仅支持 type/required/enum/properties/minimum，不支持 items/maxItems，schema 无法表达"0–6 个字符串数组"。建议明确由工具 execute 内自校验（或扩展校验器），否则该约束形同虚设。
- **[S-07] budget 估算未计入 tools 声明与输出预留（可选）** — §4.4 仅对 view() 求和。工具 schema 与本轮输出也占窗口，占用会系统性偏低、压缩触发偏晚。概设未强制要求，属精度改进：建议 estimate 入参预留 tools 文本长度（或声明偏差已接受）。
- **[S-08] memory_save"非工作区文件"措辞不准** — §4.2（行 158）。项目记忆 `<workspace>/.glaucous/memory.json` 就在工作区内。建议改为"写入的是系统内部存储（类比 audit.log 的系统写入），不属沙箱审批面"，结论（risk=SAFE）不变。
- **[S-09] 锚行建议附"目标一行"以对齐概设锚定义** — §4.7 锚行（行 197）仅含路径 + read_plan 提示；概设 §5.2 锚定义为「方案已就绪：.glaucous/plans/<id>.md（**目标一行 + 未完成任务清单**）」。可与 B-01 修复（方案 A）合并处理：改写后的 arguments 锚文本中附目标一行。
- **[S-10] FR-22"自动提炼须确认"路径的归属建议显式声明（可选）** — 本轮无"会话结束自动提炼"功能，FR-22 后半句自然不触发，但 spec 未声明该点。该功能在概设 §12 裁剪顺序中列第 2 位（可裁），建议在 §0 或范围声明中补一句"自动提炼候选属概设可裁剪项、未排期"，避免验收时被误判为遗漏。

## 已核实的事实性引用（抽样）

| spec 声明 | 核实结果 |
|---|---|
| base.py 已在 dispatch 成功/失败路径统一记账五字段（§4.6） | ✅ tools/base.py L260-268（成功）/L282-294（失败），五字段与概设 §4.2 一致 |
| push_raw_tool 产物无 _meta，L1 派生降级（§4.6） | ✅ history.py L82-84 走 `_tool_entry`，不含 metadata |
| submit_plan 落盘属 M2 任务、Day2 已登记简化（§4.7 前提） | ✅ tools/planning.py docstring L8-9 |
| build_system_prompt 签名扩展默认参数兼容（§4.9） | ✅ ui/prompts.py L40 现签名 `build_system_prompt(workspace)`；resume 路径 system_prompt 先建后传（cli.py L277-279） |
| L2 压缩复用 LLMClient.chat、不携带 tools（§2） | ✅ llm/client.py L70-75/L113-114：tools/on_text 均可空 |
| config 环境变量风格（GLAUCOUS_*） | ✅ config.py 既有 GLAUCOUS_MAX_STEPS 同范式 |
| 既有 61 用例 | ✅ 量级相符（4 个测试文件，实测 test 函数约 61~64 个，含参数化膨胀） |

## 需求覆盖对照表

| FR 编号 | 覆盖情况 | 说明 |
|---|---|---|
| FR-17 | 已覆盖 | ask_user 契约 question+options（§4.3），问题具体可答、附候选 ✓ |
| FR-18 | 已覆盖（软约束） | BASE_PROMPT 引导"环境类失败先自行重试 2 次"（§4.3）；与概设 §7.1 的 prompt 引导同构，符合设计 |
| FR-19 | 已覆盖 | ask_user 回答 → prompt 引导调 memory_save 沉淀（§4.3，不强制；与概设 §7.1"提示调用"一致） |
| FR-20 | 已覆盖 | 双层读取、全量注入不裁剪、超 4000 字符附提示（§4.1）；每会话现读现注入含 resume（§4.9）✓ |
| FR-21 | 部分覆盖 | 双作用域存储+新增+注入本轮完成（§4.2）；"可查看/删除"经 /memory 归 M3 —— §0 已声明，属合法裁剪 |
| FR-22 | 部分覆盖 | 规则/事实分存储分注入 ✓（rules.py 与 memory.py 分离、注入段独立）；"自动提炼须用户确认"路径本轮不存在（概设可裁剪项，建议按 S-10 显式声明归属） |
| FR-24 | 已覆盖 | L0 截断+落盘+read_output 回取（§4.5）→ L1 本地派生摘要（§4.6）→ L2 摘要压缩+方案锚保留（§4.7）；阈值 300行/50KB、头200+尾50、70%/85% 均与概设 §4.2 一致 |
| FR-25 | 已覆盖（阶段内） | budget 三档阈值与压缩共用常量（§4.4）、超阈值自动压缩（§4.6/4.7）、每轮 run() 结束渲染占用条；"常驻状态栏"形态归 M3（与计划表一致） |
| FR-31 | 部分覆盖 | 上下文占用 ✓（§4.4，spec 任务标注 FR-25/31 正确）；模式徽标/模型显示归 M3 3.2/3.3（计划表安排，非本 spec 缺口） |

**计划表 M2 任务覆盖**：2.1~2.8 全部覆盖（2.1→Step1、2.2→Step1/2、2.3→Step2、2.4→Step3、2.5→Step4、2.6→Step5、2.7→Step5、2.8→§9 债务登记），无超范围内容（read_plan/budget 事件/config 均为任务内支撑件）。

---
*评审依据：需求文档 v3.2、概要设计 v4.1、开发计划表 v1.0、前案 Day3 Plan、src/glaucous/ 现有代码。本报告仅覆盖文档评审，不涉及代码实现质量。*
