# 代码评审报告：V1.1-M3 会话管理 Spec（第 1 轮）

> 评审日期：2026-08-30 21:00
> 评审对象：spec `docs/designs/202608302000-plan-v11-m3-sessions.md`；代码全量（改动清单：新增 src/glaucous/sessions/{__init__,paths,index,stats}.py、tests/test_sessions_index.py、tests/test_sessions_commands.py；修改 src/glaucous/context/history.py、src/glaucous/commands.py、src/glaucous/cli.py、tests/test_turn_collapse.py——git status 与声明范围逐一核对一致，另 TODO.md 修改为 spec 声明的债务登记）
> 模式：全量评审
> 结论：**不通过**（阻塞 2 项，建议 8 项）

## 一、阻塞问题

### B1. 索引写入失败被静默吞掉，spec 要求的「renderer 告警一条」未实现
- **维度**：Spec 符合性（错误处理策略）
- **代码位置**：src/glaucous/sessions/index.py:305~315——`_write` 的 `except OSError: pass`；全部写入路径（upsert index.py:168~182、touch index.py:184~230）经此收口，SessionIndex 全类无任何 renderer/告警通道。受影响调用面：轮末刷新（cli.py:1636~1643）、启动登记（cli.py:1505）、/rename（commands.py:700）、/fork upsert（commands.py:733~742）、迁移 upsert（paths.py:94~96）。
- **spec 位置**：§3.2「写入时机（概设 §6.1）：会话创建、首条用户消息自动命名、每轮结束刷新（updated_at/message_count/token_used）、/rename、/fork、迁移。全部**尽力而为（失败打印一条 renderer 告警，不阻断对话）**」；§八「索引写入失败（IO/权限）｜尽力而为：**renderer 告警一条**，对话继续」。
- **冲突/缺陷说明**：索引文件落盘失败（权限/磁盘满/只读目录）时用户零感知：/sessions 列表与搜索持续呈现陈旧数据、token 累计停止落账、切换目标可能已失效——spec 的「尽力而为」明确以「告警一条」为呈现要求，实现只做到了「不阻断」半边，错误处理策略与 spec 不一致。
- **修复方向**：SessionIndex 增加告警回调（如 `alert: Callable[[str], None] | None = None`，repl 装配时传 `lambda msg: theme.note(f"⚠ 会话索引写入失败：{msg}")`），_write 失败时调用一次（同轮重复失败可去重节流），保持不阻断语义。

### B2. /fork 的读/写 IO 失败与空文件路径未按 §八「报错保持原会话」兜底，异常直接击穿 REPL
- **维度**：Spec 符合性（错误处理策略）/ 逻辑正确性
- **代码位置**：src/glaucous/commands.py:719（`lines = src.read_text(encoding="utf-8").splitlines()` 裸调用）、721（`lines[0]`——空文件时 IndexError）、727（`new_file.write_text(...)` 裸调用）；对照同函数已兜底的 712~714（源文件不存在）与 724~726（meta JSON 损坏）。而 cli.py:1598~1599 的 `await handle_command(task, ctx)` 无 try/except，异常穿透 repl → asyncio.run → 进程带 traceback 退出。
- **spec 位置**：§八「/fork 当前会话无 JSONL（未落盘）｜不会发生（create 即落盘）；**IO 失败 → 报错保持原会话**」。
- **冲突/缺陷说明**：spec「不会发生」的前提并不严格成立：History.create 的 meta 落盘本身尽力而为（history.py:209~213 `except OSError: pass`），open("a") 已建文件但写入失败即留下空文件——/fork 走到 721 行 IndexError；磁盘/权限类 OSError（读源、写新）同样未捕获。两种路径用户得到的都是崩溃退出，而非「报错保持原会话」。
- **修复方向**：_cmd_fork 的读源/写新段包 `try/except (OSError, IndexError, ValueError)` → `ctx.renderer.error("分叉失败：…（原会话保持不变）")` 并 return True；写新文件失败时的半写残留可容忍（每次 fork 生成新名不冲突）或 finally 清理。
## 二、建议问题

### S1. /sessions 列表卡与 /stats 双卡在单列 make_card 上自动扩列，标题栏布局异常（M2-r1-S5 同类）
- **维度**：逻辑正确性（渲染细节）
- **spec 位置**：§4.1「列表卡（make_card）」、§4.4「统计卡（make_card 两个区块）」——卡形态合规，列布局为实现细节。
- **代码位置**：src/glaucous/commands.py:620~629 与 774~803（列表卡三格行、统计卡两格行）；src/glaucous/theme.py:104~125（非 key_value 分支仅建一列）。
- **冲突/缺陷说明**：rich Table 对超列数行自动扩列（M2 评审 WSL 实测先例，不崩溃）：整卡变为 3/2 列表格，标题只占第一列的表头位，「┌─ 标题 ─┐」标题栏形态被改变。
- **修复方向**：多列卡改 key_value 形态构建，或 make_card 增加列数参数 / 独立多列 Table（对齐审批卡先例）。

