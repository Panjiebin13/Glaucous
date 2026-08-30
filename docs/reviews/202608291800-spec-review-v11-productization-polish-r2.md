# Spec 一致性评审报告（第 r2 轮）：v1.1 前置产品化打磨（技能创建 / 补全增强 / 思考折叠 / 用量展示 / 交互升级）

> 评审日期：2026-08-29 18:40
> 评审对象：`docs/designs/202608291800-plan-v11-productization-polish.md`（v1.1 评审修复版）
> 首轮报告：`docs/reviews/202608291800-spec-review-v11-productization-polish.md`（不覆盖，本报告为复审增量）
> 对照文档：`docs/编程智能体需求文档.md`（v3.2）、`docs/编程智能体概要设计说明书.md`（v4.1 §5/§6/§7/§8/§9/§10）、`docs/Glaucous开发计划表.md`、`TODO.md`、代码现状（agent/loop.py、cli.py、commands.py、permission/approval.py、tools/planning.py、llm/client.py、llm/registry.py）
> 模式：全量复审（首轮复审要求「按全量流程执行，R3/R6 波及 §五/§七/§十 一并复查」），重点复核 B1~B4/S1~S10 闭合与修复引入的新问题
> 结论：**不通过**（阻塞 2 项，建议 4 项）

## 一、评审范围

按首轮复审要求全量执行：逐条核对首轮 4 阻塞 + 10 建议在 v1.1 中的闭合情况（含与代码现状的兼容性核验），并对修复触及的 §三（R3）、§五（R5）、§六（R6）、§七（R7）、§九、§十 执行波及面复查。「上游依据」新增引用 FR-08/FR-10/FR-11 与概设 §5，经核验均真实存在（需求文档 L38/40/41；概设 §5.2/§5.3 三选项与三选一），链接可达性维持通过。

## 二、首轮问题关闭情况

