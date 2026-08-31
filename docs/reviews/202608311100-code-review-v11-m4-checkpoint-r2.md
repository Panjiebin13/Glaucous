# 代码评审报告：V1.1-M4 Checkpoint Spec（第 2 轮）

> 评审日期：2026-08-31 02:45
> 评审对象：spec `docs/designs/202608310900-plan-v11-m4-checkpoint.md`（含 S1 处置修订、决策 1 add+reset 细节更新）；聚焦代码：src/glaucous/checkpoint/{git_snapshots,store}.py、agent/loop.py、cli.py、commands.py、tests/test_checkpoint_git.py、tests/test_rollback_context.py

> 模式：聚焦复审（改动范围：r1 B1~B3 修复 + S1~S6 处置及其波及面——调用方、被调用方、共享数据结构、新增测试）
> 运行验证：WSL `pytest tests/ -q` → 262 passed in 5.04s（基线 239 守恒 + 23 新增，与 r1 的 256+6 吻合）；另以 WSL python 加载项目真实代码完成 4 项边界实证（索引错型条目、excludes 有效性、B2 回归用例效力、同仓库多实例暴露面），临时脚本置于系统 TEMP，未修改仓库任何文件
> 结论：**不通过**（阻塞 2 项，建议 3 项）

## 一、阻塞问题

### B4. store.create 的索引重写对损坏条目自身崩溃：_dump(None) 必现 AttributeError，S3「create 索引重写过滤损坏条目」未达成，checkpoint 设施此后永久失效
- **维度**：逻辑正确性（修复引入的必现异常路径）/ Spec 符合性（S3 处置未落实）
- **代码位置**：src/glaucous/checkpoint/store.py:186-190

```python
index["checkpoints"] = [
    dumped
    for e in index["checkpoints"]
    if (dumped := self._dump(self._load_cp(e))) is not None
]
```

`_load_cp`（store.py:132-148）契约「形状不符 → 返回 None」；`_dump`（store.py:120-130）直接解引用 `cp.seq`。条件表达式先求值 `self._dump(self._load_cp(e))` 再与 None 比较——索引含错型条目（非 dict，或字段错型如 `{"seq":"x"}`）时 `_load_cp` 返回 None，`_dump(None)` 即抛 `AttributeError: 'NoneType' object has no attribute 'seq'`，被 create 的宽化 except（store.py:205，S2 处置）吞掉转审计后返回 None。
- **spec 位置**：§3.2「def create(...) -> Checkpoint | None # 失败返回 None」；§五「索引损坏/缺失 → 空索引重新起步」；本轮 S3 处置声明「list()/create 索引重写均过滤损坏条目」。

- **冲突/缺陷说明**：WSL 实证（加载项目真实代码）：索引写为合法 JSON `{"checkpoints":[1]}`（_load_index 的 list 校验放行）后调用 create——create#1/#2 均返回 None，审计 `{"event":"checkpoint_create","ok":false,"error":"'NoneType' object has no attribute 'seq'"}`，而同场景 `list()` 正常返回 []。①损坏条目未被过滤，过滤代码自身崩溃；②失败发生在 `_save_index` 之前，索引永不修复，此后每轮 create 均失败（快照 commit/ref 每次白建、seq 反复覆盖同号）——checkpoint 在该工作区静默永久失效，仅 /rollback 尚可用，与 S3「损坏条目不致命」的意图相反；③现有测试仅覆盖「非法 JSON」起步（test_index_corruption_starts_fresh），「合法 JSON + 错型条目 + create」组合零覆盖。

- **修复方向**：先过滤后 dump——`cps = [cp for e in index["checkpoints"] if (cp := self._load_cp(e)) is not None]` 之后再逐条 `_dump(cp)`；并补「合法 JSON + 错型条目 + create」组合回归用例（断言 create 成功且索引中损坏条目被清除）。

### B5. 决策 5 排除不变量仍只覆盖「仓库根级 + 当前工作区级」两级：其余层级的 .glaucous（同仓库其他实例运行时目录、中间层级）仍进快照与回退面，回退将删除其会话/索引文件并回卷其审计——与代码注释自述「任意层级」直接矛盾（修复归属提请作者确认）
- **维度**：Spec 符合性（决策 5 不变量）/ 逻辑正确性（破坏性静默失败）

- **代码位置**：src/glaucous/checkpoint/store.py:85-97（`_excludes` 仅产出根级 `.glaucous` + workspace 相对路径一级，注释却称「决策 5『审计失真』防护对任意层级生效」）；git_snapshots.py:76-78（`_excluded` 注释称「B2：任意层级 .glaucous/ 均不进回退面」）、117-152（diff 排除与 A 项过滤）、155-159（restore 排除）。
- **spec 位置**：决策 5「快照排除 `.glaucous/`：运行时目录（会话/审计/索引/输出）不进快照——防止审计与会话文件进入回退面（回退审计日志 = 审计失真）」；r1-B2 修复方向①（`:(glob)**/.glaucous` 任意层级 + 按路径段判断）。

