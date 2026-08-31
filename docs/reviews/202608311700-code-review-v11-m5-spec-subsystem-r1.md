# 代码评审报告：V1.1-M5 Spec 子系统 Spec（第 r1 轮）

> 评审日期：2026-08-31 17:00
> 评审对象：spec `docs/designs/202608311500-plan-v11-m5-spec-subsystem.md`；代码本批全量改动（新增 src/glaucous/spec/ 包、tools/spec_tool.py、tests/test_spec_store.py、tests/test_spec_pipeline.py；修改 commands.py、cli.py、ui/prompts.py）
> 模式：全量评审
> 结论：**不通过**（阻塞 4 项，建议 6 项）

## 一、阻塞问题

### B1. spec §七 明确要求的命令层接线测试整体缺失
- **维度**：Spec 符合性
- **代码位置**：tests/ 全目录——经检索不存在任何经 handle_command 分派 /spec、/specs、/spec status、/spec cancel 并断言路由与「无活跃 Spec 提示」的测试
- **spec 位置**：§七 末段「命令层接线测试（handle_command 分派到 pipeline 的替身，断言 /spec、/specs、/spec status、/spec cancel 路由与无活跃 Spec 提示）」
- **冲突说明**：本批仅新增 test_spec_store.py（15 例）与 test_spec_pipeline.py（18 例），命令层接线测试未落实；tests/test_sessions_commands.py 已有 handle_command 测试范式可直接沿用
- **修复方向**：补命令层路由测试（monkeypatch 替身断言四条路由可达 + 无活跃 Spec 提示文案）

### B2. cancel 入口缺决策 12 顶层捕获，SpecStateError 可击穿 REPL
- **维度**：Spec 符合性（错误处理策略）
- **代码位置**：src/glaucous/spec/pipeline.py:155-165 `async def cancel()` 无 try/except；cli.py:1671 handle_command 调用处无捕获；cli.py:1783-1786 main 仅捕获 KeyboardInterrupt
- **spec 位置**：§3.3「顶层捕获 KeyboardInterrupt 与 Exception → 状态停驻 + renderer.error 恢复提示（决策 12）」；§五 错误处理表「非法状态迁移（并发/手工改文件）→ SpecStateError → 当前操作报错提示」；决策 12「不击穿 REPL」
- **冲突说明**：cancel 路径的 store.transition(doc, archived) 在非法迁移（如 frontmatter 被并发改至终态）时抛 SpecStateError，异常经 _cmd_spec → handle_command 无拦截，以 traceback 终止会话；start/resume 均有顶层捕获，唯 cancel 缺失
- **修复方向**：为 cancel 增加与 start/resume 同形的顶层捕获并经 renderer.error 提示；或在 _cmd_spec 层统一捕获 SpecStateError 转报错提示

### B3. 验收裁决实现为「无 ✗」而非「全部 ✓」，契约违约（含空报告）也判 verified【提请作者确认】
- **维度**：逻辑正确性
- **代码位置**：src/glaucous/spec/pipeline.py:489-492 `failed = [line ... startswith(✗)]`；`if metadata.get(ok) and criteria and not failed: transition(doc, verified)`
- **spec 位置**：决策 11「逐条 ✓/✗；全部 ✓ → verified，存在 ✗ → archived」；决策 5 保守原则（契约解析失败 → 保守判不通过）
- **冲突说明**：验收子 agent 违约时（空报告、无 ✓/✗ 行的自由文本、漏核验部分标准），failed 为空 → 判 verified。「无 ✗」与「全部 ✓」在契约违约下不等价；评审环节对同类违约保守判不通过，验收环节却反向升格为最高通过裁决，两处口径不一致
- **修复方向**：verified 要求「每条验收标准均有对应 ✓ 行」（✓ 行数 >= 标准条数且无 ✗）；不满足（含契约违约）→ 记未决归档。若作者确认现口径即为目标，请在决策记录中显式登记

### B4. BASE_PROMPT 旧句与新增 Spec 建议段自相矛盾
- **维度**：Spec 符合性（决策 10 / FR-52）
- **代码位置**：src/glaucous/ui/prompts.py:40「大任务建议用户使用 Spec 流程（后续版本提供，当前以方案确认卡替代）」与本批新增 71-73 行「可建议用户以 /spec 发起 Spec 流程」同处 BASE_PROMPT
- **spec 位置**：决策 10「FR-52 agent 亦可主动建议 = 提示词层：BASE_PROMPT 增一句大任务可建议用户以 /spec 发起」
- **冲突说明**：新增句已落实，但同提示词内旧句「后续版本提供，当前以方案确认卡替代」在 M5 落地后成为错误陈述并与新句直接冲突；该提示词是 FR-52 主动建议的唯一实现通道（决策 10 明示不做代码级触发），矛盾将导致模型行为不确定
- **修复方向**：将第 40 行改为指向 /spec 的表述（如「大任务可建议用户以 /spec 发起」），删除「后续版本提供」字样

## 二、建议问题

### S1. 评审子任务失败「重试本轮」语义两端不一致【提请作者确认】
- **位置**：pipeline.py:296-299（Spec 评审）与 pipeline.py:413-423（代码评审）
- **说明**：Spec 评审的重试忽略重试结果、无条件进入批准链（重试仍不通过也不再走修订回环）；代码评审侧重试则重新判定结果且不耗轮次。§五 未细化重试语义，建议统一口径并在代码注释或决策记录中说明

### S2. 深度介入第 3 轮未通过时不收用户建议
- **位置**：pipeline.py:244-248
- **说明**：建议收集仅在「不通过且 round 小于 3」时发生；第 3 轮未通过直接进入耗尽 ask，且「再修订一轮」复用上一轮旧 feedback。§4.3 字面为「每轮报告卡后 ask 收一句建议」，边界轮行为建议补齐或注释说明

