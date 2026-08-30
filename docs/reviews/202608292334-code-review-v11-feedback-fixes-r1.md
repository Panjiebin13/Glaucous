# 代码评审报告：v1.1 验收反馈修复批次 F1~F4（第 r1 轮）

> 评审日期：2026-08-29 23:34
> 评审对象：spec `docs/designs/202608292200-plan-v11-feedback-fixes.md`（v1.1 已批准）；代码提交 90e6824（相对基线 4bb60be）
> 模式：全量评审（改动范围：src/glaucous/cli.py、commands.py、extensions/skills.py；tests/test_turn_collapse.py、test_repl_completer.py、test_skill_command.py）
> 结论：**不通过**（阻塞 3 项，建议 3 项）

## 一、阻塞问题
### B1. F1「默认选中第一条」钩子必现 AttributeError，功能完全失效；且即便修正属性名，complete_next() 也会把候选文本落入输入行，违反 §1.1「仅高亮不落文本」

- **维度**：Spec 符合性 + 逻辑正确性
- **代码位置**：src/glaucous/cli.py:1178-1183
  ```python
  def _select_first(event) -> None:
      state = event.completion_state
      if state and state.completions and state.complete_index is None:
          session.default_buffer.complete_next()
  session.default_buffer.on_completions_changed += _select_first
  ```
- **spec 位置**：§1.1「补全菜单弹出时默认选中第一条候选……自动选中仅改变高亮、不把候选文本落入输入行（与 1.2 两段式不冲突）」；§1.4 验收点「键入 / → 菜单弹出且第一条高亮」
- **冲突/缺陷说明**（运行验证，prompt_toolkit 3.0.51）：
  1. `Event.fire()` 实现为 `handler(self.sender)`（utils.py:78），回调收到的是 **Buffer 对象**；而 Buffer 只有 `complete_state` 属性、**无 `completion_state`**。复刻钩子的最小运行验证必现：
     ```
     AttributeError: 'Buffer' object has no attribute 'completion_state'
     ```
     异常发生在每次补全菜单弹出/刷新时（async_completer 内 fire 点），默认选中逻辑从未执行（complete_index 恒为 None）——§1.1 功能实际未实现，且每次补全均在后台任务中抛异常，并中断该次补全的后续内部处理（唯一候选去重判定、select_first 应用）。
  2. 修复属性名（`event.complete_state`）后仍有第二层违约：complete_next() → go_to_completion(0) → state.new_text_and_position() → **重写 buffer 文档**。运行验证：输入 `/vi` 经 start_completion 后调 complete_next()，文本变为 `/view`（候选落行）——直接违反「不把候选文本落入输入行」明文，破坏两段式交互前提（第一次 Enter 前输入行已被篡改，用户继续键入会得到 `/view<后续输入>` 的错乱文本）。
- **修复方向**：① 属性名改为 `event.complete_state`；② 重新实现「仅高亮」：prompt_toolkit 3.0.51 无现成 API（go_to_completion/complete_next 均落文本），可直接设置 `state.complete_index = 0` 并触发界面刷新（如 get_app().invalidate()），不得改写 buffer 文档。两段式 Enter 钩子本身（cli.py:1147-1165）经源码与运行验证正确（apply_completion 后 complete_state=None；全等直提交判定的 start_position 在候选刷新后与当前文本匹配），可保留。

### B2. 轮末 finally「缓冲空且 printed」分支渲染 🕊 卡片：违反 §4.5「无卡片、无正文重复输出」，并使 §4.4 步骤 2「_terminate 轮步骤 3 自然跳过」不成立（诊断文本被二次渲染为回答卡片）

- **维度**：Spec 符合性（含语义偏差后果）
- **代码位置**：src/glaucous/cli.py:1361-1365
  ```python
  elif answer and ctx.stream_state["printed"]:
      # 降级/管道轮：正文已逐字直接打印，仅补收尾换行与卡片（TTY）
      console.print()
      if answer.strip() and session is not None:
          render_answer_card(answer)
  ```
  配套事实：cli.py:919 对 text 事件无条件置 `ctx.stream_state["printed"] = True`（含折叠激活分支）。
