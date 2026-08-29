# Spec 一致性评审报告：V1.1-M1 模式基座 Spec（r2 聚焦复审）

> 评审日期：2026-08-30 11:50
> 评审对象：`docs/designs/202608301000-plan-v11-m1-mode-base.md`
> **模式：聚焦复审（r2，改动范围：头部上游依据、§〇裁剪表、§一、§2.2、§2.3、§3.2、§3.3、§4.2、§4.4、§5.2、§六、§7.1、§7.2、§7.3、§八）**
> 对照文档：编程智能体需求文档v1.1（§2.1 FR-35~39、§4 优先级）、编程智能体概要设计说明书v1.1（§4.1~§4.3、§9、§11、§13）、Glaucous开发计划表v1.1（V1.1-M1 任务 1.1~1.5 与验收、V1.1-M3 任务 3.1）
> 上一轮报告：`docs/reviews/202608301100-spec-review-v11-m1-mode-base.md`（r1，不通过：B1/B2 + S1~S9）
> 代码基线：`src/glaucous/agent/loop.py`、`tools/planning.py`、`tools/base.py`、`cli.py`、`ui/renderer.py`（本轮改动直接涉及面，逐行核对）
> 结论：**有条件通过**（阻塞 0 项，建议 1 项）

## 一、聚焦范围说明

本轮为聚焦复审，非全量重评。核验范围：

1. **r1 两项阻塞（B1/B2）的消除验证**——针对改动章节 §〇裁剪表 mode_changed 行、§一第 4 条、§2.2、§4.2、§4.3（衔接）、§六、§7.2，逐点对照 `loop.py` L93/L97/L107/L127/L145~155 与 `planning.py` L40/L87/L129~134、`cli.py` L864~903 的代码现状。
2. **r1 九项建议（S1~S9）的修复核对**——改动章节与上游文档（概设 §9/§11 标题、§4.3 要点 3 原文、§11 末段测试清单；计划表任务 1.1「config 默认值」子项、V1.1-M3 任务 3.1）逐条比对。
3. **波及面**——本轮未改动的 §二 modes.py 设计、§3.1、§4.1、§4.3、§5.1、§7.1 其余条目，与新表述的交叉引用一致性；改动触及维度二核心机制（mode_changed 统一出口、submit_plan 出口协议），相关检查项按波及面规则全部重查。

未在本轮清单内的检查项（需求 §4/§5 硬约束逐项、FR 覆盖面、13 个测试文件依赖面全量核对等）继承 r1 结论（r1 报告第四节通过项），本轮改动未触及这些面的实质内容（§7.3 基线口径、依赖、合规项均未变化）。

## 二、上轮阻塞项处置核验

| 编号 | 处置 | 核验结果 |
|---|---|---|
| B1 | 已修复 | ✓ 消除（详见 B1 核验） |
| B2 | 已修复 | ✓ 消除（详见 B2 核验） |

### B1 核验：mode_changed 统一出口——已消除

r1-B1 指出 spec 宣称「mode_changed 无发射点/死分支」与 `loop.py` L145~155 现存第二个发射点矛盾、未裁决走法。r2 修复（裁决为保留方案）核验如下：

1. **代码事实吻合**。spec §2.2 新表述：
   > 「L105~116『Build 自然终止 → 回归 Plan』分支整体删除：自然终止序列变为 终答入史 → `_emit_budget()` → `return msg.text`……L145~155 dispatch 统一出口**保留不动**……PLAN 下 submit_plan 批准回 Build 时经此发射 mode_changed（payload：mode=build、policy=维持值）……BUILD 下批准比对为假不发射」

   逐点对照 `loop.py` 现状：
   - L102~104（`push_assistant` / `_emit_budget`）+ L117（`return msg.text or ""`）为保留序列，L105~116 恰为回归分支（注释 + `if mode_snapshot == MODE_BUILD` + `return_to_plan()` + emit）——删除范围精确 ✓
   - L145~155 统一出口：`if self._state.mode != mode_snapshot: self._emit("mode_changed", {...})`，payload 含 `mode`/`policy`/`reason`，与 spec「mode=build、policy=维持值」一致 ✓
   - `mode_snapshot` 定义于 L93，引用于 L97（`tool_schemas`）、L127（`dispatch`）、L147（出口比对）——spec「mode_snapshot 变量保留（L97/L127 仍有引用）」成立 ✓
