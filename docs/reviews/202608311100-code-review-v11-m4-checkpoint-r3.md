# 代码评审报告：V1.1-M4 Checkpoint Spec（第 3 轮）

> 评审日期：2026-08-31 11:00
> 评审对象：spec `docs/designs/202608310900-plan-v11-m4-checkpoint.md`（决策 1/决策 5/§3.1/§3.2 已回写 r2 修订）；聚焦代码：src/glaucous/checkpoint/{git_snapshots,store}.py、src/glaucous/cli.py、tests/test_checkpoint_git.py、tests/test_rollback_context.py
> 模式：聚焦复审（改动范围：r2 B4/B5 修复 + S7~S9 处置及其波及面——git 子进程三函数、store 编排、thinking 呈现链、两测试文件与 fixture）
> 运行验证：WSL `pytest tests/ -q` → 264 passed in 4.69s（基线 239 + 25 新增，与声明一致）；另以 WSL python 加载项目真实代码 + 独立 git 临时仓库完成 9 项边界实证（rm pathspec 四情况、restore 两镜像场景、快照树污染、seq 错型、排除失效注入、_thinking_line），实验脚本置于 /tmp，未修改仓库任何文件
> 结论：**不通过**（阻塞 2 项，建议 5 项）

## 一、阻塞问题

### B6. create_snapshot 的 rm 排除在「深层 .glaucous + 根级无条目」组合下整体失效：git rm 多 pathspec 原子性——任一 pathspec 无匹配即整条 fatal 不删除，深层条目留在快照树（决策 5「任意层级不进快照」破坏，r2-B5 裁决部分回归）
- **维度**：Spec 符合性（决策 5）/ 逻辑正确性
- **代码位置**：git_snapshots.py:104-112

```python
for e in excludes:
    try:
        _run(
            root, *ident, "rm", "-q", "--cached", "-r",
            "--", f":(glob)**/{e}/**", f":(glob){e}/**",
            env_extra={"GIT_INDEX_FILE": tmp_index},
        )
    except GitError:
        pass  # 无匹配条目时 rm 报 pathspec 未命中：排除目标本就不存在，容忍
```

- **spec 位置**：决策 5「实现为目录名段语义：路径中任一段等于 .glaucous 即排除，覆盖仓库根级、子目录工作区、同仓其他实例」；决策 1「`git rm -q --cached -r -- ':(glob)**/.glaucous/**' ':(glob).glaucous/**'`……无匹配时容忍」
- **冲突/缺陷说明**：WSL 实证：①索引含 `sub/.glaucous/sessions/s.jsonl` 而根级 .glaucous 无任何条目时，`git rm --cached -r` 因第二个 pathspec（`:(glob).glaucous/**`）无匹配报 fatal（exit 128「pathspec ... did not match any files」），**整条命令不执行任何删除**，第一个 pathspec 已匹配的深层条目留在索引；项目真实代码 create 同样复现：无 .gitignore、根级 .glaucous 无文件、深层实例文件在 create 时已存在 → ls-tree 实证快照树含 `sub/.glaucous/sessions/s.jsonl`。②根级场景与 tracked+staged 场景均正常（rm_exit=0）。③git rm 的多 pathspec 匹配检查是原子的——「无匹配容忍」的 try/except 只能容忍「两 pathspec 都无匹配」的整条 fatal，不能容忍「一有一无」的半命中 fatal。触发组合真实存在：用户以 .gitignore 忽略根级 .glaucous（合理常见配置，工具自身不写 .gitignore）+ 同仓其他实例的深层 .glaucous 在 create 时刻已有内容（恰为 r2-B5 裁决场景：同仓多实例并发）。
- **危害边界（同轮实证）**：受污染条目为 untracked 时回退面行为仍安全——diff M/D 路与 A 项路均被 _excluded 段语义过滤（git_snapshots.py:150-151/157-158），restore 对「ref 有、索引无」的文件不还原（实证：staged 删除后执行 restore，文件不复活）——故危害为快照污染（运行时会话/审计内容写入快照 commit 并被 ref 引用保留于对象库），非工作树破坏。
- **修复方向**：两条 pathspec 拆为两次独立 rm 调用，各自 try/except 容忍 fatal（单 pathspec 无匹配的 fatal 语义即「该形态不存在」，容忍正确）；补「create 时刻深层 .glaucous 已存在 + 根级 .glaucous 被 ignore」回归用例，断言 ls-tree 快照树无任何 .glaucous 条目。现有两用例均不触发该组合：test_any_level_glaucous_excluded 的 sibling 建于 create 之后；test_workspace_subdir_excludes_runtime 的 sub/.glaucous 在 create 时刻为空（AuditLog 惰性建文件，实证 audit_before_create=False）。

