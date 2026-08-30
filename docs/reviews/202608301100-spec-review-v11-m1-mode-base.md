# Spec 一致性评审报告：V1.1-M1 模式基座 Spec（默认 Build + 底线守卫默认 + 高风险确认 + /plan 研究模式）

> 评审日期：2026-08-30 11:00
> 评审对象：`docs/designs/202608301000-plan-v11-m1-mode-base.md`（首轮全量评审）
> 对照文档：编程智能体需求文档v1.1（§2.1/§3/§4）、编程智能体概要设计说明书v1.1（§4.1/§4.2/§4.3/§9/§10/§11/§13）、Glaucous开发计划表v1.1（V1.1-M1 任务 1.1~1.5 与验收标准、风险预案）
> 代码基线：`src/glaucous/`（modes.py / loop.py / approval.py / planning.py / base.py / commands.py / cli.py / prompts.py / renderer.py / config.py / context/history.py）与 `tests/`（13 个测试文件逐点核对）
> 结论：**不通过**（阻塞 2 项，建议 9 项）

## 一、评审范围

Spec 头部声明上游依据：需求文档v1.1 §2.1（FR-35~39）、§3 场景 F/J、§4（裁剪原则）；概设v1.1 §4.1~§4.3、§9、§10、§11；开发计划表v1.1 V1.1-M1 任务 1.1~1.5 与验收标准。三份相对链接均实际可达（文件存在）。

实际评审范围 = 声明范围 + 文档实际触及内容：模式状态机重构（默认值翻转、回归 Plan 退役、submit_plan 二选重造、授权策略生命周期）、/build per-action 参数面与命令面登记、CLI 交互改造（prompt_plan_decision / confirm 闭包 / 补全器）、BASE_PROMPT 增补、测试修订与新增、错误处理表。另按任务要求核对 fork 裁剪（FR-48）划归 M3 的正确性、范围守恒（M2~M6 无蔓延）、既有测试对 SessionState 默认值的真实依赖面、spec 引用的全部代码行号/函数名/文案的真实性。

## 二、阻塞问题

### B1. 「mode_changed 事件自本批起无发射点」与 loop.py 现存第二个发射点矛盾
- **维度**：概设一致性（偏离声明不实）+ 结构与可执行性（内部矛盾影响实现判断）
- **spec 位置**：§2.2 「`mode_changed` 事件自本批起无发射点（§〇 裁剪表第 2 行）」；§〇 裁剪表第 2 行「renderer 的 mode_changed 分支保留为兼容死分支（不删）」；§2.2 「（mode_snapshot 变量如无其他引用则一并清理）」；§八 loop.py 行「删自然终止回归 Plan 分支（L105~116）」
- **代码事实**：`agent/loop.py` L145~155 存在第二个 mode_changed 发射点，且其注释明确依赖 submit_plan 切换路径：
  > L145 「# submit_plan 三选一①②在 dispatch 内改 state——比对快照统一 emit / mode_changed（Day2 Plan §4.5 统一出口，自然终止回归在上方分支 emit）」
  > L147~155 `if self._state.mode != mode_snapshot: self._emit("mode_changed", {...})`

  v1.1 中 PLAN 下 submit_plan 批准仍发生在 dispatch 调用栈内（`planning.py` L129 `decision = self._confirm(plan)` → `cli.py` L864~901 confirm 闭包 `ctx.state.enter_build()` → `loop.py` L127 dispatch 返回后），L147 比对 `state.mode != mode_snapshot` 为真 → **仍会发射 mode_changed**（payload：mode=build、policy=维持值、reason=「用户确认方案，已切换 build 模式」）。另 `mode_snapshot` 在 L97（`tool_schemas(mode_snapshot)`）、L127（`dispatch(call, mode_snapshot)`）、L147（比对）均有引用，「如无其他引用则一并清理」的前提不成立。
- **冲突说明**：spec 宣称「无发射点」「renderer mode_changed 分支为死分支」，与代码事实及 v1.1 实际行为直接矛盾。实现者面临两种走法：保留 L145~155（PLAN 下批准仍发射，mode_changed 分支非死分支，裁剪表表述失实）或按 spec 声明删除（PLAN 下批准的模式切换反馈消失，提示符需等下一轮输入才更新）。两种走法行为不同，spec 未裁决，直接影响实现与测试断言（§7.2 未含 mode_changed 任何断言）。
- **修复方向**：在 §2.2 显式处置 L145~155 统一出口——若保留：修正「无发射点/死分支」表述，声明 PLAN 下批准的模式切换反馈由该事件承担，§7.2 补对应断言；若删除：声明切换反馈改由何种通道（如 execute 回喂文案）呈现，并同步修订 §〇 裁剪表第 2 行。同时修正 mode_snapshot 清理的条件句（明确其因 L97/L127 引用而保留）。