### S2. 「a」全部项目视图未按 spec「同项目聚组」
- **维度**：Spec 符合性（展示行为）
- **代码位置**：src/glaucous/commands.py:649~650 直接渲染 `ctx.session_index.all_sessions()`；src/glaucous/sessions/index.py:245~256 all_sessions 按 updated_at 全局倒序平铺。
- **spec 位置**：§4.1「**kw == "a"**：全部项目列表（行首加 workspace 尾段，**同项目聚组**）」。
- **冲突/缺陷说明**：行首 workspace 尾段已实现（show_workspace=True），但排序为全局时间倒序，不同项目会话交错呈现，无聚组。
- **修复方向**：/sessions a 路径先按 workspace 分组、组内按 updated_at 倒序后再传入渲染（约 3 行改动）。

### S3. 迁移汇总行「已迁移 N 个」把跳过/失败/告警行计入 N
- **维度**：逻辑正确性（计数口径）
- **代码位置**：src/glaucous/cli.py:1483~1487——`theme.note(f"已迁移 {len(migrated)} 个旧会话到用户级存储。")`；而 src/glaucous/sessions/paths.py:86~98 返回的日志行含「⚠ 跳过（目标已存在同名会话）」「⚠ 迁移失败」「⚠ 用户级会话目录不可用」。
- **spec 位置**：§5.1 步骤 1「非空逐行 theme.note(日志) + 汇总行『已迁移 N 个旧会话到用户级存储』」。
- **冲突/缺陷说明**：N 应为实际迁移成功数；实现按日志行数计——3 文件 1 成功 1 跳过 1 目录不可用时报「已迁移 3 个」，与逐行日志自相矛盾。
- **修复方向**：paths.py 返回结构化结果（或按「已迁移」前缀计数），汇总行只统计成功迁移数。
### S4. 接口签名/时点与 spec 文本的偏差（提请作者确认，随 spec 修订同步）
- **维度**：Spec 符合性（签名级，加法扩展）
- **代码位置**：①src/glaucous/sessions/index.py:184~193——touch 增 spec §3.2 签名未定义的 `auto_name` 参数；②src/glaucous/sessions/paths.py:65——`migrate_legacy_sessions(workspace, index=None)` 增 spec §二签名未定义的第二参数；③自动命名落点：cli.py:1636~1643 在轮末 finally touch 落名，spec §3.3 为「新会话首条用户消息进入时（repl 任务轮入口、begin_turn 之后）」。
- **spec 位置**：§3.2 touch 签名（仅 name/message_count/token_used）、§二 migrate 签名（仅 workspace）、§3.3 命名时机。
- **冲突/缺陷说明**：三处均为加法扩展/时点后移：auto_name 承载「不覆盖手动名」语义、index 参数复用 repl 实例避免二次装配、轮末落名在正常流程下终态等价（轮中崩溃可由重建派生自愈），无功能损失；但与 spec 文本不一致，按签名一致性口径登记。
- **修复方向**：提请 spec 作者确认后修订 §3.2/§二/§3.3，或在代码注释注明「spec 签名的实现扩展」。

