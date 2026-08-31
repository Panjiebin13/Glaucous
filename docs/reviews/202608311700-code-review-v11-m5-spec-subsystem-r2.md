# 代码评审报告：V1.1-M5 Spec 子系统 Spec（第 r2 轮）

> 评审日期：2026-08-31 18:05
> 评审对象：spec `docs/designs/202608311500-plan-v11-m5-spec-subsystem.md`（已含 §九 实现对齐注记）；代码聚焦本轮改动：`src/glaucous/spec/pipeline.py`、`src/glaucous/spec/store.py`、`src/glaucous/commands.py`、`src/glaucous/ui/prompts.py`、`tests/test_spec_pipeline.py`、`tests/test_spec_store.py`
> 模式：聚焦复审（改动范围：r1 修复点 B1~B4、S1~S6 + spec §九.6 健壮性登记项及其波及面）
> 结论：**通过**（阻塞 0 项，建议 4 项）

## 一、阻塞问题

无。

## 二、建议问题

### S1. `test_handle_command_dispatch` 对 `/specs` 的第二次调用无断言
- **位置**：tests/test_spec_pipeline.py:586-587 `asyncio.run(handle_command("/specs", ctx))` 后无用例断言
- **说明**：`/specs` 空列表提示已由 `test_specs_empty_hint` 独立覆盖，此处仅验证分派可达不抛异常；建议补「尚无 Spec」提示断言或与前者合并，避免「半断言」用例观感。不构成功能缺口。

### S2. cancel 异常兜底路径无专属回归用例
- **位置**：src/glaucous/spec/pipeline.py:155-169（B2 修复点）
- **说明**：本轮以动态脚本验证了兜底有效（将文档预置 archived 后调 cancel：非法迁移异常被捕获、经 renderer.error 提示、不击穿，输出「取消失败（…），Spec 状态不变」），但测试套件无对应用例固化；建议补一例「终态文档 cancel → errors 非空且状态不变」防回归。spec §七 未强制该项，故仅建议。

### S3. r1-S6 残留细枝末节：`_diff_summary` 的 `if not seq`
- **位置**：src/glaucous/spec/pipeline.py:487 `if not seq or store is None or not store.available:`
- **说明**：r1-S6 含两子项，本轮修复了 `_goal_of` 半角冒号截断（有 TestGoalParsing 三例），`_diff_summary` 将 seq==0 一并视为基线缺失未动；实际 checkpoint seq 自 1 起，无实害（r1 已注明），维持建议级，可在后续顺手改为 `seq is None`。

### S4. `_strip_fences` 纯函数无直接用例
- **位置**：src/glaucous/spec/pipeline.py:542-553（spec §九.6 登记的健壮性项）
- **说明**：起草/修订/补写三处消费点均经现有全流程用例间接走到，但「首尾围栏剥离、正文内围栏不动」的边界行为无直接断言；该函数为纯函数，补 2~3 例（带语言标记的开栏/无闭栏/正文含围栏）成本极低。spec §七 未要求，故仅建议。