| 编号 | 状态 | 证据（v1.1 位置 × 核验事实） |
|---|---|---|
| B1 选项集与三选项契约 | ✅ 已关闭 | §6.2 逐卡枚举：审批卡「同意/同意同类型/拒绝」映射 `ApprovalDecision.choice` 的 approve/approve_type/reject（与 cli.py L238-244 及 approval.py `ApprovalDecision` Literal 一致）；方案卡「既有一/二/三（FR-08、planning.CHOICE_*）」（planning.py L26-28 常量 "1"/"2"/"3" 吻合）；提问卡 ≤6 选项箭头选择、取消返回 None 与既有「未响应」语义一致；Esc 落点逐卡写明。FR-11/概设 §5.3 契约恢复。残留：DANGEROUS 未分列 → 新增建议 r2-S3 |
| B2 同步回调内选择器可行性 | ✅ 已关闭 | §6.1 弃用 prompt_toolkit Application，改终端原始按键：Windows `msvcrt.getwch()` / POSIX `termios/tty` raw 模式（try/finally 还原）。核验：三回调均为同步函数，现状本就以阻塞 `console.input` 在运行中事件循环内执行（cli.py L189/232/267），阻塞式按键读取与其同级可行，无事件循环冲突；按键源抽象为 `read_key` 注入参数，可测性闭合。跨平台表述两端俱全。原「回退掩盖失效」担忧消除：原始按键不依赖可失败的应用构造，回退仅对应真实终端异常 |
| B3 折叠触发点判定 | ✅ 已关闭（表述残留 → 新增阻塞 r2-B1） | 触发机制重建成立：§3.1 前置明确「流式期间无法预知当前 text 是否最终回答」，折叠时机后移至 `stream_end`；「run 循环命中『无 tool_calls → 返回』分支时、return 前发射」对应 loop.py L101-117 自然终止分支，结构可实现；「提前返回分支同样发射」与代码一致——步数上限（L81-84）、`_enforce_budget` 熔断（L88-90）、解析熔断（L132-136）三条提前返回统一经 `_terminate`（L246-255），在该函数补发射即可全覆盖。§九 R3 行已列 `agent/loop.py`。但 §3.1 文本事件的呈现分配仍依赖不可预知信号，见 r2-B1 |
| B4 Live 区与阻塞交互共存 | ✅ 已关闭 | §3.2 按真实通道拆分：明确「ask/decision/拒绝理由/重试提示不是 on_event 事件，而是 CLI 注入的同步回调」；`live_hooks` pause/resume 覆盖四个阻塞点——`make_ask_callback.ask`（cli.py L189 阻塞 input）、`make_decision_callback.decide`（含拒绝理由，L232/L243 两处 input 由一次 pause 覆盖）、`prompt_plan_decision`（L267）、`ThemeRenderer.retry`（退避睡眠期让位）；R6 选择器嵌套于前三者内部，无遗漏。异常路径 try/finally 保证 resume；折叠关闭/管道注入 no-op。缓冲范围补交互伪事件（ask/decision/plan_decision），与 §3.4「/expand 完整回看」自洽 |
| S1 usage 发射主体 | ✅ 已关闭 | §5.1 改为 `LLMClient.__init__` 构造注入 `on_usage`（与既有 `on_retry` 同模式，client.py L64-70 可对照），不经 loop 转发；§九 R5 行相应只列 client/cli/commands，与决策一致。`switch_profile` 保留钩子与现有「on_retry 钩子保留」语义（client.py L77-88）对齐 |
| S2 本轮合计口径 | ✅ 已关闭 | §5.2 新增 `turn_usage` 累加器（数字求和、cache_hit/miss 首个非 None 转 0 基线后累加），并明确「折叠摘要行（§3.1）的 token 段与该行同源（turn_usage 累计），两处数字一致」；渲染顺序上摘要行与用量行均在 `reset_turn_buffers` 清空之前，时序自洽 |
| S3 stream_end 术语与 ctx 重置表述 | ✅ 已关闭 | `stream_end` 已由 §3.1 声明为新增事件（不再是幽灵术语）；§3.3 第 4 点更正为「ctx 为同一对象复用，字段不会随 rebuild_loop 自然重置」，显式 `reset_turn_buffers` 于 /clear、/resume 调用——与 commands.py `_cmd_clear` 在同一 ctx 上替换组件的事实（L150-170）一致。残留：「见 3.4」交叉引用落空 → 并入 r2-B2 |
| S4 R7 回答文本来源 | ✅ 已关闭 | §7 确定为 `await ctx.loop.run(task)` 返回值，删除「二选一」。核验：loop 自然终止返回 `msg.text or ""`（L117），为现成单一数据源。渲染时点表述与事件时序的张力 → 新增建议 r2-S1 |
| S5 R7 与概设 §8.4 偏离声明 | ✅ 已关闭 | §7 末段显式登记偏离：「概设 §8.4 约定『正文不套面板』……在此登记偏离，概设修订随 v1.1 文档轮次同步」。核验概设 §8.4 原文「正文流式 Markdown 内联渲染，不套面板」存在（概设 L489），引用准确；§十一 验收项 6 将卡片呈现列为预期形态 |
| S6 stream_options 网关降级 | ✅ 已关闭 | §5.1 增补「首次请求因 stream_options 不被支持失败（不可重试类异常）→ 去掉该参数原样重试一次；再失败走正常错误流」。与代码兼容：现状不可重试错误直接抛 `LLMError`（client.py L109-110、L172-183），降级分支可在该点前插入，不触碰 429 重试链；§十 1 含降级重试用例 |
| S7 FR-31 裁剪偿还去向 | ✅ 已关闭 | §0 裁剪条目补「留待 M4（TODO 3.3 条目已登记该去向）」。核验 `TODO.md` L120「FR-31 状态栏仍待办」存在，偿还路径闭合 |
| S8 Banner 数据源口径 | ✅ 已关闭 | §八 A 明确「数据源统一为 `ctx.current_model`（与输入区头部行同口径；……Banner 为启动快照不随切换刷新）」，与现状 `render_prompt_header(ctx.current_model, report)`（cli.py L721）及首轮 r3-S5 登记口径一致；签名改 `render_banner(model_name, mode)`，动态值 escape |
| S9 R1 模式提示与 R1/R7 测试归属 | ✅ 已关闭 | §1.2 新增第 5 点「写文件工具仅在 Build 模式可用——若当前处于 Plan 模式，须先声明进入 Build」，与概设 §5.1 声明层隐藏写工具机制衔接；§十 2 `test_assets.py`（frontmatter 可解析/无 api_key 明文/生成与回退）与 §十 6 `test_answer_card.py` 均标注「S9 归属」，测试归属落地 |
| S10 COMMAND_META 循环导入落点 | ✅ 已关闭 | §2.1 定为 `commands.py` 新增 `COMMAND_META`，`HELP_LINES` 由它拼装，cli 补全器 meta 从 `commands.COMMAND_META` 导入。核验导入方向：cli.py 顶层 `from .commands import …`（L30）已存在，commands→cli 仅函数内延迟导入（commands.py L152-155 注释「反向引用只能函数内完成」），数据源落 commands 侧不产生模块级环 |

