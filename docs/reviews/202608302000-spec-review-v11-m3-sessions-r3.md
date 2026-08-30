# Spec 一致性评审报告：V1.1-M3 会话管理 Spec（r3 聚焦复审）

> 评审日期：2026-08-30 23:50
> 评审对象：`docs/designs/202608302000-plan-v11-m3-sessions.md`
> 模式：**聚焦复审（r3，非全量重评）**——改动范围：§5.4 收敛表（B4）、§一 分层影响表 / §5.1 步骤 2 / §5.4 代码块签名（S9）、§5.2 session_usage 恢复口径（S10）、§4.4 未标注桶（S11）、§9.2 测试清单三条（S12）、§3.1 字段集表述与 §4.3 warnings 口径（S13）；其余未改动章节继承 r1/r2 结论
> 上一轮报告：`docs/reviews/202608302000-spec-review-v11-m3-sessions-r2.md`（结论：不通过，B4 阻塞 + S9~S13 建议；B1/B2 已确认消除）
> 对照文档：编程智能体需求文档v1.1（§2.3 FR-44~51、§4）、编程智能体概要设计说明书v1.1（§6.1）、Glaucous开发计划表v1.1（V1.1-M3 任务 3.1~3.6）
> 代码基线：`src/glaucous/cli.py`（resume_history L1178~1211 / repl L1457~1460）、`commands.py`（_cmd_clear L253）、`permission/approval.py`（record_denial L165~179 / _event L183~203）、`context/history.py`（load L207~218）
> 结论：**不通过**（阻塞 1 项，建议 3 项）

## 一、上轮阻塞项处置

- **B4 —— 部分修复，残留立新阻塞 B5**。核心收敛表述已修复且与代码核实一致：§5.4 现写「现有 **5 处** `History.create` 调用仍走 workspace 旧路径（r2-B4 修正：resume_history 兜底实为 **3 处**——未找到/前缀未命中/恢复失败，cli.py L1190/L1198/L1205），必须全部收敛」，收敛表行「resume 兜底 ×3（未找到/前缀未命中/恢复失败）｜ cli.py resume_history 三个 return 分支」。**代码逐处核实**：cli.py L1190（未找到可恢复的会话）、L1198（未找到会话 \<id\>）、L1205（恢复失败）三处兜底 + L1460（repl 新会话）+ commands.py L253（_cmd_clear）= 主会话创建 **5 处**，与修订后 §5.4 完全吻合（subagent.py L170 走 `subdir="agents"`，属决策 6 排除范围，不计入）；§一 分层影响表（「5 处 History.create 收敛（repl 新会话 + resume_history 兜底 ×3 + 经 commands._cmd_clear）」）与 §5.1 步骤 2（「覆盖全部 5 处新建会话调用点」）已同步。**但 B4 修复方向明确要求的「§七『共 4 处』与『兑底』错别字同步更正」未执行**——§七 仍为「（覆盖 repl/resume 兑底/_cmd_clear 共 4 处）」，残留为阻塞 B5；§5.1 步骤 3「兜底」单数表述未精确化，因 §5.4 已权威明确，降记建议 S15。

## 二、上轮建议项（S9~S13）落实核对

| 项 | 结论 | 核对依据 |
|---|---|---|
| S9 | **部分落实**（声明范围内的三项全部落实；r2 修复方向其余两处残留 → S14） | 分层影响表 commands.py 行已补 `session_index` 与「_cmd_clear 换统一新建入口」、cli.py 行已写明「5 处 History.create 收敛（…×3…）」、tests 行已列 test_turn_collapse（fake 适配）；§5.1 步骤 2 已增「返回二元组 `(history, degraded)` 解包」；§5.4 代码块签名已改 `-> tuple[History, bool]` |
| S10 | **已落实**（核心口径） | §5.2 新增「切换后的 session_usage 恢复口径（r2-S10，适用于 /sessions 切换与运行中 /resume）……恢复时 `session_usage = {"prompt": entry.token_used, "completion": 0}`……touch 时 token_used = prompt+completion 保持单调不减，避免未恢复值覆写索引」——与决策 3「切换会话时从索引 entry 恢复」一致；单调不减闭环成立（恢复 {prompt:T, completion:0} → 首轮 touch 写 T+turn_usage ≥ 索引快照 T，FR-45 快照不被覆写）。附注：r2 建议中「查不到索引条目时按 0 起算并声明」的显式声明未附——写入侧已有 §3.2 touch 兜底 upsert 与 §八 覆盖，不再另立项 |
| S11 | **已落实** | §4.4「无 agent 字段的行归入「未标注」桶（r2-S11，如 plan_mode_blocked 命令审计行）」——代码核实成立：`record_denial` 行含 `decision: "plan_mode_blocked"` 且无 agent 字段（approval.py L167~178），`_event` 行恒有 agent（L199）；r2 建议「二选一」，作者选 §4.4 路线，决策 7 概括维持原文可接受。术语与测试输入两处表述精度 → S16 |
| S12 | **已落实** | §9.2 三条全部补齐：多命中候选（对应 §4.1 ③ 与 §3.2 prefix_candidates）、无 decision 行过滤 + 未标注桶（对应决策 7/§八）、degraded 降级路径（对应 §5.4 docstring 与 §八），口径均一致 |
| S13 | **已落实** | §3.1 标题改「字段集 = 概设 §6.1 + per-entry 冗余 workspace，r2-S13 表述修正」——已核实概设 v1.1 §6.1 entry 为 id/name/created_at/updated_at/message_count/token_used/status 七字段，spec 八字段（+workspace）为准确超集表述；§4.3 步骤 4 补「warnings 逐条 renderer.note 呈现」，与 resume 路径代码先例（cli.py L1208~1209）一致 |

