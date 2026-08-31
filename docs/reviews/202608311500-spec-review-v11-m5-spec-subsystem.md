# Spec 一致性评审报告：V1.1-M5 Spec 子系统 Spec（状态机 / 澄清起草 / 双评审循环 / 执行管线 / 命令面）

> 评审日期：2026-08-31
> 评审对象：`docs/designs/202608311500-plan-v11-m5-spec-subsystem.md`（状态：草稿）
> 对照文档：`docs/编程智能体需求文档v1.1.md`、`docs/编程智能体概要设计说明书v1.1.md`、`docs/Glaucous开发计划表v1.1.md`
> 评审模式：首轮全量评审（含既有代码依赖实证核对）
> 结论：**不通过**（阻塞 1 项，建议 12 项）

## 一、评审范围

「关联规格」声明范围：需求文档 §2.4（FR-52~59）、§4（裁剪底线「Spec 双评审循环不可裁」）；概设 §7（Spec 子系统）、§9（模块清单：spec/ 包）；开发计划表 V1.1-M5 任务 5.1~5.7 与验收标准（场景 G）。三个相对链接（`../编程智能体需求文档v1.1.md`、`../编程智能体概要设计说明书v1.1.md`、`../Glaucous开发计划表v1.1.md`）经解析均实际可达。

实际触及内容还包括：对既有代码的依赖假设（`agent/loop.py`、`agent/subagent.py`、`checkpoint/store.py`、`checkpoint/git_snapshots.py`、`cli.py`、`commands.py`、`tools/planning.py`、`tools/interactive.py`、`tools/base.py`、`safety/output_limit.py`、`ui/prompts.py`），本评审逐一实际读取核对；以及需求文档 §3 场景 G/场景 I、§6 验收场景、概设 §5/§8/§10/§11 中被 spec 引用的机制。

### 代码依赖假设核对结果（评审要求第 2 点）

| spec 依赖假设 | 实际代码 | 结论 |
|---|---|---|
| `SubagentRunner.run` 签名与 metadata 键（决策 6/7） | `run(task, context="") -> ToolResult`（subagent.py L151）；成功路径 metadata = `sub_agent`/`session_file`/`modified_files`（L235-243），空任务失败路径 `sub_agent=None`（L155-159） | ✓ 吻合 |
| 报告 1000 字截断与归档路径（决策 6） | `REPORT_MAX_CHARS = 1000`（L42）；超限落盘 `outputs_dir / f"{sanitize_call_id(call_id)}.log"`，`call_id=f"spawn_agent-{agent_id}"`（L99-112、L231）；`sanitize_call_id` 对 `spawn_agent-child-N` 原样保留（output_limit.py L22-27） | ✓ 吻合，`.glaucous/outputs/spawn_agent-<agent_id>.log` 回读路径正确 |
| 每任务一次 `loop.run` = 每任务一个 checkpoint（决策 2） | `AgentLoop.run` 入口创建 checkpoint 并回调 `on_checkpoint`（loop.py L86-106）；主 loop 注入 `checkpoint_store` 且 `on_checkpoint=lambda cp: setattr(ctx,"turn_checkpoint_seq",cp.seq)`（cli.py L1207-1216）；子 agent loop 构造时**未**注入 `checkpoint_store`（subagent.py L201-211）→ 评审子任务不产生子 checkpoint | ✓ 真实接线成立（但见 B1：该「免费」接线在全 mock 测试中不可观测） |
| `store.create` 签名（决策 3） | `create(task: str, message_count: int, anchor_digest: str = "") -> Checkpoint \| None`（store.py L148） | ✓ 吻合 |
| `diff_against` 返回值形态（§4.5） | `diff_against(root, ref, excludes) -> list[dict]`，元素为 `{status: M/D/A, path}`（git_snapshots.py L130-164）；`store.preview_changes(cp)` 为同口径封装（store.py L244-249） | ✓ 三态口径吻合（调用方式建议见 S11） |
| `rebuild_loop` 挂账点可行（决策 7） | `SubagentRunner` 在 `rebuild_loop → build_registry` 内装配（cli.py L1039-1054）；`rebuild_loop` 是启动/`/clear`/`/resume`/`/fork`/`/sessions` 切换/`/context`/`/rollback`（上下文截断）的共用重建入口（cli.py L1186、commands.py 多处）；`_agent_seq` 为类级自增（subagent.py L119），重建后编号不冲突 | ✓ 可行 |
| `make_ask_callback` 形态（hooks.ask 装配来源） | 返回**同步**函数 `ask(question, options) -> str \| None`，EOF/Ctrl+C 返回 None（cli.py L264-307）；`AskCallback = Callable[[str, list[str]], str \| None]`（interactive.py L23） | ⚠ 与 §3.3 声明的 `Awaitable` 不符（见 S2）；None 语义缺失见 S4 |
| 命令面接线的相容性（§3.5） | `handle_command` 以 `partition(" ")` 分派，新增 `/spec`、`/specs` 无结构障碍（commands.py L1047-1102）；`ReplContext` 现有字段满足需要、`subagent_runner` 为新增字段（commands.py L115-188）；`/clear`/`/resume` 均走 `rebuild_loop`（L286-336） | ✓ 相容（补全数据源注意见 S12） |
| `read_spec` 同款 `read_plan` 机制（§3.4） | `ReadPlanTool`：缺省读最新（`glob("*.md")` 取末位）、可选 `plan_id`（planning.py L172-186）；`Tool` 基类默认 `risk=Risk.SAFE`、`modes=ALL_MODES`（base.py L66-67）；子 registry 由 `build_sub_registry` 自父派生（subagent.py L57-68）→ 主 registry 单注册即子可用 | ✓ 机制表述成立（措辞见 S9，路径见 S3） |
| `BASE_PROMPT` 可增句（决策 10） | `BASE_PROMPT` 存在并经 `build_system_prompt` 注入（ui/prompts.py L21、L94-102） | ✓ 可行 |
| 代码库无 `todo_write`（决策 4 事实依据） | 全仓 grep：`src/` 零命中；仅文档命中（概设 v1.1 §7.4 表述句 + 概设 v1.0 遗留术语 3 处）；现行 `tools/interactive.py` 仅实现 `ask_user` | ✓ 属实 |

