# 代码评审报告：V1.1-M1 模式基座（第 1 轮）

> 评审日期：2026-08-31 00:00
> 评审对象：spec `docs/designs/202608301000-plan-v11-m1-mode-base.md`；代码全量（提交 a670735，相对 d4927ac 的 13 个文件改动）
> 模式：全量评审（spec 驱动交付流水线阶段 4）
> 结论：**不通过**（阻塞 1 项，建议 3 项）

## 一、阻塞问题

### B1. spec 2.3 要求的「默认徽标文案测试断言」缺失
- **维度**：Spec 符合性
- **代码位置**：`tests/` 全目录（缺失性证据）。spec 要求固化的对象在 `src/glaucous/ui/renderer.py:90~96`（`_mode_badge` 双态：build·自动放行 / build·每次审批 / plan 徽标）与 `renderer.py:117~128`（`toolbar_text` 仅按 mode 渲染徽标，无 policy 后缀）
- **spec 位置**：§2.3 原文——「`renderer._mode_badge` 已按 mode+policy 双态渲染（build·自动放行 / build·每次审批 / ◆ plan）；`toolbar_text` 仅按 mode 渲染徽标（无 policy 后缀）。默认值翻转后二者自动呈现 build 徽标，**无需改动**；本批仅以测试断言固化默认徽标文案（`_mode_badge` 双态、toolbar 仅模式态）。」
- **冲突/缺陷说明**：全 tests/ 目录检索 `_mode_badge|toolbar_text|prompt_mode` 为 0 匹配；检索徽标文案特征（⬥ / ◆ plan / 自动放行 / 每次审批 / build·）仅命中 `tests/test_mode_default_build.py:102` 一处注释，无任何断言。即本批声明的固化断言（至少 2 条：_mode_badge 双态、toolbar 仅模式态）未交付，新增的 `test_mode_default_build.py`（20 用例）未包含该项。需说明：spec §7.2 的新增用例清单未重复列出此断言，存在 spec 内部张力，但 §2.3 表述明确无歧义（「本批仅以……」），按 spec 唯一功能基准判定为未交付项（代码行为本身经冒烟验证正确，属测试债务级阻塞）。
- **修复方向**：在 `tests/test_mode_default_build.py`（或独立 renderer 测试文件）补两条断言：① `Renderer._mode_badge("build", POLICY_AUTO_APPROVE)` 含「build·自动放行」、`("build", POLICY_PER_ACTION)` 含「build·每次审批」、`("plan", None)` 为「◆ plan」；② `toolbar_text("build", ...)` 含「⬥ build」且不含 policy 后缀、`toolbar_text("plan", ...)` 为「◆ plan」。补齐后提请 r2 聚焦复审。

## 二、建议问题

### S1. 回喂/选项/命令文案与 spec 模板存在措辞级偏差（语义等价）
- **维度**：Spec 符合性（文案级）
- **代码位置与 spec 原文对照**：
  1. `src/glaucous/tools/planning.py:143~147` 实现「用户未批准。{用户反馈：X / 用户未附加反馈。}请根据反馈修订后再次提交或调整方案。」；spec §4.2 模板为「用户未批准，反馈：{feedback或"用户未附加反馈"}。请根据反馈修订后再次提交或调整方案。」
  2. `src/glaucous/cli.py:878` 箭头选项为「批准执行 / 提出修改意见」、`cli.py:389` 选项行为「2️⃣ 提出修改意见」；spec §4.3 为「批准执行 / 修改意见」「2️⃣ 修改意见」。
  3. `src/glaucous/commands.py:47` COMMAND_META /plan 为「切换到 Plan 研究模式（只读，产出分析与建议）」；spec §3.3 为「切换到 Plan 研究模式（只读）」。
- **冲突/缺陷说明**：三处均为措辞偏差，功能语义（反馈回喂、二选交互、命令摘要）与 spec 完全一致，属超集式增补或同义改写，不影响任何行为与测试断言。
- **修复方向**：与 spec 作者二选一——统一代码文案对齐 spec 模板，或随下一批 spec 修订更新措辞为实现版，避免后续复审口径漂移。

