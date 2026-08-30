# Spec 一致性评审报告：V1.1-M3 会话管理 Spec（r2 聚焦复审）

> 评审日期：2026-08-30 22:30
> 评审对象：`docs/designs/202608302000-plan-v11-m3-sessions.md`
> 模式：**聚焦复审（r2，非全量重评）**——改动范围：决策记录 2/3/5/7、§〇 裁剪表（storage 逃生门行）、§3.1（status）、§3.2（find_by_prefix 三态 + prefix_candidates）、§3.3（/skill 包装行）、§4.1（四态消解）、§4.3（fork 细节补齐）、§5.1/§5.2（统一入口引用）、§5.3（turn_active 复位接线）、新增 §5.4（存储入口收敛）、§七（数据模型表同步）、§八（新增 2 行）、§9.3（fake 适配登记）；其余未改动章节继承 r1 结论
> 上一轮报告：`docs/reviews/202608302000-spec-review-v11-m3-sessions.md`（结论：不通过，B1/B2/B3 阻塞 + S1~S8 建议）
> 对照文档：编程智能体需求文档v1.1（§2.3 FR-44~51、§4）、编程智能体概要设计说明书v1.1（§6、§10）、Glaucous开发计划表v1.1（V1.1-M3 任务 3.1~3.6）
> 代码基线：`src/glaucous/context/history.py`、`cli.py`（resume_history L1184~1211 / repl 任务轮 L1550~1611）、`commands.py`（begin_turn L144~153 / _cmd_clear L253 / _audit L161~165 / _cmd_skill L422~425）、`permission/approval.py`（record_denial L165~179 / _event L183~203）、`tests/test_turn_collapse.py`（L246 fake）
> 结论：**不通过**（阻塞 1 项，建议 5 项）

## 一、上轮阻塞项处置

- **B1 —— 已修复**。turn_active 置位/复位口径在决策记录 5、§4.5、§5.3、§七 四处表述一致（置位=repl 任务轮 `loop.run` 前、复位=repl 轮末 finally、begin_turn 不触碰）。代码核实：`begin_turn`（commands.py L144~153）仅清 text_segment/turn_usage，三处调用点（cli.py L1560 任务轮、commands.py L258 `_cmd_clear`、L276 `_cmd_resume`）中命令路径两处均无 finally 复位——新口径将置位点移出命令路径后「轮间恒为 False」重新成立；cli.py 任务轮结构（L1560 begin_turn → L1570 `loop.run` → L1580 finally，KeyboardInterrupt/Exception 的 continue 均先经 finally）确认置位与复位均有明确落位，与既有 finally 结构兼容。
- **B2 —— 已修复**。决策记录 3 统一为「/clear 重置；/fork 继承当前值；切换会话时从索引 entry 恢复」，§4.3 步骤 3「token_used 继承」/步骤 4「session_usage 继承当前值（决策 3）」与决策记录不再矛盾；§5.1 步骤 4（启动恢复）与 §5.2（/sessions 切换）的恢复口径与决策记录一致。
- **B3 —— 部分修复，残留立新阻塞 B4**。§5.4 统一入口 `create_session_history` 已建立，repl 新会话、_cmd_clear 的收敛与 §9.3 test_turn_collapse fake 适配登记均已完成；但 §5.4 收敛表「resume 兜底 ×2 / 两个 return 分支」与代码事实（resume_history 内兜底 `History.create` 共 **3 处**）不符，详见 B4。

## 二、阻塞问题