- **冲突/缺陷说明**：WSL 实证（无 .gitignore，与 r1-B2 复现口径一致）：workspace=仓库根，`sub/.glaucous/` 为同仓库另一 glaucous 实例的运行时目录——①其 audit.log/checkpoints.json 进入快照树（ls-tree 实证为 True）；②checkpoint 之后该实例新增会话文件、追加审计，根 workspace 执行 /rollback：`sub/.glaucous/session_new.jsonl` 作为 A 项**被删除**、`sub/.glaucous/audit.log` 被还原为快照时刻内容（**审计失真**，恰是决策 5 立条要防的后果）、checkpoints.json 列入 M/D 被还原。决策 5 不变量对「非根级且非当前工作区级」的运行时目录不成立（中间层级 a/sub/.glaucous 同理）；r1-B2 主缺陷（workspace 自身子目录）已消除（见通过项），此为同一不变量的残留暴露面，后果同为破坏性静默失败。

- **修复方向（提请作者确认归属）**：①代码侧：排除改任意层级——diff/restore pathspec 增 `:(glob)**/.glaucous`、`_excluded` 改按路径段判断，create 的 reset 步骤相应覆盖，并补多实例/中间层级用例；②或 spec 修订显式收窄决策 5 声明范围（仅根级 + 当前工作区级）并同步修正两处注释。二选一，不得维持「注释宣称任意层级、实现仅两级」的现状。

## 二、建议问题

### S7. note 事件在折叠思考区实时视图只显示事件名「note」，告警文案不可见
- **维度**：逻辑正确性（呈现层完整性，B3 波及面）

- **代码位置**：cli.py:1145-1152（make_on_event note 分支：thinking 激活时走 thinking.add）；cli.py:698-739（`_thinking_line` 无 "note" 分支，兜底 `return event` → 思考区行为字面量 "note"）；repl 时序 cli.py:1670（thinking.start）→ 1682（await loop.run），note 必然落在折叠激活窗口内。对照 render_event:602-604（/expand 回放与降级路径正常显示文案）。
- **spec 位置**：§五「首次创建失败时 warning 一次（FR-40『明确提示不可用原因』）」；§四「经 on_event 'note' 伪事件」。
- **冲突/缺陷说明**：折叠激活时实时视图只出现一行 "note"，不可用原因文案仅 /expand 或降级路径可见——FR-40「明确提示」在主呈现面上落空（功能不损，呈现失真）。
- **修复方向**：`_thinking_line` 增 note 分支（如 `f"⚠ {payload.get('message', '')}"`），与 render_event 同源。

### S8. B2 回归用例被 fixture 的 .gitignore 掩蔽，对 excludes 机制无拦截力
- **维度**：Spec 符合性（测试完备性，S4/B2 处置效力）
- **代码位置**：tests/test_checkpoint_git.py:38-45（fixture 写 `.gitignore` 内容 `.glaucous/`——无前导斜杠的目录模式匹配**任意层级**同名目录）；152-163（test_workspace_subdir_excludes_runtime 依赖该 fixture）。
- **spec 位置**：§七用例 4/6；r1-B2 修复方向「补子目录场景用例」（复现口径为「仓库 .gitignore 未覆盖——工具自身不写 .gitignore」）。

- **冲突/缺陷说明**：WSL 实证：在相同 fixture 配置下把 `CheckpointStore._excludes` monkeypatch 回修复前的仅根级元组，重放该用例场景——checkpoints.json 仍存活、new.txt 仍被移除，全部断言仍会通过。即 excludes 机制整体失效时该用例依然全绿，无法拦截 B2 类回归；create/preview/rollback 三函数 excludes 一致性目前没有有效用例保护。
- **修复方向**：该用例改用无 .gitignore 的独立 repo（或用例内移除 .gitignore），断言 sub/.glaucous 既不进快照树也不进 A 项。

### S9. spec §3.1/§3.2 接口清单未同步本轮实现修订，文档与代码漂移
- **维度**：Spec 符合性（文档一致性）
- **代码位置**：git_snapshots.py:81/117/155（三函数均带 `excludes=(".glaucous",)`）；store.py:76-83（公开方法 take_warning）；git_snapshots.py:137-139（R/C 行解析为旧路径 status=M）。