### B2. 「锚行后附『已进入 Build 模式』」无实现契约支撑
- **维度**：结构与可执行性（接口定义不完备，声明的行为不可实施；存疑，提请作者确认）
- **spec 位置**：§4.2 「批准：`用户已批准方案，请按方案执行。`+（若执行前 mode==PLAN，由 CLI 闭包切 Build 并在锚行后附「已进入 Build 模式」）+ 锚行」
- **代码事实**：
  - `tools/planning.py` L87 `SubmitPlanTool.__init__(self, confirm, plans_dir)`——工具不持 SessionState；L40 `ConfirmCallback = Callable[[str], PlanDecision]`——闭包只能返回 PlanDecision；
  - 回喂文本由 `execute` 内 `_reply`（L131~134）组装（文案 + 锚行），confirm 闭包（cli.py L864~901）切状态后**无法向 execute 的回喂文本注入内容**；
  - §4.1 定义的二选 PlanDecision 仅含 `choice`/`feedback` 字段，无承载「已切换 Build」信息的字段；
  - §八 将 renderer 列为（隐含）零改动面、cli.py 改动仅「prompt_plan_decision / confirm 二选化、状态切换收敛」，无渲染层附加路径。
- **冲突说明**：spec 声明的「在锚行后附『已进入 Build 模式』」在现有 ConfirmCallback 契约、SubmitPlanTool 构造、PlanDecision 结构下均无可落地路径；spec 未定义任何扩展点。实现者无法判断应改 PlanDecision 结构、改回调契约还是改渲染层——行为已声明但机制缺失，接口定义不完备。
- **修复方向**：三选一并显式定义：a) PlanDecision 增加切换标记字段（闭包置位，execute 据此拼接附言）；b) 调整 ConfirmCallback 返回契约；c) 从 execute 回喂文案中移除该附言，改由 confirm 闭包内直接呈现切换反馈（如复用/代替 mode_changed 事件行）。任选其一后同步 §4.1/§4.3/§八 与 §7.2 断言。

## 三、建议问题

### S1. 概设章节头部引用标注错位
- **维度**：概设一致性（引用正确性）
- **位置与摘录**：spec 头部「§10（工程结构）、§11（测试增补）」；概设v1.1 实际：§10 =「## 10. 配置与安全增补」，§11 =「## 11. 工程结构增补」（测试增补清单 `test_mode_default_build.py` 等仅为 §11 末段）。spec 正文引用（§4.1/§4.2/§4.3/§11 测试清单/§13 风险表）均正确，仅头部标注错位一档。
- **建议**：修正头部标注（工程结构=§11；测试增补清单在 §11 末段），避免后续追溯查错章节。

### S2. 授权策略配置面裁剪的偿还去向未指向计划表具体任务号
- **维度**：需求一致性（裁剪偿还指向模糊）
- **位置与摘录**：spec §〇 裁剪表第 1 行偿还去向「TODO.md 登记；M3 会话管理完成后随会话持久化一并评估」；计划表v1.1 V1.1-M3 任务 3.1~3.6 无对应评估项；且计划表任务 1.1 明确含「modes.py 默认模式/策略翻转 + **config 默认值** + 状态栏/提示符适配」，概设 §4.1 亦写「per-action 保留为用户可选（/build per-action **或 config**）」。裁剪本身显式合规，但偿还路径无任务号可依。
- **建议**：偿还去向落到计划表具体任务号（或在计划表 M3 增补评估项），并注明对任务 1.1「config 默认值」子项的裁剪对应关系。

### S3. /build 补全候选含非法参数 + 策略回切无通道未声明
- **维度**：结构与可执行性（接口语义张力）
- **位置与摘录**：spec §3.2 「其他非空参数 → 报错提示用法（`用法：/build [per-action]`），**不改状态**」；§3.3 「候选 `["auto-approve", "per-action"]`……候选与 /build 实际语义差异……以 meta 文字说明即可」。候选 auto-approve 被选中（F1 两段式 Enter 会将候选文本落入输入行）后提交必触发报错；且策略生命周期为单向——per-action 无会话内回切 auto-approve 的任何通道（仅重启/新会话恢复默认），spec 未声明该限制。
- **建议**：二选一：将 auto-approve 纳入 /build 合法参数（提供显式回切）并同步 §3.2/§六/§7.2；或补全候选只留 per-action，并在 §3.2/§六 声明「per-action 会话内不可回切、重启恢复默认」的限制与缓解。