### B4. §5.4 收敛表「resume 兜底 ×2」与代码 3 处兜底不符——FR-44 在残余分支上双轨分裂残留
- **维度**：需求一致性（FR-44 静默遗漏残留）——r1-B3 的未完全修复
- **spec 位置**：§5.4「现有 **4 处** `History.create` 调用仍走 workspace 旧路径，必须全部收敛」；收敛表行「**resume 兜底 ×2（未找到/恢复失败） | cli.py resume_history 两个 return 分支 | 换 create_session_history**」；§七「create_session_history(system_prompt, workspace) -> (History, degraded)（覆盖 repl/**resume 兑底**/_cmd_clear **共 4 处**）」；§5.1 步骤 3「其内部兜底『未找到→新建』也统一走 §5.4 入口」（单数指代，无法消解 ×2 与 3 的冲突）
- **代码事实**：`resume_history` 内兜底 `History.create(system_prompt, workspace)` 实际 **3 个 return 分支**——cli.py L1188~1190（`find_latest_session` 返回 None，「未找到可恢复的会话」）、L1196~1198（`--resume <id>` 无 candidates，「未找到会话」）、L1203~1205（`History.load` 异常，「恢复失败」）。加上 cli.py L1460（repl 新会话）与 commands.py L253（_cmd_clear），主会话创建调用点共 **5 处**，非 spec 声称的 4 处。r1-B3 证据已明确列出「cli.py L1190/L1198/L1205 resume_history 三处兜底」，本轮修订未据此修正。
- **冲突说明**：按 §5.4 字面（「两个 return 分支」）实现，三处兜底中必有一处不被收敛（spec 无法消解是哪一处）；该分支新建的会话仍落 `<workspace>/.glaucous/sessions/` 旧路径——迁移留空后再次产生用户级/工作区双轨分裂，且已改用户级的 `find_latest_session`（§5.2）永远找不到它，「/resume latest」对该会话失效。与 r1-B3 的阻塞本质（FR-44「会话文件迁移至 ~/.glaucous/sessions/\<project-hash\>/」的静默遗漏）同性质，在残余路径上重现。
- **修复方向**：§5.4 收敛表将 resume 行改为三处兜底分支（未找到可恢复会话 / 未找到会话 \<id\> / 恢复失败），「4 处」总数改为 5 处（repl 1 + resume 3 + _cmd_clear 1），§七「共 4 处」与「兑底」错别字同步更正；§5.1 步骤 3 的「兜底」表述精确为「三处兜底 return 分支」。

## 三、建议问题