### §六「与既有机制的边界核对」逐条验证（评审要求第 3 点）

| # | 断言 | 验证结论 |
|---|---|---|
| 1 | 父上下文隔离（FR-61/64 不变） | **基本属实且隔离更强**：pipeline 直调 `runner.run` 不经 `spawn_agent` 工具 dispatch，评审报告不以工具结果入父史（不违反 FR-61，隔离方向安全）；但「父史只增任务消息与工具结果」表述不精确——修订轮经合成消息把回读全文喂入父史（§六.1 自述），且并无「工具结果」入史（见 S10） |
| 2 | `turn_active`/`turn_checkpoint_seq` 在 pipeline 直调 `loop.run` 下的行为 | **属实**：`turn_active` 仅在 repl 任务轮壳置复（cli.py L1680/L1697），pipeline 经 `handle_command` 路径不进入任务轮壳 → 保持 False；REPL 串行 `await handle_command`，无并发命令窗口；`turn_checkpoint_seq` 由 `loop.run` 内 `on_checkpoint` 写入（cli.py L1215），命令路径无轮末清理但不构成错误消费（审批只发生在 `loop.run` dispatch 内） |
| 3 | 审批「拒绝并回退」消费任务入口 checkpoint | **属实**：`rollback_ready = active_agent=="主 agent" and turn_checkpoint_seq is not None`（cli.py L377-379）；`_reject_with_rollback` 回退至该 seq（L326-342）；任务轮内该 seq 恰为任务入口快照 |
| 4 | 深度介入仅评审轮生效 | 与 FR-55 字面一致，spec 内部自洽 ✓ |
| 5 | pipeline 运行期间无其他命令输入 | **属实**：REPL 单循环串行 ✓ |

### 决策 4（`todo_write` 联动收窄）专项评估（评审要求第 1 点）