- **spec 位置**：§4.5「非 TTY / GLAUCOUS_COLLAPSE=off：……轮末仅打印用量行（**无折叠行、无卡片、无正文重复输出**——正文已逐字打过）；Live 启动失败降级：同上本轮」；§4.4 步骤 2「_terminate 轮 run() 返回值为诊断文本，经 diagnostic 行呈现后，**步骤 3 因正文缓冲为空自然跳过**」
- **冲突/缺陷说明**（三个可达场景，场景 B 已运行级复现）：
  - **场景 A（GLAUCOUS_COLLAPSE=off 的 TTY 会话）**：thinking=None，text 逐字直打（printed=True），轮末 body 空 → elif 命中 → session 非 None → render_answer_card(answer) 执行——正文已逐字打过后再渲染同内容卡片，同时违反「无卡片」与「无正文重复输出」两条明文。前批（4bb60be）虽有同型行为，但本批 §4.5 为新明文约束，代码未跟随。
  - **场景 B（折叠激活的 _terminate 诊断轮，中间步有正文流）**：运行复现（make_on_event 注入 text→tool_start→tool_end→diagnostic→budget 序列）：中间步正文已被 tool_start flush 落账（缓冲空 ✓）、printed=True（cli.py:919 无条件置位）、answer=诊断文本非空 → **elif 命中 → render_answer_card(诊断文本)**——步骤 3 并未「自然跳过」，终止诊断以最终回答 🕊 卡片形态二次呈现，破坏「诊断经 diagnostic 行交付」的唯一呈现契约。
  - **场景 C（Live 启动失败降级轮）**：§4.5「同上本轮」，同场景 A 违约。
  - 场景 B 可达性由 loop 契约保证：_terminate 恒发生在守卫点（loop.py:80-90），此前 tool_start 必经 → 缓冲恒空；中间步正文是否出现取决于模型输出，多步任务常见。
- **修复方向**：elif 分支收紧——「缓冲空且 printed」仅表示正文已直打，不得渲染卡片（可仅保留收尾换行）；至少必须把「answer 为诊断文本的 _terminate 轮」排除在卡片渲染之外。

### B3. spec §五明文列出的测试用例缺失：「repl 消费后置 None」「当次生效（不污染 system prompt）」无任何覆盖

- **维度**：Spec 符合性（测试交付完整性）
- **代码位置**：tests/test_skill_command.py 全文覆盖核对；tests/ 全域 grep `pending_task` 仅命中 _cmd_skill 输出断言（36/61/69/76/83-86/92-93 行），无 repl 消费路径用例；cli.py:1321-1324 的消费逻辑 `task, ctx.pending_task = ctx.pending_task, None` 无测试可达
- **spec 位置**：§五「新增：tests/test_skill_command.py——/skill 解析（无参提示、未知名报错、省略描述、附描述）、pending_task 组装模板、**repl 消费后置 None**、**当次生效（不污染 system prompt）**、skills 文案无加载状态」；§〇测试声明「本轮包含测试（§五），随代码一并交付」
- **冲突/缺陷说明**：§五清单逐项核对——无参提示 ✓、未知名报错 ✓、省略/附描述 ✓、组装模板 ✓、skills 文案 ✓；「repl 消费后置 None」与「当次生效（不污染 system prompt）」两条明文用例缺失。「消费后置 None」是 F3 当次生效语义的关键护栏（若误保留会重复驱动 run()），当前无回归保护。spec §〇范围裁剪清单未声明此项裁剪，M4 既有测试债务条款不覆盖新增用例。
- **修复方向**：补两条用例：① 将 repl 消费逻辑可测化抽取（或以最小 fake ctx 驱动消费点），断言 pending_task 置 None 且 task 取值为组装文本；② 断言 /skill 轮 ctx.system_prompt 保持不变（技能正文仅作为任务文本经 run() 入史）。

## 二、建议问题

### S1. make_on_event 的 ws 参数已无用途
- **维度**：可维护性
- **代码位置**：src/glaucous/cli.py:907（签名）、959（rebuild_loop 传参）
- **说明**：ws 前批用于 _render_md_tool_end(payload, ws)，本批删除 md 卡片后函数体内不再使用，属遗留参数。
- **建议**：从签名与调用点移除，或在 docstring 注明保留原因。

### S2. SkillRegistry.loaded_names() 成为产品侧死代码
- **维度**：可维护性
- **代码位置**：src/glaucous/extensions/skills.py:135-137
- **说明**：F2 删除加载态展示后产品代码无调用方（仅 tests/test_skill_command.py:106 断言其为空集）。
- **建议**：随加载态语义移除，或保留但在 docstring 标注仅供测试/调试。