### S4. 「代码中无三选一残留引用」验收的清理清单不完整
- **维度**：需求一致性（计划表验收覆盖缺口）
- **位置与摘录**：计划表v1.1 M1 验收「代码中无三选一残留引用」。spec 改动清单未覆盖的现存残留：`renderer.py` L173~182 `plan_card` 三行三选一选项文案（全库无调用点的死方法）、`planning.py` L6/L25/L68 模块 docstring 与 SubmitPlanTool docstring 的「三选一」表述、`base.py` L57 注释「submit_plan 仅 plan」、`cli.py` L91 Banner 文案「Plan 只读探索，Build 写操作走审批」（与默认 auto-approve 行为不符）、`cli.py` L983 resume docstring「state 重置 plan/per-action」。
- **建议**：在 §八 或 §7.3 补充注释/docstring/死文案清理清单，或显式声明验收口径（如「可运行路径无三选一残留，注释级随改随清」），避免 M1 收尾验收时遗漏。

### S5. handle_command 的 /build 分派行未列入实现位置汇总
- **维度**：结构与可执行性（实现靶点遗漏）
- **位置与摘录**：spec §3.2 定义 `_cmd_build(ctx, arg: str = "")`；`commands.py` L496~497 现状 `if cmd == "/build": return await _cmd_build(ctx)`（不传 rest）；§八 commands.py 行仅列「_cmd_build(arg)、_cmd_plan 文案、COMMAND_META/_COMMAND_USAGE/HELP_LINES」。漏改分派行则 /build 参数恒为空。
- **建议**：§八 commands.py 行补「handle_command /build 分派传 rest」。

### S6. base.py 拦截 hint「另一分支」括注与代码分支语义错位
- **维度**：结构与可执行性（引用表述与代码事实不符）
- **位置与摘录**：spec §4.4 「另一分支（PLAN 下引用仅 BUILD 工具）文案同理对齐」；`base.py` L198 分支为「工具 modes=={BUILD} 且当前 PLAN」（与 spec 新文案对应 ✓），L203~204 else 分支实为「当前 Build、工具非 BUILD 专属（即仅 PLAN 可用）」的拦截；且 submit_plan 改 ALL_MODES 后 v1.1 无仅 PLAN 工具，该分支暂无触发工具。
- **建议**：括注修正为「Build 下引用仅 PLAN 工具」，并说明该分支在 v1.1 的触发面变化（保留为兜底）。

### S7. 补全 policy 分支缺测试声明
- **维度**：结构与可执行性（测试声明与条款对应缺口）
- **位置与摘录**：spec §3.3 定义 ARG_COMPLETIONS `/build: "policy"` 与 `kind == "policy"` 分支行为；§7.2 新增用例清单未含补全断言，§7.1 修订清单亦未列 `tests/test_repl_completer.py`（现有补全测试文件，13 文件中唯一覆盖 make_repl_completer 的用例集）。
- **建议**：§7.2 补一条 policy 补全用例（候选集合/前缀过滤/display_meta），或在 §7.1 列明 test_repl_completer.py 扩展。

### S8. 「toolbar_text 已按 mode+policy 双态渲染」表述与代码不符
- **维度**：概设一致性（引用表述轻微失实）
- **位置与摘录**：spec §2.3 「renderer._mode_badge / toolbar_text 已按 mode+policy 双态渲染（build·自动放行 / build·每次审批 / ◆ plan）」；`renderer.py` L117~128 `toolbar_text` 实际仅按 mode 渲染（`badge = "⬥ build" if mode == "build" else "◆ plan"`，无 policy 后缀），policy 双态仅存在于 `_mode_badge`（L90~96）。结论「无需改动」不受影响。
- **建议**：表述拆分（_mode_badge 双态 / toolbar_text 仅模式），避免实现者按错误表述写 toolbar 断言。

