# 代码评审报告：V1.1-M1 模式基座（第 2 轮 · 聚焦复审）

> 评审日期：2026-08-31 00:30
> 评审对象：spec `docs/designs/202608301000-plan-v11-m1-mode-base.md`；代码提交 28539bd（相对 a670735 的 3 文件改动）
> 模式：聚焦复审（改动范围：`tests/test_mode_default_build.py` 新增 TestModeBadgeDefaultCopy（+27 行，含 2 导入）、`TODO.md` 追加 r1 建议项登记节（+6 行）、`docs/reviews/202608310000-code-review-v11-m1-mode-base-r1.md` 留痕新增）
> 结论：**通过**（阻塞 0 项，建议 0 项）
> 上一轮：r1 不通过（B1 阻塞 + S1~S3 建议登记 TODO.md 放行）；本轮仅复审 B1 修复改动与波及面，不重评 r1 已通过项。

## 一、阻塞问题

（无）

## 二、建议问题

（无）

## 三、B1 消除核对（复审要求 1）

r1-B1 要求：补齐 spec §2.3 要求的默认徽标文案测试断言（_mode_badge 双态、toolbar 仅模式态）。逐项核对如下：

### 3.1 断言覆盖面与约束力（tests/test_mode_default_build.py:335~352）

| spec §2.3 固化对象 | r1 修复方向要求 | 本轮断言（位置） | 约束力判定 |
|---|---|---|---|
| `renderer._mode_badge` build·自动放行 | 断言 `("build", POLICY_AUTO_APPROVE)` 含「build·自动放行」 | L341：`r._mode_badge("build", POLICY_AUTO_APPROVE).plain == "⬥ build·自动放行"` | ✓ **精确相等**，强于要求的包含断言；文案任何漂移即失败 |
| `renderer._mode_badge` build·每次审批 | 断言 `("build", POLICY_PER_ACTION)` 含「build·每次审批」 | L342：`r._mode_badge("build", POLICY_PER_ACTION).plain == "⬥ build·每次审批"` | ✓ 精确相等 |
| `renderer._mode_badge` ◆ plan | 断言 `("plan", None)` 为「◆ plan」 | L345：`self._renderer()._mode_badge("plan", None).plain == "◆ plan"` | ✓ 精确相等 |
| `toolbar_text` build 仅模式态 | 断言含「⬥ build」且不含 policy 后缀 | L351：`"⬥ build" in line and "自动放行" not in line` | ✓ 正面（徽标在）+ 负面（policy 附注不进 toolbar）双面覆盖；spec §2.3「无 policy 后缀」本质即负断言，not in 为该性质的唯一正确表达 |
| `toolbar_text` plan 仅模式态 | 断言为「◆ plan」 | L352：`"◆ plan" in r.toolbar_text("plan", None)` | ✓ 包含级；toolbar 返回 `[badge | model | ctx]` 整行，精确绑定整行会过度耦合与徽标无关的 ctx 段格式，in 断言聚焦徽标本体，属更优测试设计，不判偏差 |

**结论：B1 已消除。** 断言与实现（`src/glaucous/ui/renderer.py:90~96` 双态三元、`renderer.py:117~128` toolbar 纯模式徽标）逐字对齐，具有真实回归约束力；`_mode_badge` build 双分支（note 三元两取值）与 plan else 分支三条路径全部覆盖。

### 3.2 波及面（复审要求 2：私有方法耦合与新增依赖）

- **私有方法耦合可接受**：`_mode_badge` 为私有方法，但 ① spec §2.3 以 `renderer._mode_badge` 指名要求固化该私有方法行为，测试耦合私有正是 spec 的明确要求；② 项目既有测试惯例中直接调用私有方法已有 20+ 处先例（`tests/test_turn_collapse.py`：`cli._usage_line` L328/330/332、`cli._thinking_line` L341~351、`cli._collapse_enabled` L292~302、`commands._cmd_expand` L265/278；`tests/test_compression_event.py`：`loop._enforce_budget` L76/94/111），本轮写法与惯例一致。公开方法 `toolbar_text` 的断言同时存在，公开契约面亦有覆盖。
- **新增导入无回归风险**：`from rich.console import Console`（L16）与 `from glaucous.ui.renderer import Renderer`（L48）——renderer 导入链（rich 三件套 / context.budget / permission.modes / risk / ui.theme）在 tests 现有用例中均已经由 `glaucous.cli` 导入路径间接加载（本测试文件 L20 已导入 cli），rich 依赖已在 pyproject 声明，无新增三方依赖。`Renderer(Console())` 构造仅赋值 console/model_name/last_budget 三字段（renderer.py:37~40），无终端交互副作用；被测的 `_mode_badge`/`toolbar_text` 均为只读纯函数（不写 console、不依赖 theme 全局注册），测试间无状态泄漏。
- **全量回归**：pytest 全量 **168 passed 1 skipped**（= r1 基线 165 + 新增 3），无回归；目标文件单跑 23 passed（20 + 3）。

### 3.3 修复最小性（复审要求 3：无夹带）

`git show 28539bd --stat` 证据：提交仅含 3 文件——`tests/test_mode_default_build.py`（+27：2 导入 + TestModeBadgeDefaultCopy 类）、`TODO.md`（+6：r1 建议项登记节，S1~S3 三条与 r1 报告原文一致）、r1 报告留痕（+64 新增文件）。**零源码改动**，r1 建议项 S2 指出的 `test_approve_in_build_touches_no_state`（L258~267）未被顺手改动（维持 r1 现状，其偿还归 TODO.md 登记路径），无夹带、无范围蔓延。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | §2.3 徽标文案固化：_mode_badge 双态三断言（精确相等，三路径全覆盖）+ toolbar 单态两面（徽标在 / policy 附注不在） | ✓ |
| Spec 符合性 | 修复最小性：28539bd 仅 3 文件（测试 / TODO 登记 / 报告留痕），零源码改动，无夹带 | ✓ |
| 逻辑正确性 | 波及面：Renderer 无副作用构造、被测方法只读纯函数、rich 依赖既有、导入链无新增；私有方法耦合符合项目既有测试惯例且为 spec 指名要求 | ✓ |
| 逻辑正确性 | 运行验证：`pytest tests/test_mode_default_build.py -q` 23 passed；全量 `pytest tests/ -q` **168 passed 1 skipped**（165 基线 + 3 新增，≥140 守恒） | ✓ |
| 流程合规 | TODO.md r1 建议项登记节（S1~S3）与 r1 报告逐条一致；r2 报告独立落盘不覆盖 r1 | ✓ |

## 五、复审要求

无。B1 已消除，V1.1-M1 交付放行；S1~S3 按 TODO.md 登记偿还，不构成放行前置条件。

## 六、遗留债务台账（非本轮问题，登记备查）

- S1 文案措辞超集式改写（planning.py / cli.py / commands.py）——TODO.md 已登记
- S2 `test_approve_in_build_touches_no_state` 断言空洞——TODO.md 已登记
- S3 「三选一」退役声明残留 5 处注释/docstring——TODO.md 已登记（spec §7.3「随改随清」口径合规）