### B7. restore 去除 pathspec 后对「用户 tracked 的 .glaucous 文件」执行删除：决策 5 目的句「不进回退面」与 §六 2 断言被实证否定（修复归属提请作者确认）
- **维度**：Spec 符合性（决策 5 / §六 2）
- **代码位置**：git_snapshots.py:163-168

```python
def restore_from(root: Path, ref: str, excludes=(".glaucous",)) -> None:
    """...快照已排除 excludes（B5），ref 树中无对应条目，restore 无需 pathspec..."""
    _run(root, "restore", f"--source={ref}", "--worktree", "--staged", "--", ".")
```

- **spec 位置**：决策 5「防止审计与会话文件进入回退面（回退审计日志 = 审计失真）」；§六 2「快照排除 .glaucous/（决策 5），故 .glaucous/ 内增删不进回退面——这是有意设计非缺陷」
- **冲突/缺陷说明**：WSL 实证：`.glaucous/audit.log` 已提交（tracked）+ 工作树修改 → `restore --source=<快照 ref（已排除 .glaucous）> --worktree --staged -- .` → **文件被删除**（.glaucous/ 目录整个消失，git status 呈 staged `D .glaucous/audit.log`）。spec §3.1 回写的「快照已排除 excludes，ref 树无对应条目，无需 pathspec」推理存在盲区：pathspec 不仅限定从 ref 取内容，还限定 restore 触碰的路径集合——「索引有、ref 无」的 tracked 条目会被 restore 删除。工具自身不写 .gitignore（r1-B2 复现口径），用户 `git add -A` 极易将运行时文件纳入索引；此后任一次回退（/rollback 或拒绝并回退）都会静默删除这些审计/会话文件——正是决策 5 立条要防的「审计失真」，§六 2 的「不进回退面」断言被直接违反。触发前提（用户误 tracked）概率低，但后果为静默数据丢失，与 r2-B5 同级判例（非常规配置 + 破坏性静默失败）。
- **修复方向（提请作者确认归属）**：①代码路线：restore 恢复排除 pathspec（如 `-- . ':(exclude)**/.glaucous/**'` 形态或按 diff 清单精确还原），并同步修订 §3.1 表述；②spec 路线：决策 5/§六 2 显式收窄（「未被纳入 git 索引的 .glaucous 文件不进回退面」）并修正相关注释。二选一，不得维持「spec 断言不进回退面、实证被删」的矛盾现状。

## 二、建议问题

### S10. spec §3.2 的 _evict 签名与实现漂移（S9 回写遗漏）
- **维度**：Spec 符合性（文档一致性）
- **代码位置**：store.py:211 `def _evict(self, index: dict[str, Any]) -> None`；spec §3.2 行 115 `def _evict(self) -> None`
- **说明**：实现带 index 参数（配合 B4 修复中 create 已持有索引，避免二次读写），行为语义与 spec 描述一致（超 max_keep 删最旧 ref + 清索引行），但 spec 接口清单未同步。回写即可。

### S11. git_snapshots.py 模块 docstring 仍描述已废弃的旧方案
- **维度**：文档一致性
- **代码位置**：git_snapshots.py:6-7「read-tree → add -A -- . ':(exclude).glaucous' → write-tree → commit-tree → update-ref」
- **说明**：与实现（add -A -- . 全量 + rm --cached 排除，即决策 1 回写版）不符；create_snapshot 自身 docstring（86-89 行）已正确描述新方案。同步模块 docstring，避免误导后续维护。

### S12. 索引 seq 字段错型 → create 永久失败（B4 同构残余，实证；非本轮引入，未判阻塞）
- **维度**：逻辑正确性（健壮性）
- **代码位置**：store.py:164 `seq = int(index.get("seq", 0)) + 1`
- **说明**：WSL 实证：索引写为合法 JSON `{"version":1,"seq":"x","checkpoints":[]}` 后 create 返回 None（int("x") 抛 ValueError，被 S2 宽化 except 捕获转审计），且 _load_index 的结构校验不重置 seq → 此后每轮 create 均失败、快照 commit 白建（无 ref 引用），仅 /rollback 尚可用——与 r2-B4 危害模式同构。触发面更窄（单字段错型，需外部手改索引），非本轮改动引入，故本轮判建议；建议 _load_index 对 seq 一并归一化（非 int 即置 0），一处改动闭合该形态。

### S13. 全仓行尾漂移：r2 评审后工作树被批量重写为 CRLF，git status 噪声化
- **维度**：工程卫生（不影响功能）
- **代码位置**：git status 93 文件 M；`git diff --ignore-cr-at-eol --stat` 证实其中 86 个为纯行尾重写（0 行实质变化），实质改动仅 M4 七文件 + TODO.md + 新增 checkpoint/ 包与两测试文件
- **说明**：pytest 不受影响（264 passed），但按现状提交将产生全仓噪声 diff，淹没 M4 交付的真实改动面，破坏历史可审查性。建议提交前统一行尾（.gitattributes 声明或批量还原），使 M4 提交仅含实质改动。