**结论：登记与论证充分，判定合规，不记录问题。**依据：
1. **显式声明**：决策 4 标题即「对概设 §7.4 的显式修正」，§八再次登记（非静默偏离）；
2. **事实依据经核实**：`src/` 全仓无 `todo_write`（见上表），该词仅为概设 v1.0 遗留术语；
3. **与上游定义句一致**：概设 §7.4 原句为「任务清单与 todo_write 联动：**任务进度勾选直接写回 Spec 文档 checkbox（状态即文档）**」——冒号后的定义性表述与收窄后的语义完全一致；开发计划表任务 5.4 字面亦为「任务清单与 todo 联动**勾选回写**」；
4. **需求语义无损**：FR-56「任务进度可见」由文档 checkbox + `/spec status` 卡 + 每任务抬头行三通道承担（§一/决策 4），且该收窄不触碰需求 §4 裁剪底线（双评审循环完整保留）。

## 二、阻塞问题

### B1. 每任务 checkpoint 的「免费接线」（决策 2）与测试计划的「checkpoint hook 计数断言」（§七）互斥，实现口径无法判定

- **维度**：结构与可执行性（内部自洽）；波及维度二的概设 §5.2/§7.4 任务级 checkpoint 机制与计划表任务 5.7 验收
- **spec 位置（三处互相矛盾）**：
  - 决策 2：「执行阶段每任务恰为一次 `loop.run` → **每任务一个 checkpoint（FR-56）由既有接线免费获得**」；§一架构图「逐任务：loop.run(任务指令+Spec 锚)（免费获得任务入口 checkpoint，决策 2）」——即 pipeline 内唯一的显式 checkpoint 是决策 3 的执行入口（`hooks.checkpoint` = `store.create(...).seq`，§3.3）；
  - §七测试 6：「全流程回放：……执行（**2 任务勾选+checkpoint 两次**）」；
  - §七验收映射：「每任务 checkpoint（6，**checkpoint hook 计数断言**）」。