2. **与 confirm 闭包新规则的链路闭环**。`cli.py` L864~903 现状：confirm 闭包在 dispatch 调用栈内切状态（L890/L892 两处 `enter_build`），dispatch 返回后 L147 比对发射。spec §4.3 收敛为「`decision.choice == CHOICE_APPROVE and ctx.state.mode == MODE_PLAN` 时调 `enter_build()`，BUILD 下批准不触碰状态」→ PLAN 下批准：比对为真、发射（renderer L54~58 的 mode_changed 分支为活分支，事件行即时呈现）；BUILD 下批准：比对为假、不发射 ✓
3. **内部自洽**。§〇裁剪表 mode_changed 行（「dispatch 统一出口……保留」「无需偿还：mode_changed 分支为活分支」）、§一第 4 条、§2.2、§4.2（切换反馈由事件承担）、§4.3、§六（「BUILD 下批准……不发射 mode_changed」）、§7.2（「BUILD 下批准不改状态不发射 mode_changed、PLAN 下批准切 Build 且发射 mode_changed（mode=build、policy=维持值）」断言）、§八，八处相互引用无冲突 ✓

### B2 核验：切换反馈承载契约——已消除

r1-B2 指出「锚行后附『已进入 Build 模式』」在 ConfirmCallback 契约/SubmitPlanTool 构造/PlanDecision 结构下无落地路径。r2 采纳裁决方案 c，核验如下：

1. **可实施性**。spec §4.2 新表述：
   > 「批准：`用户已批准方案，请按方案执行。`+ 锚行……**不在回喂文本中拼接任何切换附言**——ConfirmCallback 契约（str→PlanDecision）与 SubmitPlanTool 构造均不改动」

   `planning.py` 现状：`ConfirmCallback = Callable[[str], PlanDecision]`（L40）、`__init__(self, confirm, plans_dir)`（L87）、回喂经 execute 内 `_reply` 组装（L131~134）——固定文案 + 锚行输出无需感知 mode，现有机制完全支撑 ✓
2. **与 §4.3 衔接一致**。confirm 闭包现状确含两处 `ctx.state.enter_build(...)`（`cli.py` L890/L892），spec「删除两处」属实；闭包持 `ctx` 可读 `ctx.state.mode`，「PLAN 下批准才 enter_build（维持策略）」可实施；pause/resume、flush_text_segment、plan_decision 伪事件、live_hooks["step"] 保留声明与现状（L871/893/894~898/900~901）吻合 ✓
3. **反馈闭环**。PLAN 下批准的切换反馈由 mode_changed 事件行承担（见 B1 核验第 2 点），BUILD 下批准无切换、无需反馈——spec §4.2/§4.3/§六/§7.2 四处一致 ✓

## 三、上轮建议处置核对

| 编号 | 上轮问题 | 处置 | 核验结果 |
|---|---|---|---|
| S1 | 概设章节头部标注错位一档 | 头部改「§9（CLI 交互增补）、§11（工程结构增补，末段含测试增补清单）」 | ✓ 已修复：概设实际 §9=「CLI 交互与视觉增补」（L296）、§10=「配置与安全增补」（L305）、§11=「工程结构增补」（L326），测试增补清单确在 §11 末段（L350 含 `test_mode_default_build.py`） |
| S2 | 策略配置面裁剪偿还去向无任务号 | 偿还去向改「关联计划表 V1.1-M3 任务 3.1……计划表任务 1.1 的『config 默认值』子项按本裁剪落地为 SessionState 默认值翻转，不新增 config 字段」 | ✓ 已修复：计划表任务 3.1 实存（「sessions/paths.py：project-hash 目录布局 + 旧会话自动迁移」，会话持久化基座，括注概括合理）；任务 1.1 原文确含「config 默认值」子项，对应关系成立 |
| S3 | 补全候选含非法参数 + 回切无通道 | auto-approve 纳入 /build 合法参数（§3.2 分支 + 显式回切声明 + 用法文案 `[auto-approve\|per-action]`），同步 §3.3/§六/§7.2 | ✓ 已修复：四分支定义、策略回切声明（会话内互切 + 重启恢复默认 + /plan 不改策略）、§六非法参数收窄、§7.2 三分支用例全链一致；候选「两者均为 /build 合法参数，选中即合法」矛盾消除。回切参数是 FR-36「默认 auto-approve」语义的自然延伸，不构成范围蔓延 |
| S4 | 三选一残留清理清单不完整 | §7.3 补清理清单五项 + 验收口径「可运行路径零残留 + 注释/文案级随改随清」；§八补清理行 | ✓ 基本修复：五项（renderer.plan_card 死方法、planning.py docstring、base.py L57 注释——已亲核属实、cli.py Banner L91、cli.py resume docstring L983）与 r1 所列残留一一对应。**但存在新缺口 → 本轮 S10（loop.py L145~146 注释未入清单）** |
| S5 | handle_command 分派行未列入实现位置汇总 | §3.2 补「/build 分派行改传 rest 参数（现状不传，漏改则参数恒为空）」；§八 commands.py 行同步 | ✓ 已修复（commands.py 分派行现状 r1 已核实为不传 rest） |
| S6 | base.py 拦截 hint 括注与分支语义错位 | 括注改「else 分支（Build 下引用仅 PLAN 工具）」+ 兜底说明「该分支在 v1.1 下暂无触发工具……保留为兜底」 | ✓ 已修复：`base.py` L198 条件确为 `tool.modes == frozenset({MODE_BUILD})`（PLAN 下引用仅 BUILD 工具），L203~204 else 为 Build 下引用非 BUILD 专属工具；submit_plan 改 ALL_MODES 后无仅 PLAN 工具，兜底说明自洽 |
| S7 | 补全 policy 分支缺测试声明 | §7.1 列 `tests/test_repl_completer.py` 扩展（候选集合/前缀过滤/display_meta）；§7.2 补 policy 补全用例 | ✓ 已修复，两处均落实 |
| S8 | toolbar_text「双态渲染」表述与代码不符 | §2.3 表述拆分：「`_mode_badge` 已按 mode+policy 双态渲染……`toolbar_text` 仅按 mode 渲染徽标（无 policy 后缀）」 | ✓ 已修复：`renderer.py` L90~96 `_mode_badge` 确为双态（·每次审批/·自动放行/◆ plan），L117~128 `toolbar_text` 确为仅 mode（`badge = "⬥ build" if mode == "build" else "◆ plan"`） |
| S9 | 「概设 §4.3 要点逐条」存在未注明适配偏差 | §5.2 ① 加注记：「概设要点 3 原文『禁止在需求未澄清时启动长流程』中的长流程=Spec 起草/评审，随 M5 落地；M1 阶段适配为『不蛮干动手』」 | ✓ 已修复：注记与概设 §4.3 要点 3 原文（L116）吻合，适配口径显式、与 FR-37 验收标准对应 |