### S9. 「概设 §4.3 要点逐条」存在未注明的适配偏差
- **维度**：概设一致性（覆盖表述偏差）
- **位置与摘录**：spec §5.2 「先澄清后开发段（新，概设 §4.3 要点逐条）」之①「不得在需求未澄清时动手修改」；概设 §4.3 要点 3 原文「3. 禁止在需求未澄清时启动长流程（spec 起草/评审）」。改写与 FR-37 验收标准（「动手前必须明确需求边界……」）一致且有时序理由（M1 无 Spec 长流程可禁），但声称「逐条」而未注明差异。
- **建议**：在该段注明要点 3 的 M1 适配口径（长流程禁令随 M5 Spec 子系统落地），保持「逐条」声明与实际内容严格对应。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 需求 §4/§5 约束合规（无新依赖、凭据不入库、CLI 形态、Python 3.11+） | ✓ |
| 需求一致性 | FR-35~39 逐条覆盖（默认翻转/底线守卫+per-action/先澄清/高风险确认/Plan 研究） | ✓ |
| 需求一致性 | FR-37 评测集裁剪显式登记偿还（M6 任务 6.2）；fork 裁剪（FR-48 收窄）正确划归 M3、未混入本批 | ✓ |
| 需求一致性 | 范围守恒：checkpoint(M4)/会话管理(M3)/spawn_agent(M2)/Spec(M5) 均显式排除，无内容蔓延 | ✓ |
| 需求一致性 | 场景 F/J 数据流可支撑（§7.3 交互验收对应计划表 M1 验收场景 F/J） | ✓ |
| 需求一致性 | 计划表任务 1.1~1.5 与 spec §二~§七 一一对应（config 子项裁剪已声明，偿还指向见 S2） | ✓ |
| 概设一致性 | 状态机（§一）与概设 §4.1 状态图/行为表一致；守卫检查点位置不变声明与概设一致 | ✓ |
| 概设一致性 | 高风险确认（§四）与概设 §4.2 语义一致（提示词引导、无硬编码、批准后按当前策略执行） | ✓ |
| 概设一致性 | PLAN 行为基座（声明层隐藏/SAFE 白名单/区外读审批/dispatch 校验）为概设允许的 v1.0 机制复用 | ✓ |
| 概设一致性 | v1.0 偏离声明：5 项关键差异显式列出（mode_changed 发射点处置除外 → B1） | ✓ |
| 概设一致性 | 新增测试 test_mode_default_build.py 与概设 §11 清单同名同覆盖面 | ✓ |
| 概设一致性 | 实现落位全在既有文件，无违反概设工程结构的路径 | ✓ |
| 结构与可执行性 | 头部要素齐全（创建日期/状态/上游依据/决策记录），相对链接全部可达 | ✓ |
| 结构与可执行性 | 内容完备：总体设计/数据模型（PlanDecision）/接口定义/错误处理表/测试与验证齐备 | ✓ |
| 结构与可执行性 | §7.1 适配清单与代码真实依赖面吻合（13 个测试文件逐一核对：无其他用例依赖自然终止回归或 SessionState 默认值的渲染断言；test_arrow_select 用自定义 OPTIONS，不受选项改二影响） | ✓ |
| 结构与可执行性 | 边界定义：/build 无参在 PLAN 下行为、PLAN 下 submit_plan 批准的策略落位（enter_build 维持策略）、BUILD 下批准幂等均已定义 | ✓ |
| 结构与可执行性 | 基线口径：≥140 passed 守恒与计划表「既有测试全绿」硬门槛一致 | ✓ |
| 结构与可执行性 | 代码靶点真实性：approval.py L78~88、loop.py L105~116、base.py L198~204/L233~243、planning.py CHOICE_*/modes={MODE_PLAN}、cli.py L377/L864~901/L115/SLASH_COMMANDS、prompts.py L28~39、commands.py 既有文案/注释引用均核实准确 | ✓ |

## 五、复审要求

**结论：不通过**——存在阻塞项 B1、B2，必须修复后复审。

1. **B1**（必须）：显式处置 loop.py L145~155 mode_changed 统一出口（保留并修正表述 / 删除并声明替代反馈通道），同步修订 §〇 裁剪表、§2.2 与 §7.2 断言。
2. **B2**（必须）：为「锚行后附『已进入 Build 模式』」指定唯一可落地契约（PlanDecision 扩展 / 回调契约调整 / 闭包内呈现三选一），同步 §4.1/§4.3/§八/§7.2。
3. 建议项 S1~S9 不阻塞放行，建议随 B1/B2 修复一并处理（尤其 S2 偿还任务号、S4 残留清理清单，直接关系 M1 收尾验收）；如暂缓，请登记至 TODO.md 并注明去向。

复审时按规范生成新报告文件（轮次 r2），不覆盖本报告。
