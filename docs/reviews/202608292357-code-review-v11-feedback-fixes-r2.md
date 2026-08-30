# 代码评审报告：v1.1 验收反馈修复批次 F1~F4（第 r2 轮）

> 评审日期：2026-08-29 23:57
> 评审对象：spec `docs/designs/202608292200-plan-v11-feedback-fixes.md`（v1.1 已批准）；代码提交 1afe913（相对 r1 基线 90e6824）
> 模式：聚焦复审（改动范围：src/glaucous/cli.py、tests/test_skill_command.py、TODO.md；r1 阻塞项 B1~B3 修复核验 + 夹带检查）
> 上轮报告：docs/reviews/202608292334-code-review-v11-feedback-fixes-r1.md（不通过，B1~B3 + S1~S3）
> 结论：**通过**（阻塞 0 项，建议 0 项）

## 一、阻塞问题

无。

## 二、建议问题

无。

## 三、通过项

### 3.1 r1-B1 修复核验：默认选中第一条钩子（cli.py:1177-1189）✓

**Spec 依据**：§1.1「补全菜单弹出时默认选中第一条候选……自动选中仅改变高亮、不把候选文本落入输入行（与 1.2 两段式不冲突）」；§1.4「键入 / → 菜单弹出且第一条高亮」。

**静态验证**（prompt_toolkit 3.0.51 源码）：
- `Event.__call__` 存在（utils.py:75-78，docstring 明示 `obj.event()` 即 fire 语法糖）——`buffer.on_completions_changed()` 直接调用合法；fire 时 handler 收到构造期 sender（Buffer），修复版签名 `_event=None` 收下不用、改闭包读 `session.default_buffer.complete_state`——r1 的 `completion_state` AttributeError 根因根除；
- `CompletionState.complete_index` 为普通实例属性（buffer.py:86），可直接赋值；框架自身 `go_to_index` 亦直接赋值该属性（buffer.py:99），同型操作有内部先例；
- 置位路径不触碰 `new_text_and_position`/`set_document`（buffer.py:101-119 仅在 go_to_completion 落文本路径调用）——「仅高亮不落文本」成立；
- 全包 grep `on_completions_changed +=` 零命中：除产品钩子外无其他订阅者，重发 fire 仅自触发；置位后 `complete_index is None` 不再成立——嵌套空转一次即返回，无递归（fire 遍历为索引迭代器，嵌套迭代安全）；
- 置 index=0 后 `current_completion` property（buffer.py:121-129）返回第一条候选，与 `_two_stage_enter`（cli.py:1147-1161，本轮未触碰）的 apply_completion 语义正确衔接：Enter 落行不执行、再 Enter 执行（§1.2/§1.4）。

**运行验证**（Buffer 全链路复刻钩子 + 真实 `make_repl_completer`，经 `start_completion` 异步补全）：

```
P1 state: idx=0 comps=['/view']   text: '/vi'（未落行）  current_completion: /view
P1 calls: {'n': 2, 'set': 1}      <- 初次 fire 置位 + 重发空转，有限次
P2 state: idx=0（/vie 前缀刷新后仍选中第一条，§1.1 跟随过滤）
P3 after cancel: None（Escape 后钩子空转无异常）
RESULT: PASS；stderr 无任务异常
```

真实终端交互观感（菜单高亮渲染）依赖 prompt_toolkit 运行时，属 spec §1.2 已声明的自动化边界，仍列入用户终端验收清单。

### 3.2 r1-B2 修复核验：轮末 finally 分支收紧（cli.py:1363-1389）✓

**Spec 依据**：§4.5「轮末仅打印用量行（无折叠行、无卡片、无正文重复输出）」；§4.4 步骤 2「_terminate 轮……步骤 3 因正文缓冲为空自然跳过」。

修复后三分支：`if body:`（折叠轮缓冲一次性输出 + 卡片，cli.py:1370-1376）→ `elif ctx.stream_state["printed"]:` 仅 `console.print()` 收尾换行（cli.py:1377-1381，无 answer 判定、无卡片渲染）→ `else:` 异常轮 flush 落账不呈现（cli.py:1382-1384）。

- r1 场景 A（GLAUCOUS_COLLAPSE=off 降级轮）与场景 C（Live 启动失败降级）：正文已逐字直打 → elif 命中仅收尾换行——「无卡片、无正文重复输出」成立（收尾换行为 r1 修复方向明示允许）；
- r1 场景 B（折叠激活的 _terminate 诊断轮）：中间步正文经 tool_start flush 落账、缓冲空 → if 不命中；elif 仅空行 → 步骤 3 的正文与卡片跳过，诊断仅经 diagnostic 行交付——「自然跳过」成立；
- `render_answer_card` 全文件唯一调用点收敛至 `if body:` 分支（cli.py:1376，import 于 75 行）；
- 既有测试无冲突保护断言（test_turn_collapse.py 轮末用例针对 on_event/flush 层），全量 140 passed 佐证。

### 3.3 r1-B3 修复核验：pending_task 消费可测化 + §五两条明文用例（cli.py:1208-1215 / tests/test_skill_command.py:109-129）✓

**Spec 依据**：§五「repl 消费后置 None」「当次生效（不污染 system prompt）」。

- 新增顶层 `consume_pending_task(ctx)`（取出并置 None，返回任务），repl 斜杠分派后改用该函数（cli.py:1340）——与原内联元组赋值语义完全等价，仅可测化抽取；
- `TestPendingTaskConsumption::test_consume_returns_task_and_resets`：消费返回任务、置 None、二次消费返回 None（不重复驱动）——覆盖「repl 消费后置 None」；
- `test_skill_command_does_not_touch_system_prompt`：`_cmd_skill` 后 system_prompt 原样、SKILL_BODY 仅存在于 pending_task——覆盖「当次生效（不污染 system prompt）」；与 `_cmd_skill` 实现（commands.py:382-404，仅写 ctx.pending_task）一致；
- 专项运行：tests/test_skill_command.py 12 passed。

### 3.4 范围控制与登记 ✓

- `git diff --stat 90e6824..1afe913`：仅 cli.py（钩子重写 / consume_pending_task 及调用点 / elif 分支共三处）、tests/test_skill_command.py（新增测试类）、TODO.md（登记）+ r1 报告留痕——无夹带改动，修复内容与声明清单逐项对应；
- TODO.md 已登记 S1（make_on_event ws 参数）、S3（/skills 条目排版）、spec §〇范围裁剪偿还项（FR-31 状态栏、思考跨 /stop 落盘、/skill Tab 补全）及 S2 说明（M3 既有 API 不在本批范围）——S1~S3 按流程放行，符合「登记即偿还」约定。

### 3.5 运行验证汇总 ✓

| 验证项 | 结果 |
|---|---|
| `pytest tests/ -q`（PYTHONPATH=src） | **140 passed, 1 skipped**（r1 为 138 + B3 新增 2；≥115 基线守恒） |
| B1 复刻钩子全链路运行 | PASS（idx=0 / 不落行 / 无递归 / 无异常） |
| B3 专项 `pytest tests/test_skill_command.py -v` | 12 passed |
| 管道冒烟 /help /skills /skill（无参+未知名）/expand 空态 /exit | 全通过，退出码 0 |

## 四、复审要求

无。r1 三项阻塞（B1/B2/B3）均已按修复方向落实并经静态 + 运行双重验证；建议项 S1~S3 已按流程登记 TODO.md。§1.4 交互验收点（默认选中高亮、两段式 Enter、Escape 跳过）仍按 spec §1.2 自动化边界声明留待用户真实终端复核，不属于代码评审门槛。