## 三、通过项（r1 问题逐项销项判定）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| r1-B1 销项 | TestCommandRouting 9 例（test_spec_pipeline.py:469-586）：/spec 发起路由、/specs 空提示、无活跃用法提示、/spec cancel 无活跃/有活跃路由、/spec status 卡、无参 executing→ask_continue、非 executing→续跑提示、handle_command 分派；替身经 monkeypatch 打桩 `glaucous.spec.pipeline.SpecPipeline`，因 `_cmd_spec` 为函数体内惰性导入，桩在调用时生效（用例实跑通过验证）；§七 末段「四路由 + 无活跃提示」全覆盖 | ✓ |
| r1-B2 销项 | pipeline.py:155-169 `cancel()` 增与 start/resume 同款顶层捕获（KeyboardInterrupt/Exception → renderer.error「取消失败…状态不变」）；动态验证：预置 archived 文档后 cancel，异常被捕获、无 traceback、errors 非空；符合决策 12 与 §五「非法迁移报错不击穿」 | ✓ |
| r1-B3 销项 | pipeline.py:520-534 `_acceptance` 改保守口径：`all_ok = metadata.get("ok") and criteria and not failed and len(checked) >= len(criteria)`，与 spec §九.1 作者裁决字面一致；结论落 frontmatter `acceptance`（store.py:131-132 `_render_frontmatter` keys 增列，经 transition 落盘）；未决归档注记「核验未完成（契约违约或子任务失败）」；新增两用例（契约违约/部分核验）实跑通过且断言 `acceptance==存在未决`、正文含「契约违约」 | ✓ |
| r1-B4 销项 | prompts.py:40 改为「大任务建议用户使用 /spec 发起 Spec 流程（v1.1-M5 已提供，见下方「Spec 流程」段）」，与 71-73 行新增段一致；全仓 grep「后续版本提供/方案确认卡替代」零命中，矛盾句已清除 | ✓ |
| r1-S1 关闭 | pipeline.py:316-323：Spec 评审失败重试结果参与判定（通过直进批准链；不通过提示后呈批避免拉锯），与代码评审侧（437-450 重试重新判定）语义对齐；代码注释显式引用作者裁决，符合 §九.2 | ✓ |
| r1-S2 关闭 | pipeline.py:264-266 建议收集移出 `round_no < MAX` 分支，每轮报告卡后均收（含第 3 轮）；第 3 轮所收建议经 275 行「再修订一轮」的 `_revise_turn(doc, feedback)` 注入，符合 §九.4 与 §4.3 字面 | ✓ |
| r1-S3 关闭 | pipeline.py:171-178 新增公开方法 `ask_continue`（继续→resume/取消→cancel/None→停驻，决策 13）；commands.py:1112 改为消费该方法；grep 确认 commands.py 已无 `pipeline._hooks` 跨层访问（余下 `live_hooks` 为 REPL 既有协议，无关） | ✓ |
| r1-S4 关闭 | TestEscalationExtraRound：Spec 评审「再修订一轮（仅一次）」→ 修订+加轮评审→批准链（reviews 队列 FAIL×4+PASS+ACC 与 turns 队列逐项核对与实现流程吻合）；代码评审「再修复一轮」后追加复审轮（433 行队列位与 §九.3 一致）；用例 7 `test_revise_then_pass` 改为真实两轮不过第三轮过（以「评审检查清单（Spec 评审）」标题计数==3，模板字面核对成立） | ✓ |
| r1-S5 关闭 | commands.py:1068-1070 `_spec_status_lines` 状态行增「验收：{acceptance}」，数据源为 frontmatter `acceptance` 字段（B3 落盘同源），/spec status 与无参进度卡共用；终态回退展示（1086 行）亦可见，符合 §3.5 | ✓ |
| r1-S6 关闭（主体） | pipeline.py:209-221 `_goal_of` 从「澄清完成」标记后截取、只切第一个冒号（全角优先）；TestGoalParsing 三例（内嵌半角冒号/无冒号/回退）实跑通过；残留子项 `_diff_summary` 见建议 S3 | ✓ |
| §九.6 健壮性 | `_strip_fences`（pipeline.py:542-553）用于起草/补写/修订三处；`list_all` 空文件/非 Spec 判损跳过（store.py:273-274）并有 test_spec_store.py:146-150 用例（空文件告警+排除）；均与登记注记一致 | ✓ |
| 波及面静态审读 | 新增/改动未破坏既有不变量：`acceptance` 仅在 `_acceptance` 写入、经 transition 落盘，旧文档缺省空串不影响状态卡渲染（1070 行判空）；`ask_continue` 内 resume/cancel 各自带顶层捕获，None 停驻（决策 13）；深度介入建议收集外移不改变耗尽升级三选与「再修订仅一次」结构；`_strip_fences` 仅剥首尾、不触碰正文内围栏；无新增范围蔓延 | ✓ |
| 运行验证 | 子系统两文件 49 passed（2.85s，与送评声明「新增 49」一致）；全量 319 passed / 2 failed / 1 skipped——2 个失败为 r1 已定性的 M4 既有 `test_checkpoint_git.py` Windows 环境因子（CJK 文件名 GBK 解码、锁文件 unlink），本批未触碰该两文件，基线口径为 WSL（送评声明 322 passed 与此吻合：319+2+1=322 收集数）；冒烟 `import glaucous` 与 `python -m glaucous --help` 正常；cancel 兜底动态验证通过 | ✓ |

## 四、复审要求

无。r1 阻塞项 B1~B4 全部消除、建议项 S1~S6 全部关闭（S6 残留一无实害细枝末节，降为建议 S3 随后续处理）；本轮未发现新阻塞。建议 S1~S4 可在后续批次自愿销项，不阻断本里程碑收口。