## 四、新发现问题

### S10. loop.py L145~146 注释未纳入三选一残留清理清单
- **维度**：需求一致性（计划表验收覆盖缺口，r1-S4 同类）
- **spec 位置**：§一第 4 条 / §2.2 「L145~155 dispatch 统一出口**保留不动**」；§7.3 清理清单仅列 renderer.plan_card、planning.py docstring、base.py L57、cli.py L91、cli.py L983 五项
- **代码事实**：`agent/loop.py` L145~146 注释：
  > 「# submit_plan 三选一①②在 dispatch 内改 state——比对快照统一 emit / # mode_changed（Day2 Plan §4.5 统一出口，自然终止回归在上方分支 emit）」

  v1.1 下两处失实：其一「三选一①②」为三选一残留字样，直接命中计划表 M1 验收口径「代码中无三选一残留引用」；其二「自然终止回归在上方分支 emit」所指分支（L105~116）本批删除，表述失实。
- **冲突说明**：spec「保留不动」的字面执行会连注释一并保留，而清理清单（「注释/文案级随改随清」的显式化载体）未含此项——恰是本批改动直接波及的相邻注释，最易在「保留不动」指令下遗漏，与 S4 清单的设立意图（避免 M1 收尾验收遗漏）相悖。
- **建议**：将「`loop.py` L145~146 注释改写（去除『三选一①②』与『自然终止回归在上方分支 emit』表述，对齐『PLAN 下 submit_plan 批准经此出口发射』新语义）」补入 §7.3 清理清单；或在 §2.2 对「保留不动」补一句「注释随本批语义同步改写」。定级为建议：机制行为已由 spec 明确，不影响实现判断，仅涉验收口径与注释准确性。

## 五、波及面复查（本轮未改动章节 × 新表述）