### S2. `test_approve_in_build_touches_no_state` 断言空洞（confirm 未接 state）
- **维度**：逻辑正确性（测试有效性）
- **代码位置**：`tests/test_mode_default_build.py:255~264`——`state = SessionState()` 构造后未传入任何被测对象，`confirm=lambda plan: PlanDecision(choice=CHOICE_APPROVE)` 与 `state` 无引用关系，`assert state.mode == ...` 恒真
- **spec 位置**：§7.2「submit_plan 二选：……BUILD 下批准不改状态不发射 mode_changed……」
- **冲突/缺陷说明**：spec 要求的「BUILD 下批准不改状态」未获有效断言（「不发射 mode_changed」已由 `test_plan_approve_switches_and_emits_mode_changed` 的 events2 对照覆盖，L298~312）。因 `SubmitPlanTool` 结构性不持有 state，该断言虽空洞但与实现契约一致，不构成行为缺陷。
- **修复方向**：将 confirm 闭包改为模拟 cli 收敛规则（参照同文件 L273~277 写法：闭包内按 `state.mode` 条件调用 `enter_build()`），使断言真实约束「BUILD 下批准不触发 enter_build」。

### S3. 「三选一」字样以退役声明形式残留于注释/文档字符串
- **维度**：Spec 符合性（残留口径确认）
- **代码位置**：`src/glaucous/permission/modes.py:9`、`src/glaucous/tools/planning.py:4、27`、`src/glaucous/ui/renderer.py:172`、`src/glaucous/agent/loop.py:104`
- **spec 位置**：§7.3「三选一残留验收口径（计划表 M1 验收『代码中无三选一残留引用』）：可运行路径零残留 + 注释/文案级随改随清」
- **冲突/缺陷说明**：可运行路径已零残留（退役常量 CHOICE_KEEP_PLANNING / CHOICE_BUILD_PER_ACTION / CHOICE_BUILD_AUTO_APPROVE 全仓 0 引用，plan_card 已删除）；残留的 5 处「三选一」字样均为 v1.1 退役决策记录（非协议引用），按「随改随清」口径判定合规。登记此条仅为对齐计划表字面「零 token」口径的裁量提示。
- **修复方向**：无需处理；若追求字面零 token，可改写为「旧三选项协议」表述。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | §2.1 modes.py：默认值翻转（mode=MODE_BUILD、policy=POLICY_AUTO_APPROVE，L36~37）、enter_build(policy=None) 签名扩展与维持语义（L40~50）、enter_plan 重命名且不动策略（L52~55）、docstring 状态流转重写（L5~10）；agent/state.py re-export 增 MODE_BUILD/MODE_PLAN | ✓ |
| Spec 符合性 | §2.2 loop.py：自然终止回归分支删除，序列为 终答入史 → _emit_budget() → return（L102~107）；mode_changed 统一出口保留（L138~146，r1-B1 裁决注释在位 L20~22/L135~137）；终止条件与守卫检查点零改动 | ✓ |
| Spec 符合性 | §3.1 approval.py 零改动：gate 守卫优先级（DANGEROUS/区外读永远单独确认）在默认策略下即默认路径（L77~88），审计 auto_approve 保留 | ✓ |
| Spec 符合性 | §3.2/§3.3 commands.py：_cmd_build 三分支（无参维持策略 L191、per-action/auto-approve 显式落位、非法参数报错不改状态 L186~188）、was_build/policy_changed 在 enter_build 前取值（L189~190）、审计附落位后 policy（L197）、重复切换轻提示不重复审计（L193~196）；handle_command 传 rest（L511~512）；/plan 改 enter_plan 与文案（L172~179）；COMMAND_META/_COMMAND_USAGE/HELP_LINES 同步（L45~85）；ARG_COMPLETIONS "/build":"policy" 与补全分支（cli.py L114、L1110~1114：候选两合法参数、前缀过滤、display_meta「授权策略」） | ✓ |
| Spec 符合性 | §4.1~§4.3 planning.py/cli.py：PlanDecision 二选（L32~41）、三选一 CHOICE_* 退役且全仓 0 引用、modes=ALL_MODES（L86，决策 D-2）、批准回喂不含切换附言（L137~141，r1-B2 方案 c）、锚行机制原样保留（L92~135；history.py ANCHOR_TOOL_NAME L32 未动）；prompt_plan_decision 二选化 + EOF→feedback（cli.py L376~405）；confirm 状态切换收敛唯一规则 CHOICE_APPROVE and mode==MODE_PLAN（cli.py L888~889），旧两处按选项落位 enter_build 已移除（diff 证据）；pause/resume/flush_text_segment/live_hooks 时序原样（L874/L890/L896/L899） | ✓ |
| Spec 符合性 | §4.4 base.py：Plan 下仅 Build 工具 hint 新文案（L199~203）、Plan 写拦截尾句新文案（L242~243）、工具可见性机制零改动；L57 注释更新为全模式可用 | ✓ |
| Spec 符合性 | §5.2 prompts.py：会话模式段重写（L30~34，无「自动回到 Plan」句）、先澄清后开发段（L36~40，含 r1-S9 适配注记 L7~8）、高风险主动确认段（L42~44）；build_system_prompt 签名未动（L67） | ✓ |
| Spec 符合性 | §六 错误处理表 6 行逐条核对全部成立（非法参数 / 重复切换轻提示不重复审计 / BUILD 下批准幂等不发射 / EOF-Ctrl+C-Esc 归 feedback / Plan 幻觉声明层+执行层双保险 / 默认策略守卫优先级） | ✓ |
| Spec 符合性 | §7.1 修订既有 4 处全部落地（enter_plan 断言含 policy 不变；enter_build(None) 维持策略；guard 显式构造 + 新增默认构造断言 TestDefaultConstruction；policy 补全 3 用例）；§7.2 新增用例覆盖 spec 清单全部条目（B1 徽标断言除外）；test_turn_collapse / test_compression_event 的 SessionState() 裸构造逐一核对，无 plan 徽标/策略文案隐含断言，无需调整 | ✓ |
| Spec 符合性 | §7.3 三选一清理清单 5 项逐项完成：plan_card 死方法删除（renderer.py L172~173 注释在位）、planning.py 模块与工具 docstring 重写、base.py L57 注释、cli.py Banner 文案（L90）、cli.py resume docstring（L981） | ✓ |
| Spec 符合性 | 范围控制：diff 13 文件均在 spec §八 清单内，无 spec 之外功能；两项显式裁剪（策略持久化面 TODO.md 登记、自然终止 mode_changed 发射退役）为 spec 声明边界，不计缺陷 | ✓ |
| 逻辑正确性 | pytest：**165 passed 1 skipped**（不低于 140 基线守恒；净增 25 用例）。环境注记：本机未安装 glaucous 包，需 PYTHONPATH=src 方可收集（仓库约定 editable 安装），属环境配置非代码缺陷 | ✓ |
| 逻辑正确性 | 管道冒烟（临时工作区执行）：Banner 徽标「模式 build·auto」；/help 含 /build [auto-approve|per-action] 新文案；/plan 后提示符切为 plan；/build 提示「已进入 Build 模式（自动放行 + 底线守卫）」且回 build·auto；/build auto-approve 轻提示「已处于 Build 模式，授权策略不变」；/build nonsense 报用法且提示符不变（状态未动）；/expand 空态提示；/exit 退出码 0 | ✓ |
| 逻辑正确性 | 关键路径静态审读：PLAN 下批准经 confirm 切模式后由 loop 比对快照发射 mode_changed（policy=维持值），BUILD 下批准比对为假不发射（有测试）；同轮 mode 快照保证切换轮后续幻觉写调用仍按 Plan 拦截；PLAN 下 bash SAFE 白名单（SAFE 无审批动作放行）、区外读走 gate 单独审批、写工具声明层过滤 + 执行层拦截双保险均未回退；F1~F4 回归（两段式 Enter 补全、/skill pending_task 消费、会话级思考缓冲仅 /clear 与 /resume 清空、/expand 全会话重放）测试全绿 | ✓ |

## 四、复审要求

- **B1**（必须）：补齐 spec §2.3 要求的默认徽标文案测试断言（_mode_badge 双态、toolbar 仅模式态），随后做 r2 聚焦复审（范围仅限新增断言及其波及面）。
- S1~S3 为建议项，可与 B1 修复顺带处理，不作为放行前置条件。