### S9. 概述层未随 §5.4 收敛同步：§一 流程图/分层影响表、§十 汇总表、§5.1 步骤 2
- **维度**：结构与可执行性（内部表述不同步，权威章节明确、不误导实现）
- **位置与摘录**：§一 流程图仍写「`History.create(session_dir=project_dir(workspace)) 或 resume（用户级目录）」；§一 分层影响表 commands.py 行「ReplContext 增 session_usage/turn_active；新命令 _cmd_sessions/...」（缺 `session_index` 字段、缺 _cmd_clear 接入 create_session_history）；§十 commands.py 行「ReplContext 三新字段、4 新命令、META/USAGE、分派」（未列 create_session_history 接入）；§5.1 步骤 2「经统一存储入口 `create_session_history(system_prompt, workspace)`（§5.4）」未体现 (History, degraded) 二元组解包。§5.4/§七/§5.3 已是权威明确口径。
- **建议**：§一 流程图改提统一入口（或注明经 §5.4 封装）、分层影响表 commands.py 行补 `session_index` 与 _cmd_clear 接线、§十 同步、§5.1 步骤 2 补解包示意（`history, degraded = ...`）。

### S10. /resume 命令路径（运行中同项目切换）的 session_usage 恢复未定义——决策 3「切换会话时从索引 entry 恢复」的落地缺口
- **维度**：结构与可执行性（决策 3 波及面：恢复路径覆盖不全）
- **位置与摘录**：决策记录 3「**切换会话时从索引 entry 恢复**」；§4.5 将 /resume 列入切换类命令；§5.1 步骤 4 的恢复动作限定「**启动后**首次 touch（……恢复时 token_used 从索引恢复进 session_usage）」；§5.2 仅在「/sessions \<id\> 跨项目切换」处写「恢复的 token_used 写回 session_usage」。运行中 /resume（走 resume_history 同项目路径）的 session_usage 取值无任何条目定义。
- **冲突说明**：/resume 恢复后 session_usage 若保持 0（或前会话值），首轮结束后 §5.3 的 `SessionIndex.touch(token_used=…)` 将以未恢复值**覆写**索引 token_used 快照——FR-45 数据被破坏。/resume 属决策 3 所称「切换会话」，属本轮改动章节的波及面。
- **建议**：在 §5.2 resume_history 改造条目中明确：/resume 恢复成功后同样从索引 entry 恢复 token_used 进 session_usage（查不到索引条目时按 0 起算并声明）。

### S11. /stats 的 agent 维度小计对无 agent 字段的行（record_denial）归属未定义
- **维度**：结构与可执行性（r1-S1 修复后残留的同构风险，转移到 agent 字段）
- **位置与摘录**：决策记录 7「审批管线行（`time/decision/agent/...`）」；§4.4「审批决策分布（……按 decision 字段聚合计数，**含 agent 维度小计 main/child-N**）」。代码事实：审批管线 `_event` 行含 `agent`（approval.py L199），但 `record_denial` 行（plan_mode_blocked，L167~179）**有 `decision` 无 `agent` 字段**——决策 7 的格式概括与该行不符。
- **冲突说明**：record_denial 行按 §八/决策 7 口径会被统计（有 decision），但 agent 维度聚合对这些行按 `entry["agent"]` 直取会 KeyError——与 r1-S1 指出的 decision 直取风险同构。
- **建议**：决策 7 概括改为「time/decision/…（agent 字段除 record_denial 行外恒有）」或 §4.4/§六 明确 agent 维度取 `entry.get("agent", "main")`（或标注「无 agent 字段的行不进 agent 小计」），二选一。

### S12. §九 测试清单未覆盖本轮新增行为与口径
- **维度**：结构与可执行性（测试与验证方式未随改动同步）
- **位置与摘录**：§9.2 仅列「空态提示、当前项目列表、`a` 全部项目、kw 名称搜索、id 前缀切换（含跨项目）」——§4.1 ③ 新增的「多命中 → 列出候选列表」与 §3.2 `prefix_candidates` 无对应用例；§八 新增的「行无 decision 字段 → 跳过不统计」（决策 7）无验证用例（r1-S1 曾建议预置一条 mode_switch 行）；§5.4 的 degraded=True 降级路径无测试安排（§9.1/9.2 均未列）。
- **建议**：§9.2 补三点：多命中 kw → 候选列表且不切换；预制 audit.log 含一条命令审计行（at/event）断言不计入决策分布；monkeypatch 让 `project_dir`/`create` 抛 OSError 断言降级返回 (History, degraded=True)。

### S13. 表述精度残留两处
- **维度**：结构与可执行性（表述与事实的小出入，不影响实现判断）
- **位置与摘录**：
  1. §3.1 标题「索引数据模型（**字段集 = 概设 §6.1**）」——SessionEntry 现含 status（7 字段对齐）但**多出概设没有的 workspace 字段**（概设 §6.1 entry 为 id/name/created_at/updated_at/message_count/token_used/status，共 7 项），「=」字面失准（workspace 与 FR-45「id/名称/工作区/时间/消息数/token」对齐，合理超集）；
  2. §4.3 步骤 4「`History.load(新文件, ctx.system_prompt)` 三元组解包（history, meta_workspace, warnings，r1-S3）」——解包后 **warnings 的呈现口径未声明**（resume 路径代码先例是 `renderer.note` 逐条呈现，cli.py L1208~1209），实现者可能静默丢弃。
- **建议**：§3.1 改为「概设 §6.1 字段集 + workspace（FR-45 对齐）」；§4.3 补一句 warnings 处置（比照 resume 路径 renderer.note 呈现，或显式声明丢弃）。

## 四、通过项（本轮实际复查；未列项继承 r1 结论）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | FR-50 切换保护实现口径：决策 5/§4.5/§5.3/§七 四处一致；与 begin_turn 三调用点、repl finally 结构（cli.py L1560/L1570/L1580）核实兼容，「轮间恒为 False」成立 | ✓ |
| 需求一致性 | FR-49 统计口径：决策 7 双格式登记与 audit.log 代码事实相符（审批行 time/decision、命令行 at/event）；§六 docstring、§八 过滤行三处呼应 | ✓（agent 字段残留见 S11） |
| 需求一致性 | FR-44 存储收敛：§5.4 统一入口机制成立，_cmd_clear/repl 收敛与 §9.3 fake 适配（test_turn_collapse.py L246 两参 fake 核实）登记到位 | ✗（resume 兜底 ×2 vs 代码 3 处，见 B4） |
| 结构与可执行性 | session_usage 三路径口径自洽：决策 3 vs §4.3（继承）/§5.1 步骤 4（启动恢复）/§5.2（/sessions 恢复） | ✓（/resume 路径缺口见 S10） |
| 结构与可执行性 | §3.2/§4.1 消解规则闭环：三态 find_by_prefix + prefix_candidates 与四态调用侧对应，workspace 传当前项目语义明确 | ✓ |
| 结构与可执行性 | §4.3 fork 细节：created_at 沿用 meta.created_at（rebuild 跳变消除）、History.load 三元组解包与代码返回（history.py L248）一致、ctx.state/session_events 处置已明确定义 | ✓（warnings 见 S13） |
| 结构与可执行性 | §3.1 status 恒 active：概设 §6.1 status 字段对齐落实，取值定义清楚 | ✓（表述见 S13） |
| 结构与可执行性 | §3.3 /skill 包装行跳过：规则可执行，消除 r1-S5 的固定引导语前缀与换行符故障；附注——按组装文本结构（commands.py L422~425），顺延行实际命中「[技能 {name}]」标签行而非用户任务段，含技能名有区分度、/rename 可覆盖，若意图取用户任务段需再明确顺延目标 | ✓ |
| 结构与可执行性 | §〇 storage 逃生门裁剪登记：与概设 §10 `[sessions] storage = "user" | workspace` 对应，偿还去向明确（TODO.md） | ✓ |
| 结构与可执行性 | 决策记录 2 已知边界登记（rebuild 丢失手动命名，TODO 偿还 meta 增字段） | ✓ |
| 结构与可执行性 | 降级接线（r1-S8）：§5.4 docstring 与 §八 均明确 OSError → (History, degraded=True) + 回退 workspace 旧路径，与 create_session_file mkdir 无捕获的代码事实兼容 | ✓ |

**继承上轮（未改动且未被波及）**：硬约束合规（需求 §5 继承项）、FR-45~47/51 覆盖与计划表 3.1~3.6 对应、/fork 收窄裁剪合规、范围守恒、场景 I 数据流、存储布局/命令面语义/索引写入时机/重建降级与概设 §6 一致、头部要素与三链接可达、fork→rebuild_loop 依赖顺序、轮末接线时序、§十 任务映射——以上按 r1 通过项结论继承；其中 §十/§一 的概述层同步问题本轮以 S9 记录。

## 五、复审要求

**结论：不通过**——存在阻塞项 B4（r1-B3 残留），必须修复后复审。

1. **B4**（必须）：§5.4 收敛表 resume 行改为三处兜底分支、「4 处」总数改 5 处（§七 与 §5.1 步骤 3 同步更正），确保 resume_history 全部兜底路径接入 create_session_history。
2. 建议项 S9~S13 不阻塞放行，随 B4 一并处理成本最低：S9/S12 为本轮改动的同步收尾；S10 涉及索引数据正确性建议优先；S11/S13 为口径表述补齐。如暂缓，请登记至 TODO.md 并注明去向。

复审时按规范生成新报告文件（轮次 r3），不覆盖本报告。