| 交叉点 | 结论 |
|---|---|
| §二 modes.py（enter_build 三形态/enter_plan）↔ §一第 6 条「三处显式改变」↔ §3.2 /build 四分支 ↔ §3.2「/plan 不改变策略」 | 一致：无参=维持、per-action、auto-approve 恰为三处显式改变；enter_plan 清豁免不动策略，与「策略作用域=构建期」声明吻合 |
| §二 enter_build(policy=None)「两种情况均清空 approved_types」↔ §3.2 空参数分支「维持现策略，仅确保 mode=BUILD」 | 一致 |
| §3.1 approval.py 零改动 ↔ §一第 3 条「gate 逻辑零改动」↔ §六「DANGEROUS/区外读永远单独确认」 | 一致 |
| §4.1 PlanDecision 二选常量 ↔ §4.3 confirm 规则引用 `CHOICE_APPROVE` ↔ §7.2 断言 | 一致（新常量命名与「CHOICE_* 三常量全部删除」指旧三常量，意图明确，不歧义） |
| §4.3 EOF/Ctrl+C → CHOICE_FEEDBACK ↔ §六「确认卡 EOF/Ctrl+C/Esc 归为修改意见」 | 一致 |
| §5.1 /plan 语义 ↔ §3.2 /plan 改造（enter_plan + 文案） | 一致 |
| §7.1 修订清单 ↔ §2.1 新语义（enter_plan 断言三要素、enter_build(None) 维持策略断言、显式构造适配） | 一致 |
| §六「已处于 Build 且无参数变化时轻提示」 ↔ §3.2 审计文案段 | 一致，/build auto-approve 重复执行幂等边界已覆盖 |
| §〇裁剪表「/plan、/build 走命令面直改状态，不经事件通道」↔ loop 出口仅在 dispatch 栈内比对（命令面不经 loop dispatch） | 一致，命令面切换无 mode_changed 发射、经 mode_switch 审计事件呈现 |
| §4.2「落地 FR-39『口头确认』」↔ 需求文档 FR-39「/build 或口头确认回到 Build」 | 一致 |
| §7.2 test_mode_default_build.py ↔ 概设 §11 末段测试增补清单同名条目（默认 Build/底线守卫/风险确认通道） | 覆盖面一致（spec 为概设允许的细化超集） |
| §八实现位置汇总 ↔ 正文各章（含新增「分派传 rest」「清理行」） | 一致；loop.py 行仅列删除分支、未列 L145~155 保留项——保留属零改动，§一/§2.2 已明确，可接受 |

## 六、通过项（本轮实际复查项；其余继承上轮）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 概设一致性 | B1：mode_changed 统一出口处置与 loop.py 代码事实吻合（删除范围/保留范围/mode_snapshot 引用/payload 语义逐点核对） | ✓ |
| 概设一致性 | B1：八处引用（§〇/§一/§2.2/§4.2/§4.3/§六/§7.2/§八）内部自洽 | ✓ |
| 概设一致性 | S1：头部概设章节标注与概设实际标题逐一对得上（§9/§10/§11） | ✓ |
| 概设一致性 | S6：base.py 拦截分支括注与 L198/L203~204 分支语义一致 | ✓ |
| 概设一致性 | S8：renderer `_mode_badge`/`toolbar_text` 表述拆分与代码一致 | ✓ |
| 概设一致性 | S9：概设 §4.3 要点 3 适配注记与原文吻合 | ✓ |
| 需求一致性 | S2：偿还去向指向计划表实存任务号（M3-3.1）且注明任务 1.1 config 子项对应关系 | ✓ |
| 需求一致性 | S3：/build 参数面扩张后 FR-36 语义仍守住（per-action 可选 + 默认 auto-approve 可回切），无范围蔓延 | ✓ |
| 需求一致性 | S4：三选一残留清理清单五项与代码残留一一对应（base.py L57 亲核属实） | ✓ |
| 需求一致性 | FR-39「口头确认」引用与需求原文一致（§4.2 改动波及复查） | ✓ |
| 结构与可执行性 | B2：方案 c 在 ConfirmCallback/SubmitPlanTool/`_reply` 现有契约下可实施，与 §4.3 衔接闭环 | ✓ |
| 结构与可执行性 | S5：分派传 rest 已入 §3.2 与 §八 | ✓ |
| 结构与可执行性 | S7：policy 补全测试已入 §7.1/§7.2 | ✓ |
| 结构与可执行性 | S3：补全候选「选中即合法」与 /build 四分支、§六收窄、用法文案四点一致 | ✓ |
| 结构与可执行性 | 波及面：§二/§3.1/§4.1/§4.3/§5.1/§7.1 与新表述交叉引用无矛盾（见第五节） | ✓ |
| （继承 r1） | 需求 §4/§5 硬约束合规、FR-35~39 覆盖、范围守恒、场景 F/J、测试依赖面全量核对、头部链接可达 | 继承上轮 ✓（本轮改动未触及） |

## 七、复审要求

**结论：有条件通过**——r1 两项阻塞均已消除，无新增阻塞；1 项新建议放行前登记待办即可。

1. **S10**（建议，登记 TODO.md 后放行）：`loop.py` L145~146 注释补入 §7.3 清理清单或在 §2.2 补「注释随语义同步改写」一句——直接关系 M1 验收口径「代码中无三选一残留引用」的收尾核对，建议随实现一并处理，无需再触发一轮评审。
2. S1~S9 修复均已确认到位，无遗留。
3. 本报告为聚焦复审结论；后续如再有章节改动，按改动范围决定全量或聚焦复审。