- **上游位置**：开发计划表任务 5.7「状态机流转/双评审循环回放/轮次耗尽升级/**任务执行联动**」与 M5 验收「执行中**每任务有 checkpoint 可回退**」；概设 §5.2「Spec 任务清单额外每任务一个（复用同机制）」。
- **冲突说明**：§七为「PipelineHooks 全 fake」回放测试——`run_turn` 被脚本化替身替代，真实 `loop.run` 不执行，决策 2 的「免费」任务级 checkpoint（loop.py L86-106 的 run 入口接线）**在测试中完全不可观测**；而按决策 2/3 的设计，`hooks.checkpoint` 在管线内仅于执行入口被调用 1 次，「2 任务 → checkpoint 两次」与「每任务 checkpoint 的 hook 计数断言」均无对应可观测。两条出路互相矛盾：若 pipeline 改为每任务显式调 `hooks.checkpoint`（测试方可计数），则真实接线中每任务产生两个 checkpoint（显式 + `loop.run` 入口），与决策 2「零改动免费获得」的叙述冲突，且决策 2/§一未作此声明；若维持决策 2，则 §七断言无法成立，任务 5.7 的「任务执行联动」验收落空。此为影响实现判断（`_execute` 是否显式建任务级 checkpoint）与验收可测性的内部矛盾。**存疑，提请作者确认口径。**
- **修复方向**（二选一并全文对齐）：① 改为 pipeline 每任务经 `hooks.checkpoint` 显式创建任务级快照（概设 §7.4「每任务开始前打任务级 checkpoint」的字面实现），修订决策 2 表述（`loop.run` 免费接线降级为 `turn_checkpoint_seq` 的来源与兜底），测试断言改为入口 1 + 每任务 N 的计数；② 维持「免费接线」，则 §七需替换观测点（如为可注入的真实 `CheckpointStore` 替身接线、或断言每任务轮入口的 seq 单调递增），删除现有 hook 计数表述，并说明 mock 层如何证明「可回退」。

## 三、建议问题

### S1. 头部概设章节引用错误：「§9（模块清单：spec/ 包）」应为 §11

- **维度**：概设一致性（引用正确性）
- **位置与摘录**：spec 头部「[编程智能体概要设计说明书v1.1.md] §7（…）、**§9（模块清单：spec/ 包）**」；概设 v1.1 中 §9 为「CLI 交互与视觉增补」，`spec/` 包清单位于「**§11 工程结构增补**」（`spec/store.py`、`templates.py`、`pipeline.py`、`tools.py`）。
- **建议**：将头部引用更正为 §11（§9 若指卡片/命令面可保留并注明），避免按引用回溯时落空。

### S2. `hooks.ask` 声明为 Awaitable，与装配来源的同步签名不符

- **维度**：结构与可执行性（内部自洽 / 与既有代码相容性）
- **位置与摘录**：spec §3.3「ask: Callable[[str, list[str]], **Awaitable[str | None]]」；既有代码 `AskCallback = Callable[[str, list[str]], str | None]`（interactive.py L23），`make_ask_callback` 返回同步函数（cli.py L272）。
- **建议**：将 `hooks.ask` 改为同步签名（与现状一致，pipeline 内直接调用不产生事件循环问题——既有 ask_user 在 dispatch 内亦同步阻塞），或显式声明适配层（如 `asyncio.to_thread` 包装）；二者择一写明，消除实现歧义。

### S3. `read_spec` 落位 `tools/spec_tool.py` 偏离概设 §11 规定的 `spec/tools.py`，未声明

- **维度**：概设一致性（架构与分层 / 工程结构）
- **位置与摘录**：spec §一模块表「工具层 | `tools/spec_tool.py`（新增）| ReadSpecTool」；概设 §11 工程结构「`spec/ …` ├── `tools.py  # read_spec 工具 + Spec 锚注入`」。
- **建议**：落位 `tools/` 与既有代码惯例（`tools/planning.py` 的 `read_plan`）一致，是合理取舍，但需按「路径不一致需有声明」补一句偏离说明；或改按概设置于 `spec/tools.py`。

### S4. §五错误处理遗漏「确定性门 ask 返回 None（用户未响应）」场景

- **维度**：结构与可执行性（错误处理完备性）
- **位置与摘录**：§五场景表未含该项；既有 `make_ask_callback` 对 EOF/Ctrl+C 返回 **None 而非抛异常**（cli.py L295-297），`AskUserTool` 同款契约（interactive.py L62-67）。pipeline 的澄清门/批准卡/耗尽升级卡/任务失败三选均为强制决策点，None 到达时行为未定义；且 Ctrl+C 在 ask 卡内被吞为 None，决策 12 的顶层 `KeyboardInterrupt` 捕获不会触发。
- **建议**：在 §五补一行：ask 返回 None 时按决策 9「绝不静默继续或静默终止」口径处理（如状态停驻 + 恢复提示，或视同取消归档并显式告知），逐门统一。

### S5. 轮次上限硬编码 3，未引用概设 §10 配置项 `[review].max_rounds`，亦未声明固定

- **维度**：概设一致性（核心机制 / 配置）
- **位置与摘录**：决策 9「澄清访谈 ≤3 轮、批准反馈修订 ≤3 轮、Spec 评审 ≤3 轮、代码评审 ≤3 轮」；概设 §10「`[review] max_rounds = 3  # Spec 评审与代码评审共用上限`」。
- **建议**：声明 Spec 评审/代码评审两上限读取 `config`（概设 §10 口径），或显式声明本轮固定字面量 3 及理由；澄清/批准修订两个新预算可保留字面量并注明。

### S6. 轮次耗尽升级的测试仅覆盖 Spec 评审一处

- **维度**：结构与可执行性（测试覆盖）
- **位置与摘录**：§七测试 8「评审 3 轮耗尽 → ask 选项呈现且『再修订仅一次』」；而决策 9 定义了四类耗尽点（澄清 ≤3、批准反馈修订 ≤3、Spec 评审 ≤3、代码评审 ≤3），计划表 5.7 字面要求为泛化的「轮次耗尽升级」。
- **建议**：至少为批准反馈修订与代码评审各补一条耗尽升级回放用例（澄清耗尽可与现有门用例合并），保证四类确定性门的升级选项均有回归。

### S7. 评审任务提示词未写入「不得 ask_user」约束（概设 §8.3 要求）

- **维度**：概设一致性（核心机制）
- **位置与摘录**：概设 §8.3「评审子 agent 不应 ask_user，其输入已含全部材料——**检查清单约束写入评审员角色提示词**」；spec §3.2（REVIEW_CONTRACT/两套清单）与 §4.3/§4.5 的评审输入材料均未提及该约束。子 registry 含 `ask_user`（父集去 `spawn_agent`），无提示词约束时评审员可能发起提问。
- **建议**：在评审/验收核验任务提示模板中显式写入「仅依据所给材料裁决，不得调用 ask_user」条款，并在 §3.2 登记。

### S8. 执行入口 checkpoint 在超长任务清单下可能被保留淘汰，diff 基线失效风险未登记

- **维度**：结构与可执行性（风险完备性）
- **位置与摘录**：决策 3/§4.5 以 `entry_checkpoint` 为代码评审 `diff_against` 基线；`CheckpointStore._evict` 对超过 `max_keep`（默认 50）者删除最旧 ref（store.py L215-228）。每任务一次 `loop.run` 各产生一个 checkpoint，任务清单 + 评审修订轮累计超 50 时入口 ref 可能被淘汰。
- **建议**：在 §八风险表登记该边界，并给出降级口径（如基线 ref 失效 → 按决策 8 同款「无 diff」注明），或声明接受该边界（实际清单极少超 50 任务）。

### S9. §3.4「主/子 registry 均注册」表述与接线事实不符

- **维度**：结构与可执行性（术语与表述）
- **位置与摘录**：§3.4「SAFE 风险、全模式、**主/子 registry 均注册**」；§一接线表为「`build_registry` 注册 ReadSpecTool」（单注册）。实际机制：子 registry 由 `build_sub_registry` 自父派生（subagent.py L57-68），主 registry 单注册即子 agent 可用。
- **建议**：改为「主 registry 注册，子 registry 经派生自动可用」，与接线表一致。

### S10. §六.1「父史只增任务消息与工具结果」表述不精确

- **维度**：结构与可执行性（术语与表述）
- **位置与摘录**：§六.1「父史只增任务消息与工具结果」；实际：评审/验收经 `runner.run` 直调，不经 `spawn_agent` dispatch，**不存在**报告工具结果入史（隔离强于该表述）；同时决策 6 回读的**全文**经修订轮合成消息进入父史（§六.1 后半句自述）。
- **建议**：修正为「父史只增各阶段合成任务消息与其终答；评审报告以卡片呈现、经子会话留痕，修订轮按需注入全文」，与决策 6 口径对齐。

### S11. §4.5 直调 `git_snapshots.diff_against` 缺少 root/excludes 要素，建议改用 `store.preview_changes`

- **维度**：结构与可执行性（接口定义完备性）
- **位置与摘录**：§4.5「diff 基线 = `entry_checkpoint` ref（`diff_against`，M/D/A 三态）」；实际签名 `diff_against(root, ref, excludes)` 需要仓库根与排除集（git_snapshots.py L130），`store._root` 为私有；`CheckpointStore.preview_changes(cp)` 已提供同口径封装（store.py L244-249），且入口 seq → `Checkpoint` 尚需 `store.get(seq)` 一步。
- **建议**：§4.5 改为「`store.get(entry_checkpoint)` → `store.preview_changes(cp)`」，或写明 root/excludes 的获取方式。

### S12. 补全数据源表述：命令补全实际由 `COMMAND_META` 驱动，`SLASH_COMMANDS` 已无引用

- **维度**：结构与可执行性（与既有代码相容性）
- **位置与摘录**：§一接线表「SLASH_COMMANDS/ARG_COMPLETIONS 增 /spec /specs」；实际命令段补全遍历 `COMMAND_META`（cli.py L1362-1372，注释「单一数据源」），`SLASH_COMMANDS` 定义后无任何引用（全仓 grep 仅 1 处定义命中）；`/spec` 参数补全（`status`/`cancel`）需在 `ARG_COMPLETIONS` 新增 kind 并在 `make_repl_completer` 增加分支。
- **建议**：接线表改为「COMMAND_META/_COMMAND_USAGE/ARG_COMPLETIONS（新增 spec 子命令 kind）+ handle_command 分派」，删除或注明 `SLASH_COMMANDS` 为遗留清单。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 硬约束符合性（需求 §5 约束与合规：无框架依赖、本机执行、Git 仅子进程调用等） | ✓（全部经既有机制复用，无新依赖引入） |
| 需求一致性 | 时间可行性（计划表 M5 = 2~3d，9/8~9/10；v1.1 为赛后演进排期，不触碰 9/2 提交硬约束） | ✓ |
| 需求一致性 | 任务 5.1~5.7 全量落实且映射 FR-52~59（5.1→§3.1/§3.2·FR-54；5.2→§4.1/§4.2·FR-53；5.3→§4.3·FR-55；5.4→§4.4/§4.6·FR-56；5.5→§4.5·FR-57/58；5.6→§3.5·FR-59；5.7→§七） | ✓ |
| 需求一致性 | 无范围蔓延（验收核验子 agent 轮属 FR-58/概设 §7.3 落实；BASE_PROMPT 建议句属 FR-52 字面；未引入新需求） | ✓ |
| 需求一致性 | 裁剪底线「Spec 双评审循环不可裁」：§八 声明无裁剪，双评审循环完整（≤3 轮 + 耗尽升级） | ✓ |
| 需求一致性 | 决策 4（`todo_write` 联动收窄）：显式声明 + 事实依据经 grep 实证 + 与概设 §7.4 定义句及计划表 5.4 字面一致 + 进度可见性三通道兜底 → 判定合规 | ✓ |
| 需求一致性 | 典型场景支持：场景 G 全流程有对应数据流（§一架构图 + §四）；场景 I 跨会话续接由 frontmatter 落盘 + `/spec` 无参续跑支撑（决策 12/§3.5） | ✓ |
| 概设一致性 | 状态机与概设 §7.1 一致（六态 + archived，修订回环不迁移状态，§2.2 迁移表强校验） | ✓ |
| 概设一致性 | 文档结构与概设 §7.2 / FR-54 一致（七节 + 结构化验收标准行） | ✓ |
| 概设一致性 | 双评审循环与概设 §7.3 一致（检查清单两套逐条对应、深度介入每轮收建议、耗尽升级不静默） | ✓ |
| 概设一致性 | 命令面与概设 §7.5 一致（`/spec` 无参显示进度、`/specs`、`/spec status`、`/spec cancel`） | ✓ |
| 概设一致性 | 决策 6 截断回读与 M2 机制吻合（`REPORT_MAX_CHARS=1000`、`outputs/spawn_agent-<agent_id>.log`、`metadata.sub_agent`、不改 `build_report`） | ✓ |
| 概设一致性 | 决策 3 入口基线与概设 §7.3「自任务入口 checkpoint」语义对齐；`store.create` 签名与代码吻合 | ✓ |
| 概设一致性 | 决策 7 挂账经 `rebuild_loop` 共用入口，/clear、/resume 等全部重建路径有效（D8 口径），与 subagent.py 归属切换机制相容 | ✓ |
| 概设一致性 | §六.2 边界断言经代码实证成立（`turn_active` 置复位置、`turn_checkpoint_seq` 写入与消费链路、无并发命令窗口）；§六.3 审批拒绝回退语义与概设 §7.4 一致 | ✓ |
| 结构与可执行性 | 头部要素齐备（创建日期/关联规格含章节任务号/状态草稿），三个相对链接全部可达 | ✓ |
| 结构与可执行性 | 内容完备：总体设计/分层影响/数据模型/接口定义/错误处理/测试验证/裁剪声明八节齐备，无「待定/TODO」未决项 | ✓ |
| 结构与可执行性 | 决策记录集中（12 条决策显式登记取舍与理由），偏离均有声明（决策 4/8/10/12） | ✓ |
| 结构与可执行性 | 测试计划覆盖任务 5.7 四项中的三项：状态机流转（用例 1-5）、双评审循环回放（用例 6/7）、任务执行联动主干（用例 6/10，checkpoint 断言部分见 B1） | ✓（轮次耗尽升级为部分覆盖，见 S6） |

## 五、复审要求

**结论：不通过**（阻塞 1 项，建议 12 项）。

必须修复的阻塞项：**B1**（每任务 checkpoint 的「免费接线」与 §七「checkpoint hook 计数断言」互斥）——需作者二选一定案（显式每任务建快照并修订决策 2，或维持免费接线并更换测试观测点），同步修订决策 2/§一架构图/§七测试 6/验收映射的表述使其一致。

建议项（S1~S12）登记为待办：其中 S2/S4 涉及交互契约语义，建议随 B1 一并修订；S1/S3/S5/S9/S10/S11/S12 为引用与表述对齐，可在同轮修订中低成本完成；S6/S7/S8 建议在本里程碑内落实（S7 为概设 §8.3 的字面要求）。修复后发起聚焦复审（改动章节预期：决策记录、§一、§3.3、§3.4、§4.5、§五、§六、§七、§八）。