## 三、阻塞问题

### B5. §七「共 4 处」与「兑底」未随 B4 修正同步——与 §5.4/§一 的「5 处」覆盖总数矛盾残留
- **维度**：结构与可执行性（文档内部矛盾影响实现判断）——r2-B4 修复方向的未完成子项
- **spec 位置**：§七「新建会话唯一入口｜`create_session_history(system_prompt, workspace) -> (History, degraded)`（覆盖 repl/resume **兑底**/_cmd_clear **共 4 处**）」；对照同文档 §5.4「现有 **5 处** `History.create` 调用仍走 workspace 旧路径（r2-B4 修正：resume_history 兜底实为 **3 处**……）必须全部收敛」与 §一 分层影响表「**5 处 History.create 收敛**（repl 新会话 + resume_history 兜底 ×3 + 经 commands._cmd_clear）」
- **代码事实**：主会话创建调用点共 **5 处**（cli.py L1190/L1198/L1205 兜底 ×3 + L1460 repl + commands.py L253 _cmd_clear），§5.4/§一 的「5 处」正确，§七 的「共 4 处」为 r2-B4 修正前的旧数
- **上游位置**：需求文档v1.1 §2.3 FR-44「会话文件迁移至 ~/.glaucous/sessions/\<project-hash\>/，按项目目录哈希分目录；meta 记录原工作区路径」
- **冲突说明**：§七 是「数据模型与接口汇总」，是实现者核对收敛覆盖面的常查入口；「共 4 处」会引导少收敛一处 resume 兜底——该分支新建会话仍落 `<workspace>/.glaucous/sessions/` 旧路径，重现 r1-B3/r2-B4 的存储双轨分裂（FR-44 静默遗漏），且已改用户级的 `find_latest_session` 对该会话失效。r2-B4 修复方向明确要求「§七『共 4 处』与『兑底』错别字同步更正」，本轮未执行，按聚焦复审规则维持阻塞。
- **修复方向**：§七 该行「共 4 处」改「共 5 处」、「兑底」改「兜底」，与 §5.4/§一 对齐；一行同步修正，机制无需改动。

## 四、建议问题

### S14. 概述层与 §5.4 签名的同步残留（r2-S9 部分落实）
- **维度**：结构与可执行性（表述不同步；权威章节明确、不误导实现）
- **位置与摘录**：
  1. §5.4 引言行「新增 `sessions/paths.py::create_session_history(system_prompt, workspace) **-> History**`：」与同节代码块「`def create_session_history(...) -> tuple[History, bool]`」不一致——本轮只改了代码块，紧邻引言行遗漏（其余三处引用 §5.1 步骤 2/§七/§九 均为二元组口径）；
  2. §一 流程图仍写「`History.create(session_dir=project_dir(workspace)) 或 resume（用户级目录）`」——r2-S9 修复方向项（改提统一入口或注明经 §5.4 封装），未列入本轮修复清单；可读作统一入口的内部机制，误导性低；
  3. §十 commands.py 行「ReplContext 三新字段、4 新命令、META/USAGE、分派」仍未列统一入口接入（r2-S9 修复方向项）。
- **建议**：§5.4 引言行补 `-> tuple[History, bool]`；§一 流程图与 §十 随下次修订一并同步（或登记 TODO 注明去向）。

### S15. §5.1 步骤 3「兜底」单数表述未精确化（r2-B4 修复方向残留项，降级记录）
- **维度**：结构与可执行性（表述精度）
- **位置与摘录**：§5.1 步骤 3「恢复路径：`resume_history(workspace, ...)` 内部改为用户级目录（§5.2），其内部**兜底**「未找到→新建」也统一走 §5.4 入口」——r2-B4 修复方向要求精确为「三处兜底 return 分支」，未执行。
- **建议**：r2 时该项因 §5.4 自身「×2/两个 return 分支」无法消解而并入阻塞；现 §5.4 已权威明确（×3 + 行号），「统一走」语义可覆盖全部兜底分支，不再误导实现，故降为表述精度建议：改为「其内部三处兜底 return 分支也统一走 §5.4 入口」。