**小结**：首轮 4 阻塞项的修复机制（三选项契约恢复、原始按键选择器、stream_end 事件、live_hooks 时序）全部成立且与代码现状兼容；10 项建议全部闭合。但对 §3.1 的修复遗留两处新的内部矛盾（见第三节），故本轮仍不通过。

## 三、新增阻塞问题

### r2-B1. §3.1 文本事件的呈现分配自相矛盾：「全部事件入 Live」与「最终回答增量在 Live 下方」无法同时成立，且缓冲范围与步数口径未定
- **维度**：结构与可执行性（内部自洽，B3 修复范围内残留）
- **spec 位置**：§3.1 前提「既有事实：……loop 对**每次** LLM 响应（含带 tool_calls 的中间步正文）都发射 `text` 事件，流式期间无法预知当前 text 是否最终回答」；同节「**TTY 且折叠开启**……本轮 `stream_end` 之前的全部 on_event 事件不逐条直接打印，而是渲染进一个 `rich.live.Live` 动态区……；最终回答的流式增量（`text` 事件）照常逐字打印在 Live 区**下方**」；§3.2「`turn_events`……记录两类条目：① on_event 的 `(event, payload)` 全量；② 交互伪事件」；§3.1 计数「⚙ 思考中 · N 步」与摘要行「💭 思考过程（N 步 · …）」；§3.4 不变量「摘要行的 N 与 /expand 打印条数一致」；§7「流式期间保持逐字保真（现状不变）」
- **上游位置（代码事实）**：`agent/loop.py` 每次 `chat` 均传 `on_text=self._emit_text`（L95-99、L271-273），中间步正文与最终回答的 text 事件在流式期间无任何可区分标记——此即首轮 B3 判定依据，v1.1 §3.1 前提已自行承认
- **冲突说明**：三个相互关联的未决点：① 前提声明「无法预知 text 是否最终回答」，但呈现条款仍以「最终回答的流式增量」为分流条件——若按「全部 on_event 入 Live」，text 事件（含最终回答）全部进 8 行滚动窗，「照常逐字打印在下方」与 §7「流式期间逐字保真」不可达；若按「text 一律打印在 Live 下方」（唯一可实现读法），则「全部 on_event 入 Live」不成立，且中间步正文同样全量可见（折叠不覆盖正文）——两种读法下总有一条条款失真，实现者必须替 spec 做取舍；② `turn_events` 的「on_event 全量」是否含 text 增量未定义：若含，/expand 将重放最终回答全文（与 R7 md 卡片三份重复）且「N 步」计数被增量条目淹没；若不含，「全量」表述失真；③ 「N 步」的口径（tool_start 数？非 text 事件数？缓冲条目数？）未定义，而 §3.4 将其与 /expand 条数绑定为不变量，口径不同则不变量无法验收。
- **修复方向**：存疑，提请作者确认。显式改写为可实现分配（如「text 事件（含中间步正文）不入 Live 区与缓冲，照常逐字打印在 Live 区下方；仅非 text 事件与交互伪事件渲染进 Live 区并计入缓冲」或作者选定的其他口径），同步定义「N 步」计数来源与 `turn_events` 收录范围，使 §3.4 不变量可验收。