### S3. 命令层跨层访问 pipeline 私有成员
- **位置**：commands.py:1111 pipeline._hooks.ask(如何处理？, ...)
- **说明**：/spec 无参 executing 分支直接消费测试注入点 _hooks；建议把「继续执行/取消归档」的 ask 决策收敛为 SpecPipeline 的公开方法（如 prompt_resume）

### S4. §七 用例点覆盖缺口
- **位置**：tests/test_spec_pipeline.py
- **说明**：§七-8① 要求断言「再修订仅一次」，现有用例仅断言耗尽 ask 问题出现；评审耗尽的「再修订一轮（仅一次）」分支与代码评审「再修复一轮（仅一次）」分支均无用例覆盖；用例 7 字面为「两轮不通过后第三轮通过」，test_revise_then_pass 实为 1 轮不通过第 2 轮通过，建议补足

### S5. 状态卡与验收归档的呈现粒度
- **位置**：commands.py:1062-1074（_spec_status_lines）；pipeline.py:495（验收归档注记）
- **说明**：§3.5 要求 /spec status 卡含「（若 code_review 后）验收核验结果」，状态卡未含验收结果（无活跃时回退展示最新终态文档时亦不可见）；§4.5.4 字面「验收报告追加至风险与回退尾」，实现为未决项前 5 条摘要（未决信息已足够，口径可再对齐）

### S6. 细枝末节
- **位置**：pipeline.py:200（_goal_of）；pipeline.py:459（_diff_summary）
- **说明**：_goal_of 先按全角冒号切分后再按半角冒号切分，目标行含半角冒号时前缀被截掉；_diff_summary 的 `if not seq` 将 seq==0 一并视为基线缺失（实际 seq 自 1 起，无实害）

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | §2.2 TRANSITIONS 与迁移表逐条一致（含 reviewing→draft 保留边、双终态无出边）；修订回环不迁移状态仅 round 自增 | ✓ |
| Spec 符合性 | §2.1 frontmatter 九字段、类型归一化、损坏行跳过与缺 status→draft 容错 | ✓ |
| Spec 符合性 | §四流程细则：澄清 ≤3 轮门与耗尽升级；起草缺节补写一次、仍缺注记不阻断；评审 ≤3 轮 + 深度介入 + 耗尽三选（再修订仅一次分支存在）；批准反馈修订 ≤3 轮 + 耗尽两选 | ✓ |
| Spec 符合性 | 执行期双保险（决策 2）：每任务 loop.run 前 hooks.checkpoint 权威快照 + 入口基线 1 次；重试不重复打快照（测试以调用计数 3 = 1 + 2 断言）；跳过项经 append_note 入风险节 | ✓ |
| Spec 符合性 | §4.5 代码评审经 store.get(基线 seq) + preview_changes 取 diff（不直调 git_snapshots.diff_against，r1-S11 落实）；摘要 ≤4000 字截断注记；非 Git / 基线淘汰双降级路径（决策 3/8） | ✓ |
| Spec 符合性 | §五错误策略：决策 13 None 语义五处映射（澄清→取消、模式→全自动、建议→空、批准→空意见归档、任务失败→归档）；决策 6 截断回读（文件名与 spawn_agent-<id>.log 契约一致，归档缺失用截断文本）；决策 5 契约解析失败保守判不通过 | ✓ |
| Spec 符合性 | §六边界：评审/验收经 ctx.subagent_runner 直调、报告不以工具结果入父史（卡片呈现）；pipeline 不置位 turn_active；read_spec 仅主 registry 注册、子 registry 派生继承（r1-S9）；SLASH_COMMANDS 零引用核实，未登记 /spec（r2-S3） | ✓ |
| Spec 符合性 | §3.4 ReadSpecTool：SAFE/全模式、缺省最新活跃否则最新任意状态、spec_id 经 sanitize_call_id 净化；决策 14「不得调用 ask_user」入两套契约；决策 7 runner 经 rebuild_loop 挂账（/clear、/resume 重建后仍有效）；决策 16 ask 同步回调薄包装 | ✓ |
| 逻辑正确性 | 异步链完整：hooks 均声明 Awaitable，调用点全部 await，无悬空协程；for-else 耗尽分支与嵌套重试 while 的出环条件审读无误 | ✓ |
| 逻辑正确性 | 文件原子写（tmp + os.replace，tmp 后缀不混入 *.md glob）；frontmatter 围栏未闭合容错；append_note 节尾插入（无节则补建）；check_task 按全文第 N 个 checkbox 定位、重复勾选幂等 | ✓ |
| 运行验证 | 本批复核（Windows，PYTHONPATH=src）：tests/test_spec_store.py + test_spec_pipeline.py 共 33 passed（基线 273 + 33 = 306 与送评声明一致）；全量 303 passed、1 skipped（既有 symlink 权限跳过） | ✓ |
| 运行验证 | 全量中 2 个失败均为 M4 既有 test_checkpoint_git.py 用例（CJK 文件名 git 子进程 GBK 解码、锁文件 unlink 语义），失败因子为 Windows 环境且该两文件本批未改动；spec 声明基线环境为 WSL，不计本批问题 | ✓（附注） |
| 运行验证 | 断言有效性抽查：33 个新增用例断言指向状态、frontmatter 字段、checkpoint 调用计数与标签、文档正文内容、ask 问题清单，均为可失败断言，未见恒真断言；FakeHooks 缺省取首项行为不掩盖关键门（关键门均脚本化指定） | ✓ |

## 四、复审要求

必须修复的阻塞项：B1、B2、B3、B4。