### S5. /sessions 切换对 History.load 无异常兜底（索引陈旧 → REPL 崩溃）；degraded 会话入索引后无法切回
- **维度**：逻辑正确性（健壮性）
- **代码位置**：src/glaucous/commands.py:675（`History.load(session_file, ctx.system_prompt)` 无 try）——对照同批 resume_history 已捕获 (ValueError, OSError)（cli.py:1214~1221）；复合边界：degraded 降级会话（文件在工作区旧路径）经启动 touch（cli.py:1505）入索引后，_entry_file（commands.py:583~587）恒拼用户级 project-hash 路径，切换必失败且走 675 行裸调用。
- **spec 位置**：§5.2「/sessions <id> 跨项目切换：走 SessionIndex.find_by_prefix 拿到绝对路径 → History.load(path, system_prompt)」——spec 未单列该场景失败策略，属健壮性缺口（经 B2 所述无兜底的 handle_command 放大为进程退出）。
- **冲突/缺陷说明**：索引与文件系统可能失同步（外部移除 ~/.glaucous/sessions 下文件、B1 场景下索引写失败期间清理等），当前实现一触即崩。
- **修复方向**：_switch_to_session 包 try/except (ValueError, OSError) → renderer.error 并保持当前会话；_entry_file 可先行 exists 校验给友好提示。
### S6. 测试完备性对照 spec §9.1/§9.2 存在缺口
- **维度**：Spec 符合性（测试完备性）
- **代码位置**：tests/test_sessions_index.py（TestIndexCrud:52~94 无「原子写：损坏 tmp 不影响原文件」用例）；tests/test_sessions_commands.py 全文缺：①「a」全部项目视图用例；②跨项目 id 前缀切换用例（test_switch_by_id_restores_usage:117~124 仅当前项目）；③/stats 内容断言（test_stats_renders_without_error:236~244 仅断言无错误，角色分布/全局聚合零断言）；④「切换后未提交修改提示：mock git status 返回非空（monkeypatch subprocess）」——_note_uncommitted/_git_dirty 零测试覆盖。
- **spec 位置**：§9.1 第 2 条「索引 upsert/touch/remove 幂等与字段覆盖语义；**原子写（损坏 tmp 不影响原文件）**」；§9.2「/sessions：……`a` 全部项目、id 前缀切换（**含跨项目**）」「/stats：角色分布、决策分布（预制 audit.log）、**全局聚合**」「切换后未提交修改提示：mock git status 返回非空（monkeypatch subprocess）」。
- **冲突/缺陷说明**：主体行为均有覆盖（与 M2-r1-S7 同类的断言形态缺口），其中 git dirty 提示为 spec 点名的可测形态、完全无覆盖。
- **修复方向**：按 §9.1/§9.2 清单补齐；优先补跨项目切换与 monkeypatch subprocess 的 git dirty 两条。

### S7. 会话中途索引损坏的重建为静默降级，无「索引已重建」提示
- **维度**：Spec 符合性（提示口径，轻微）
- **代码位置**：src/glaucous/sessions/index.py:170~172/200~202/247~249（upsert/touch/all_sessions 的 corrupted→rebuild 均静默）；提示仅存在于启动路径（cli.py:1488~1491）。
- **spec 位置**：§八「索引文件缺失/JSON 损坏｜load 返回 corrupted → 列表/切换前触发 rebuild（降级路径明确，FR-45），**提示「索引已重建」**」。
- **冲突/缺陷说明**：运行中索引损坏后首次查询自愈重建但无提示，提示口径仅在启动路径满足；影响轻微（重建自愈、无数据损失）。
- **修复方向**：与 B1 的告警回调合并实现（rebuild 成功时发一条提示）。