### r2-B2. 异常 raise 路径无 `stream_end`，「收缩/清理总被执行」承诺不成立；§3.1「补发约定见 3.4」引用落空
- **维度**：结构与可执行性（与代码事实不符 + 内部交叉引用失效）
- **spec 位置**：§3.3「`stream_end` 之外终止（预算熔断提前返回、异常兜底）：loop 的提前返回分支同样发射 `stream_end`（保证 cli 侧收缩/清理总被执行）」；§3.1「**`stream_end` 到达**（本轮结束，含异常兜底路径后的补发约定见 3.4）」；§3.4 仅含两条不变量（「折叠只改变呈现」「N 与 /expand 条数一致」），无任何补发约定
- **上游位置（代码事实）**：`agent/loop.py` 的终止路径分两类——返回类（自然终止 L117、三条提前返回经 `_terminate` L81-84/L88-90/L132-136）与异常类（`except BaseException` 分支 L137-143：salvage 悬空 call 后 **raise**，不是返回；另 `chat` 抛 `LLMError` 直接穿透 `run`，client.py L110/L120）。异常最终由 repl 的 `except (KeyboardInterrupt, asyncio.CancelledError)` 与 `except Exception` 承接（cli.py L750-759）
- **冲突说明**：① 「提前返回分支同样发射」只覆盖返回类路径；`LLMError`（鉴权/参数/重试耗尽）与中断（Ctrl+C → CancelledError）等 raise 路径不经任何返回分支，`stream_end` 不会发射——折叠模式下 Live 永不停止、`turn_events/turn_usage` 不清空，下一轮成功轮次将把两轮事件一并折叠，「保证收缩/清理总被执行」的字面承诺与代码结构不符；② §3.1 明示「补发约定见 3.4」，§3.4 实际不含该约定（约定只以「提前返回分支」形式部分出现在 §3.3），交叉引用指向不存在的内容，实现者无法据文档完成异常路径的 Live 善后。
- **修复方向**：存疑，提请作者确认。补齐异常路径约定（方向二选一或组合：cli 侧 repl 的 except/finally 中执行 Live 停止 + `reset_turn_buffers`；或声明 `stream_end` 补发的具体落点），并将 §3.1「见 3.4」改为指向实际承载该约定的小节，保证文档自引用有效。

## 四、新增建议问题

### r2-S1. R7/R5 渲染时点：§3.1 将其列入「stream_end 到达」步骤，但数据源（run 返回值）在事件发射时不可得
- **维度**：结构与可执行性（时序表述）
- **位置与摘录**：§3.1「`stream_end` 到达……2. 按序执行 R7（md 卡片）、R5（用量行）渲染」；§7「回答文本来源……`await ctx.loop.run(task)` 的返回值」
- **建议**：`stream_end` 在 loop 内 `return` 前发射（§3.1），cli 的 on_event 同步执行时 `run()` 尚未返回，R7 所需全文此刻不可得；唯一一致实现是步骤 1（Live 收缩）可在事件侧或轮末执行，步骤 2/3 实际发生在 `run()` 返回后的轮末处理。建议在 §3.1 注明「R7/R5 渲染与缓冲清空在 `ctx.loop.run(task)` 返回后（轮末）执行，`stream_end` 仅作轮末信号」，消除事件内/事件后歧义。

### r2-S2. `turn_usage` 累计口径未界定压缩调用：`/compact` 与 loop 内 L2 压缩同经 `ctx.llm`，其 usage 计入轮次用量
- **维度**：结构与可执行性（口径完备性）
- **位置与摘录**：§5.1「`LLMClient.__init__` 签名追加 `on_usage`」、§5.2「cli 构造 `LLMClient` 时注入 `on_usage` 累加器」；代码事实：`compactor.compact_history(..., self._llm, ...)`（loop.py L197-199）与 `_cmd_compact`（commands.py L135-136）复用同一 `LLMClient`
- **建议**：on_usage 为客户端级回调后，L2 压缩调用的 token 会累入 `turn_usage`：loop 内压缩计入当轮尚可称「本轮开销」，但轮间 `/compact` 的用量会串入下一轮的用量行，数字误导。建议明确口径（计入当轮/压缩用量单列/不计入），并在 `reset_turn_buffers` 的清空时机上保持一致。

### r2-S3. §6.2 审批卡未分列 DANGEROUS 与非 DANGEROUS（首轮 B1 修复方向曾显式要求）
- **维度**：结构与可执行性（决策完备性）
- **位置与摘录**：§6.2「审批卡（`make_decision_callback`）：选项为既有三选项……『同意』『同意同类型』『拒绝』」；代码事实：现 `decide()` 对 DANGEROUS 只提示 `\[a] 同意 \[c] 拒绝(附理由)`、不提供 b（cli.py L231-234，注释「破坏性命令……不可批量放行」）；`gate` 对 DANGEROUS 的 approve_type 仅本次放行、不记豁免（approval.py L114-123）
- **建议**：安全语义无破坏（gate 守卫兜底），但「既有三选项」对 DANGEROUS 与现状（两选项）不一致，实现者需自行决定是否对破坏性命令隐藏「同意同类型」。建议按首轮 B1 修复方向补一句分列：DANGEROUS 卡选项是否维持现状两项。