### S16. plan_mode_blocked 行的表述精度：§4.4 术语误用 + §9.2 测试输入指代不明
- **维度**：结构与可执行性（术语统一 / 测试描述精度）
- **位置与摘录**：
  1. §4.4「无 agent 字段的行归入「未标注」桶（r2-S11，如 plan_mode_blocked **命令审计行**）」——按决策 7 的格式分类，「命令审计行」专指 `at/event/...` 格式；而 plan_mode_blocked 行由 `record_denial` 写入（time/mode/…/decision/allowed，**无 agent**，approval.py L167~178），属审批管线行家族（r2-S11 原文亦按此定位）。术语与决策 7 冲突（行引用本身无歧义）；
  2. §9.2「**无 decision 行过滤（r2-S12）**：audit.log 含 at/event 格式行（无 decision）→ 不计入决策分布；**无 agent 字段 → 归「未标注」桶**」——后半句紧随 at/event 行描述，可误读为「at/event 行归未标注桶」，与 §八「行无 decision 字段……跳过不统计」的过滤顺序冲突。「未标注」桶的实际适用行是**有 decision 无 agent** 的行（即 plan_mode_blocked 形态）。
- **建议**：§4.4 措辞改为「如 plan_mode_blocked（record_denial 审批管线行，缺 agent）」之类与决策 7 分类一致的表述；§9.2 该条拆清两个断言的输入——①预制 at/event 行断言不计入决策分布，②预制一条含 decision 无 agent 的行断言归「未标注」桶。

## 五、通过项（本轮实际复查；未列项继承 r1/r2 结论）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | FR-44 收敛覆盖：§5.4「5 处 / 兜底 ×3」与代码逐处核实一致（cli.py L1190/L1198/L1205/L1460 + commands.py L253）；§一 分层影响表、§5.1 步骤 2 已同步 | ✓（§七 汇总残留见 B5） |
| 需求一致性 | FR-45 数据保护：§5.2 恢复口径使 touch 写入值恒 ≥ 索引快照（单调不减），r2-S10 指出的「未恢复值覆写索引」风险消除 | ✓ |
| 需求一致性 | 决策 3 三路径口径自洽：/clear 重置、/fork 继承当前值（§4.3 步骤 4）、切换恢复（§5.1 步骤 4 启动路径 + §5.2 新增 /sessions 切换与运行中 /resume 口径），四处表述无矛盾；与 §4.4 展示（历史累计计入 ↑ 侧）已声明 | ✓ |
| 结构与可执行性 | §5.4 二元组口径一致性：§5.1 步骤 2（解包）✓、§9.2 degraded 测试（回退旧路径 + degraded=True + 启动不阻断）✓、§八 降级行 ✓、§七 签名 ✓ | ✓（引言行见 S14） |
| 结构与可执行性 | S11 未标注桶与代码事实相符（record_denial 有 decision 无 agent；_event 恒有 agent），与决策 7/§八 过滤顺序无冲突 | ✓（表述见 S16） |
| 结构与可执行性 | §9.2 三条新增测试与 §4.1 ③ / §3.2 prefix_candidates、§5.4 降级口径对应完整 | ✓（测试输入见 S16） |
| 结构与可执行性 | §3.1 字段集表述与概设 v1.1 §6.1 核实一致（七字段 + workspace 超集）；§七「entry 字段见 §3.1」引用有效 | ✓ |
| 结构与可执行性 | §4.3 warnings 呈现口径与 resume 代码先例一致（cli.py L1208~1209 renderer.note 逐条） | ✓ |
| 结构与可执行性 | 波及面：§5.4「5 处」与 subagent.py L170（`subdir="agents"`）不冲突（决策 6 排除 + 收敛表穷举五处）；§5.2 新口径未波及 §4.3（/fork 继承语义独立）、§5.3（token_used=prompt+completion 与之衔接） | ✓ |

**继承上轮（未改动且未被波及）**：硬约束合规（需求 §4/§5）、FR-46~48/50/51 覆盖与计划表 3.1~3.6 对应、/fork 收窄裁剪合规、范围守恒、§3.2/§4.1 消解规则闭环、§4.5 切换保护四处口径、§〇 裁剪登记、决策 1/2/6/7 已知边界、头部要素与三链接可达（r1/r2 已核，本轮头部未改）——按 r1/r2 通过项结论继承。

## 六、复审要求

**结论：不通过**——存在阻塞项 B5，必须修复后复审（r4）。

1. **B5**（必须）：§七「新建会话唯一入口」行「共 4 处」→「共 5 处」、「兑底」→「兜底」，与 §5.4/§一 的收敛总数对齐。
2. 建议项 S14~S16 不阻塞放行：S14/S15 为本轮改动的同步收尾（各一行修正，成本最低，建议随 B5 一并处理）；S16 为术语与测试描述精度。如暂缓，请登记至 TODO.md 并注明去向。

复审时按规范生成新报告文件（轮次 r4），不覆盖本报告。