### S8. /sessions 切换保留当前 mode/policy，与 /resume「重置为启动默认」语义不一致（spec 未明确，提请作者确认）
- **维度**：逻辑正确性（语义一致性）
- **代码位置**：src/glaucous/commands.py:671~688（_switch_to_session 不触碰 ctx.state）；对照 /resume 路径 `ctx.state = state`（SessionState()，commands.py:301~302 + cli.py:1235）。
- **spec 位置**：§5.2 对 /sessions 切换仅规定 History.load 与 token 恢复，未提 state；resume_history docstring「state 重置为启动默认（v1.1：Build + auto-approve，策略不跨会话持久化）」。
- **冲突/缺陷说明**：同为「切到历史会话」，/sessions 切换延续当前模式/策略，/resume 重置为 Build+auto——两条恢复路径语义分叉（如 Plan 模式下切换回的会话仍受只读约束）。spec 未定义，不判违规，提请作者确认统一口径。
- **修复方向**：确认后二选一：_switch_to_session 同步重置 state，或 spec 修订注明「/sessions 切换保留当前模式/策略」。
## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | FR-44 五处收敛齐全：cli.py:1197/1208/1218（resume_history 兜底 ×3）+ 1497（repl 新会话）+ commands.py:277（_cmd_clear）全部换 create_session_history；全仓检索无主会话 History.create 残留（subagent.py:170 subdir="agents" 为 M2 既有语义保留）；入口签名/降级二元组/调用方告警文案（r1-S8）符合 §5.4 | ✓ |
| Spec 符合性 | FR-45 索引主链路：SessionEntry 八字段（index.py:11~21）、原子写 tmp+replace（index.py:305~315）、损坏 rebuild 重建（load 返回 corrupted → upsert/touch/all_sessions 三处触发，index.py:170~172/200~202/247~249）、find_by_prefix 四态消解前三态正确（精确/多命中候选/未命中，test_sessions_index.py:81~94）、find_by_name 搜索、find_by_id 复用——除 B1 告警通道与 S7 提示口径外符合 §3.2/§3.3 | ✓ |
| Spec 符合性 | FR-46 自动命名：derive_name 前 20 字符 + /skill 包装行跳过（index.py:36~45，r2 评审已认可顺延行命中技能标签行为合规；test_sessions_index.py:172~177 三断言）、auto_name 不覆盖手动名（touch 语义 + test_sessions_index.py:66~70）——除 S4 时点偏差登记外符合 §3.3 | ✓ |
| Spec 符合性 | FR-47 /sessions 列表与搜索：默认项目视图（workspace 过滤 + updated_at 倒序）、四态消解顺序正确（精确 stem → 前缀唯一 → 多命中候选 → 名称搜索，commands.py:637~665）、多命中候选呈现（prefix_candidates）、跨项目切换绝对路径 load + workspace 更新 + usage 恢复（_restore_session_usage r2-S10 口径，commands.py:577~580）——除 S2 聚组、S5 兜底外符合 §4.1/§5.2 | ✓ |
| Spec 符合性 | FR-48 /fork 另存为语义：新 session_id 替换 meta（commands.py:705~750）、源文件不存在兜底（712~714）、meta JSON 损坏兜底（724~726）、副本 upsert 登记与完成后 note——除 B2 IO 兜底缺口外流程符合 §5.3 | ✓ |
| Spec 符合性 | FR-49 /stats：角色分布计数、决策分布仅统计含 decision 字段行（r2-S11 过滤口径，stats.py:29~52）、无 agent 归「未标注」桶（UNKNOWN_AGENT）、全局聚合（会话数/消息数/token）、两卡呈现——test_sessions_commands.py:236~244 冒烟通过 | ✓ |
| Spec 符合性 | FR-50 切换保护（r1-B1 生命周期）：turn_active 置位在 loop.run 之前（cli.py:1618）、finally 复位（cli.py:1632）、begin_turn 不触碰（commands.py:296）、_switch_blocked 拦截 + 完成后切换排空（cli.py:1619~1674），test_sessions_commands.py 覆盖拦截/排空/切换成功三路径 | ✓ |
| Spec 符合性 | FR-51 迁移：migrate_legacy_sessions 启动时执行（cli.py:1497 前后装配）、agents/ 不动（决策 6，test_sessions_index.py:131~142）、同名冲突跳过仍 upsert、幂等（二次运行空日志，test_sessions_index.py:144~145）、无旧目录静默——除 S3 计数口径外符合 §5.1 | ✓ |
| Spec 符合性 | 命令登记与接线：COMMAND_META 四新条（commands.py:57~60）、_COMMAND_USAGE（77~79）、handle_command 分派（commands.py:309~310/666/687/709）、SLASH_COMMANDS（cli.py:109~113）、ARG_COMPLETIONS（cli.py:118）与补全 session 分支、make_prompt_session 签名扩展——与既有 /clear /resume 风格一致 | ✓ |
| Spec 符合性 | 前轮评审遗留项落地核对：r1-B3/r2-B4 五处收敛（见 FR-44 行）、r1-S2 多命中候选、r1-S4 重建派生名、r2-S10 token 恢复口径、r2-S11 decision 过滤、r1-S8 degraded 降级（paths.py:51~62 + 调用方告警 cli.py:1499/commands.py:281）均已在代码中落地 | ✓ |
| 逻辑正确性 | imports 无环：sessions 包不 import commands/cli（index 对 paths 的反向引用延迟到函数内，paths.py:83~84），commands/cli 单向依赖 sessions——静态审读 + 全量测试通过佐证 | ✓ |
| 逻辑正确性 | 轮末 finally 三路径（正常/异常/中断）均复位 turn_active 并累加 usage + touch（cli.py:1632~1643），usage 口径 prompt+completion 单调不减（r2-S10）；D8 闭包不捕获实例原则保持 | ✓ |
| 一致性 | 运行验证：WSL ~/miniconda3/envs/glaucous/bin/python -m pytest tests/ -q = 232 passed（基线 212 + 20 新增）3.72s；import glaucous.cli 冒烟通过；无范围蔓延（git status 与声明范围一致，TODO.md 为 spec 声明的债务登记） | ✓ |
## 四、复审要求

修复以下阻塞项后进行第 2 轮聚焦复审（范围：B1/B2 改动文件及其波及面）：

1. **B1**（必须）：SessionIndex 建立告警通道，索引写入失败时 renderer 告警一条（spec §3.2/§八「尽力而为：renderer 告警一条，对话继续」）；可与 S7 的「索引已重建」提示一并实现。
2. **B2**（必须）：_cmd_fork 读源/写新段按 §八「IO 失败 → 报错保持原会话」兜底（含空文件 IndexError 路径），消除异常击穿 REPL。

建议项 S1~S8 不作为本轮放行前置；其中 S3（迁移计数虚报）、S5（切换 load 无兜底）建议与阻塞项顺带处理，S4/S8 待 spec 作者确认后闭环。