### r2-S4. 方案卡第三项文案「拒绝」与既有/FR-08「继续讨论一下」不一致；「理由」落点字段未指明
- **维度**：结构与可执行性（术语与映射）
- **位置与摘录**：§6.2「方案卡……『执行（逐次审批）』『执行（自动批准）』『拒绝』；取消（Esc）= 选择三、理由「用户取消」」；上游：需求文档 FR-08「③继续讨论一下（留在 Plan 修订方案）」（L38）；`PlanDecision` 仅有 `choice` 与 `feedback` 字段、无 `reason`（planning.py L31-36）
- **建议**：选项与 CHOICE_* 的一一映射已由「既有一/二/三」锚定、功能语义无损，但第三项标签建议沿用「继续讨论」以免用户误读为终止；「理由『用户取消』」请显式写明落入 `PlanDecision.feedback`，与首轮 B1「明确理由对应 feedback 的写法」要求闭合。

## 五、通过项（本轮复查）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 概设一致性 | B1 修复后审批/方案契约：三选项映射 `ApprovalDecision.choice`、三选一映射 `planning.CHOICE_*`，与概设 §5.2/§5.3、FR-08/FR-11 一致，`test_approval_flow` 走注入回调不受影响 | ✓ |
| 结构与可执行性 | B2 替代方案可行性：同步阻塞按键读取与现状 `console.input` 同级（均在运行中事件循环内的同步回调），无 prompt_toolkit Application 的循环冲突；`read_key` 注入支持单测 | ✓ |
| 结构与可执行性 | B3 触发机制：`stream_end` 于自然终止分支与 `_terminate`（统一覆盖步数上限/`_enforce_budget` 熔断/解析熔断三条提前返回）可实现，与 §九 R3 行列出 `agent/loop.py` 一致 | ✓ |
| 结构与可执行性 | B4 阻塞点覆盖：ask/decide（含拒绝理由）/plan_decision/retry 四点与代码中全部阻塞式交互点比对无遗漏；伪事件范围与四阻塞点一致（retry 不入缓冲，符合预期） | ✓ |
| 结构与可执行性 | S1/S2 闭合：usage 直连 `LLMClient` 构造注入，发射主体与 §九 表一致；摘要行与用量行同源 `turn_usage`，清空时点在渲染之后 | ✓ |
| 概设一致性 | S5 偏离声明成立：概设 §8.4「正文不套面板」原文核验存在，§7 声明+概设修订登记齐备 | ✓ |
| 结构与可执行性 | S6 降级与 `_is_retryable` 分类兼容，测试声明覆盖「首次参数报错 → 去参重试成功」 | ✓ |
| 需求一致性 | S7 偿还路径：§0 → TODO 3.3/M4 登记（TODO.md L120）核对一致 | ✓ |
| 结构与可执行性 | S9/S10：§1.2 模式提示与概设 §5.1 衔接；§十 测试归属标注；COMMAND_META 落 commands.py 与既有导入方向（cli→commands 顶层、commands→cli 延迟）无环 | ✓ |
| 结构与可执行性 | §十 测试声明覆盖修复后新机制：stream_options 降级（§十.1）、按键序列注入（§十.5）、资产合法性（§十.2）、折叠缓冲与 /expand（§十.3）、补全器（§十.4）、回答卡片（§十.6）、管道降级不回退（§十.7）；基线「67 passed 1 skipped」表述未变 | ✓ |
| 结构与可执行性 | 头部「评审修复记录」声明 B1~S10 吸收并指向首轮报告路径，可达 | ✓ |
| 需求一致性 | 硬约束（§4/§5）：修复未触碰凭据零存储、截止期范围与提交物安排（R4 模板仍仅 `api_key_env`） | ✓ |
| 需求一致性 | 无范围蔓延：新增内容（live_hooks、turn_events、stream_end、select_with_arrows）均为修复既有问题的配套机制，未引入新需求 | ✓ |

## 六、复审要求

不通过。必须修复的阻塞项：

- **r2-B1**：§3.1 重写文本事件呈现分配（消除「全部入 Live」与「最终回答增量在下方」的矛盾）、定义 `turn_events` 收录范围与「N 步」计数口径；
- **r2-B2**：补异常 raise 路径（`LLMError`/中断）的 Live 善后与缓冲清空约定，修正「补发约定见 3.4」的失效引用。

建议项 r2-S1~S4 修复后登记 `TODO.md` 即可，不阻断通过判定。下一轮复审可聚焦 §3.1/§3.2/§3.3/§3.4 与 §6.2（改动章节），其余章节结论本轮已复核继承；若 §3.1 文本分配口径改变，需同步复查 §十.3（`test_turn_collapse`）的断言范围。