### S3. /skills 条目顺序与 spec 字面存在偏差
- **维度**：一致性（文案）
- **代码位置**：src/glaucous/commands.py:373-375（格式为 `{name} [{来源}] {description}`）
- **spec 位置**：§二「条目仅剩 `[来源] 名称` + 一句描述」
- **说明**：要素完备、加载态已删（核心目标达成），顺序沿用前批「名称在前」排版，与 spec 字面顺序不一致。
- **建议**：与作者确认 spec 措辞是否为格式约束；若为，调换为 `[来源] 名称 描述`。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | F1 补全器：make_repl_completer(workspace, model_names=None) 签名与 §1.3 一致；ARG_COMPLETIONS 超集取代 PATH_ARG_COMMANDS（旧标识零残留）；model_names 经闭包动态取 ctx.registry_entries 档案名（不缓存快照）；空格全列 / 前缀过滤 / 无匹配无候选（§1.3、§1.4） | ✓ |
| Spec 符合性 | F1 两段式 Enter（§1.2）：菜单开 → apply_completion（apply 后 complete_state=None，经源码验证）；候选与输入行全等 → 直提交（防 complete_while_typing 菜单重开死锁）；Escape → cancel_completion（S1 衔接闭环）；管道/降级无补全器不受影响 | ✓ |
| Spec 符合性 | F2（§二）：/skills 无任何加载态字样；说明行逐字一致；COMMAND_META /skills、/expand 联动（S2）；index_text 注入本无加载态 | ✓ |
| Spec 符合性 | F3（§三）：命令面四处登记（SLASH_COMMANDS 17 项 / COMMAND_META / _COMMAND_USAGE / handle_command 分派）；组装模板与 §3.2 逐字一致；完全相等匹配（S6）；pending_task 消费走与用户输入同一任务轮入口（begin_turn → thinking.start_turn → run）；不注入 system prompt；skill_text 纯读取无副作用 | ✓ |
| Spec 符合性 | F4 落账判定（§4.2，B1 契约）：触发点仅两处（tool_start；ask/decision/confirm 伪事件落账前保序 flush）；loop 自然终止序列核实（loop.py:104-117 先 budget 后 mode_changed，均不触发 flush，终答不被误落账）；空段不落账不计数（S3） | ✓ |
| Spec 符合性 | F4 缓冲口径（§4.3）：turn_events → session_events（旧字段零残留）；begin_turn 只清 turn_usage + text_segment，不动会话缓冲（B2 修复成立）；重置点收窄至 /clear、/resume（/stop 落盘不含思考缓冲）；/expand 全会话重放 +「── 思考过程（本会话）──」分隔头 + 空态提示 + 只读；tool_end 缓冲保留全量 content 供重放 | ✓ |
| Spec 符合性 | F4 N 口径（§4.3）：N = 非 text 事件 + 交互伪事件（live_hooks["step"]）+ 正文段落账条目；diagnostic 计入不占行（B4 必达豁免：直打 + 落账 + note_step）；异常轮落账发生在收缩行渲染之后不计入已显示 N（S9）；轮计数器 begin_turn 后经 thinking.start_turn 清零 | ✓ |
| Spec 符合性 | F4 轮末时序（§4.4 主体）：close → turn_ok 正文缓冲一次性输出+卡片 → 异常轮 flush 落账 → text_segment.clear 兜底 → 用量行；折叠轮终答呈现方式偏离已获 spec 记录；_terminate 路径经 loop.py:246-255 核实（先 diagnostic 后 budget，diagnostic 到达时缓冲必空） | ✓（B2 所述分支除外） |
| Spec 符合性 | F4 降级（§4.5）：非 TTY/off 时 text 直打不进正文段缓冲（防轮末重复）、非 text 逐条打印、会话缓冲仍记录（管道 /expand 可用）；Live 启动失败本轮降级不重试；GLAUCOUS_COLLAPSE=off 语义保留 | ✓ |
| 范围控制 | 无范围蔓延：_render_md_tool_end / PATH_ARG_COMMANDS / reset_turn_buffers / turn_events 全部清除无残留；THINKING_LINE_WIDTH 等新增常量属滚动窗口实现细节，未越 spec；§〇范围裁剪项均未擅自实现 | ✓ |
| 工程约定 | 模块位置符合概设 §10（cli.py/commands.py/extensions/skills.py + tests/ 三文件）；无新增配置面；密钥零存储未破坏（概设 §9） | ✓ |
| 逻辑正确性 | 运行验证：pytest tests/ -q → **138 passed, 1 skipped**（≥115 基线守恒；本机需 PYTHONPATH=src 指向 src 布局，属环境因素非代码缺陷） | ✓ |
| 逻辑正确性 | 冒烟：import glaucous ✓；管道链路 /help（含 /skill 条目与新 meta）/skills（无加载态+说明行）/skill 无参与未知名分支/expand 空态/exit 退出码 0 全通过 | ✓ |
| 逻辑正确性 | 关键路径静态审读：flush_text_segment 空段语义；except 分支 continue 前先执行 finally（Python 语义，轮末渲染不丢）；_renderable 切片无负长度（text_tail ≤2 < 8）；complete_next/go_to_completion 不触发 on_completions_changed（钩子无自递归风险）；诊断轮会话缓冲时序 text_segment→tool_start→tool_end→diagnostic→budget 经运行复现正确 | ✓ |

## 四、复审要求

不通过，必须修复全部阻塞项后提请第 r2 轮复审：

- **B1**（cli.py:1178-1183）：_select_first 钩子——修正属性名 `completion_state` → `complete_state`，并按 §1.1 重新实现「仅高亮不落文本」（不得经 complete_next/go_to_completion 改写 buffer 文档）；修复后需在真实终端复核 §1.4 验收点（键入 / 第一条高亮、两段式 Enter、Escape 跳过补全）。
- **B2**（cli.py:1361-1365）：elif 分支去除卡片渲染（至少排除 _terminate 诊断轮与降级轮），使 §4.4 步骤 2「诊断轮步骤 3 自然跳过」与 §4.5「无卡片、无正文重复输出」成立；如测试断言与修复冲突随语义同步修订。
- **B3**（tests/test_skill_command.py）：补「repl 消费后置 None」「当次生效（不污染 system prompt）」两条 §五明文用例，总数不回退。

建议项 S1~S3 建议随本轮一并处理，不作为复审门槛。