- **spec 位置**：§3.1 行 82-85（三函数签名无 excludes 参数）；§3.1 行 90「快照五步中 add -A pathspec：`-- . ':(exclude).glaucous'`」与决策 1 修订后的「add 明 pathspec + reset 排除」不一致（实现遵循决策 1）；§3.2 行 101-111（CheckpointStore 方法清单无 take_warning）。
- **冲突/缺陷说明**：各处置均有决策依据、行为与 spec 语义不冲突（excludes 为带默认值的附加参数，spec 调用形态全兼容；R/C→M 保持 §3.1 的 {status,path} 形状），但 spec 是唯一功能基线，应回写本轮接口修订，避免下轮评审以旧签名误判。
- **修复方向**：spec §3.1/§3.2 同步 excludes 参数、take_warning、R/C→M 行为；§3.1 行 90 与决策 1 的 add+reset 表述二选一改齐。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|---|---|---|
| Spec 符合性 | B1：`_run` 统一注入 `-c core.quotepath=off`（git_snapshots.py:36，覆盖 ls-files/ls-tree/others/diff 全部输出）；test_cjk_filename_removed 断言未转义路径进 A 项且回退移除；A 项 `is_file()` False 现为「缺失容忍」语义（路径已还原为真实名），OSError 才入 failed（§五） | ✓ |
| Spec 符合性 | B2（主缺陷）：workspace 为子目录时 `sub/.glaucous` 与根级 `.glaucous` 双排除——`_excludes` 计算相对路径，create/preview/rollback 三调用点全传入（store.py:170/252/260-261），三函数签名一致（默认 (".glaucous",)）；实证（无 .gitignore 真实缺陷配置）索引/审计/会话文件存活、仅新增文件被移除；残留暴露面→B5 | ✓（主缺陷消除） |

| Spec 符合性 | B3：非 Git 早退分支审计 ok=false（store.py:154-166）；take_warning 一次性（store.py:76-83，实证第二次返回 None）；loop.run 失败路径经 on_event("note") 呈现（loop.py:99-104）；make_on_event note 分支落账+呈现（cli.py:1145-1152）；render_event note 分支使 /expand 回放与降级路径兼容（cli.py:602-604，_cmd_expand else 分支可达） | ✓ |
| Spec 符合性 | S1：spec §3.1 已增「S1 处置（交付后作者确认）」修订注记（不引入 NotGitWorkspace，rev-parse 失败统一 GitError）——代码与修订后 spec 一致 | ✓ |
| Spec 符合性 | S2：create 异常捕获宽化为 Exception（store.py:205），失败返回 None 成为硬契约（审计 ok=false + 一次性告警留痕） | ✓ |
| Spec 符合性 | S3（list 半边）：`_load_cp` 形状容错（store.py:132-148）+ list() 双层过滤（237-239），实证错型条目下 list() 正常；create 半边失效→B4 | ✓（限 list/get） |

| Spec 符合性 | S4：三个新增用例存在且方向正确——test_a_item_unlink_failure_reported（只读目录→failed 标记）、TestRollbackCommand.test_non_git_notes_reason（命令层降级）、test_full_flow_files_only（完整流程；_cmd_rollback 函数内 `from .cli import select_with_arrows` 调用时绑定，monkeypatch 生效）；subdir 用例效力缺口→S8 | ✓ |
| Spec 符合性 | S5：`_cmd_rollback` 汇总列出「未能移除」路径 ≤5 条 + 「等 N 项」溢出计数（commands.py:938-943） | ✓ |
| Spec 符合性 | S6：diff_against 解析 R/C 行取旧路径列 status=M（git_snapshots.py:137-139），新路径经 ls-files∪others−ref 树进 A 项，还原/移除语义自洽 | ✓ |
| 逻辑正确性 | take_warning 时序：create 失败置 pending（仅首次）→ loop.run 同轮取用并清空 → 后续轮静默；审计每次失败均留痕（§3.2 要求审计、§五仅约束告警一次，二者不矛盾）；on_checkpoint 仅成功路径触发，与 note 互斥 | ✓ |

| 逻辑正确性 | note 事件波及面：/expand 回放 ✓、降级/管道直打 ✓、session_events 落账不受 begin_turn 清空协议影响；折叠思考区呈现缺口→S7 | ✓（除 S7） |
| 逻辑正确性 | 范围核查：git status 显示本轮改动仅及声明文件（TODO.md 为决策 6/7 偿还登记）；create_snapshot/diff_against/restore_from 调用方仅 store.py；update_ref 为决策 1 五步固有（r1 已核）；无范围蔓延 | ✓ |
| 运行验证 | WSL `pytest tests/ -q` → 262 passed（5.04s）；git log 顶部仍为 aee703f（M4 未提交，与 r1 评审时点一致） | ✓ |

## 四、复审要求

**不通过**，必须修复阻塞项 **B4、B5** 后提请第 3 轮复审：

- B4：store.py:186-190 索引重写改为「先 `_load_cp` 过滤、后 `_dump`」，补「合法 JSON + 错型条目 + create 成功且索引修复」回归用例；
- B5：按提请作者确认的归属执行——任意层级排除（代码路线）或决策 5/注释声明收窄（spec 路线），并按 S8 让 B2 回归用例在真实缺陷配置下生效。

建议项 S7~S9 随本轮或下轮一并处理（S9 的 spec 回写可与 B5 的归属裁决合并处理）。