### S14. spec 状态行预写评审结论，与实际评审进度不符
- **维度**：文档一致性
- **代码位置**：spec 头部第 4 行「状态：已批准（经 4 轮评审；r1 B1~B3、r2 B4/B5、r3 B6 修复后 r4 通过，0 建议）」
- **说明**：本轮（r3）实际结论为不通过（B6/B7），状态行描述的「r3 B6 / r4 通过 / 0 建议」尚未发生。若为预写终态，建议评审落定后据实更新，避免误导后续读者。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|---|---|---|
| Spec 符合性 | B4 消除：create 索引重写改「先 _load_cp 解析、None 跳过、后 _dump 收集」（store.py:178-184）；test_corrupt_index_entry_not_fatal 覆盖「合法 JSON + 错型条目 [1] → create 成功（seq=2 不断号）、错型条目清理、list 仅剩新条目」，行为符合 §六 1「索引丢失即失去对应快照入口」边界声明；此前「每轮 create 永久失败、快照白建」路径消除 | ✓ |
| Spec 符合性 | B5（回退面）：_excluded 改目录名段语义（git_snapshots.py:76-80，路径任一段命中即排除）；diff M/D 路与 A 项路双过滤（150-151/157-158）；_excludes() 单一来源 (".glaucous",)（store.py:85-89），三个调用点一致（create→create_snapshot:160-162、preview_changes→diff_against:245、rollback→diff_against+restore_from:252-254）；test_any_level_glaucous_excluded 拦截力实证——排除失效注入（_excluded 恒 False）重放下 sibling 会话文件被删、用例必红 | ✓ |
| Spec 符合性 | B5（快照侧·根级）：rm --cached 根级排除实证生效（rm_exit=0，索引仅剩非排除条目）；tracked+staged 的 .glaucous 条目正常移除（实证 rm_exit=0，git rm --cached 不受 staged 内容差异阻碍）；深层暴露面→B6 | ✓（限根级） |
| Spec 符合性 | S7：_thinking_line 增 note 分支返回「⚠ {message}」（cli.py:705-707，实证输出 "⚠ m"）；呈现链完整——make_on_event note 分支折叠激活走 thinking.add（cli.py:1148-1152）→ ThinkingView.add 经 _thinking_line 生成行文本入折叠滚动区（cli.py:823-830）；/expand 回放与降级直打路径不变（render_event note 分支） | ✓ |
| Spec 符合性 | S8：两测试 fixture 的 .gitignore 收窄为 `/.glaucous/`（仅根级忽略，test_checkpoint_git.py:42、test_rollback_context.py:41）；实证 sub 工作区用例不再被掩蔽——AuditLog 惰性建文件（create 时刻 sub/.glaucous 无条目）→ 快照树干净（ls-tree 实证 ['.gitignore','a.txt']），且排除机制整体失效时该类断言必红（P3 交叉实证） | ✓ |
| Spec 符合性 | S9：spec 回写核验——决策 1（rm --cached 步骤 + 「Use -f」实测注记 + 无匹配容忍）、决策 5（目录名段语义）、§3.1（三函数 excludes 参数/R/C→M/restore 无 pathspec/A 项来源）、§3.2（take_warning/anchor_digest）；残留漂移→S10/S11 | ✓ |
| 逻辑正确性 | restore 镜像场景：untracked 运行时文件在回退中不删（A 项段语义过滤）不还原（实证 ref 有、索引无不复活）——untracked 常态下回退面安全；tracked 暴露面→B7 | ✓（限 untracked） |
| 逻辑正确性 | B4 波及面：create 失败路径契约不变（审计 ok=false + take_warning 一次性 + 返回 None）；_evict 在 clean 列表上工作（条目均为 _dump 产物，pop(0) 安全）；测试 fixture 的 S8 收窄不破坏既有用例语义 | ✓ |
| 运行验证 | WSL pytest tests/ -q → 264 passed（4.69s，基线 239 + 25 新增，与声明一致）；git 顶部 aee703f 未变（M4 未提交，与前两轮时点一致）；行尾漂移为纯 CRLF 重写、内容等价实证→S13 | ✓ |

## 四、复审要求

**不通过**，必须消除阻塞项 **B6、B7** 后提请第 4 轮复审：

- B6：rm 排除拆分为每 pathspec 独立调用（各自容忍无匹配 fatal）；补「create 时刻深层 .glaucous 已存在 + 根级被 ignore」回归用例，断言快照树（ls-tree）无任何 .glaucous 条目；
- B7：按提请作者确认的归属执行——restore 恢复排除 pathspec（代码路线）或决策 5/§六 2 声明收窄（spec 路线），不得维持 spec 断言与实证行为矛盾的现状。

建议项 S10~S14 随本轮或下轮一并处理（S12 一行改动可与 B6 同文件顺带；S13 建议在 M4 提交前完成行尾治理）。
