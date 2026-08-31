# 代码评审报告：V1.1-M4 Checkpoint Spec（第 4 轮）

> 评审日期：2026-08-31 03:38
> 评审对象：spec `docs/designs/202608310900-plan-v11-m4-checkpoint.md`（决策 1/5 与 §3.1/§3.2 已回写 r3 修订，状态行已澄清）；聚焦代码：src/glaucous/checkpoint/git_snapshots.py、src/glaucous/checkpoint/store.py、tests/test_checkpoint_git.py、tests/test_rollback_context.py
> 模式：聚焦复审（改动范围：r3 B6/B7 修复 + S10~S14 处置及其波及面——create_snapshot 排除步骤、restore_from pathspec、_load_index seq 归一化、模块 docstring、全仓行尾治理、两测试文件新增用例）
> 运行验证：WSL `pytest tests/ -q` → 267 passed in 5.30s（基线 239 + 28 新增，与声明一致）；另以 WSL git 2.25.1 于 /tmp 独立临时仓库完成 7 项边界实证（B6 旧/新 rm 调用对照、B7 旧/新 restore 对照、`:(exclude,glob)` 语法生效性、裸 .glaucous 文件形态），实验产物均在 /tmp，未修改仓库任何文件（本报告除外）
> 结论：**通过**（阻塞 0 项，建议 1 项）

## 一、阻塞问题

无。

（r3 两项阻塞 B6/B7 的消除证据见「三、通过项」前两行。）

## 二、建议问题

### S15. glob 排除形态不覆盖「名为 .glaucous 的裸文件」：决策 5 段语义表述与决策 1 glob 形态的固有张力（非本轮引入，提请知悉）
- **维度**：Spec 符合性（文档表述一致性）
- **代码位置**：git_snapshots.py:108（rm 形态 `:(glob)**/{e}/**` / `:(glob){e}/**`）、git_snapshots.py:175-176（restore 形态 `:(exclude,glob)**/{e}/**` / `:(exclude,glob){e}/**`）
- **spec 位置**：决策 5「实现为目录名段语义：路径中任一段等于 .glaucous 即排除」；决策 1「`git rm -q --cached -r -- ':(glob)**/.glaucous/**'` 与 `':(glob).glaucous/**'`」
- **说明**：WSL 实证（/tmp 独立仓库）：`:(glob)**/.glaucous/**` 不命中名为 `.glaucous` 的普通文件（rm exit 128，write-tree 后快照树仍含 `.glaucous` 裸条目）——glob 语义要求 `.glaucous` 后有 `/`。两决策在「用户提交了名为 .glaucous 的裸文件」这一极端形态下互相矛盾：决策 5 段语义要求排除、决策 1 glob 形态不排除。实现遵循决策 1（明文 glob），无实现错误；且 diff_against 的段语义过滤（git_snapshots.py:77-81）会把该文件滤出变更清单，故若该形态 tracked，回退时会被 restore 还原且不出现在确认卡。触发前提极罕见（工具运行时目录是 .glaucous/ 目录，裸同名文件只能来自用户手工创建并提交），自 r2-B5 引入 glob 形态起即存在，非本轮改动引入，故判建议。
- **修复方向**：无需改代码。建议在 spec 决策 5 补一句澄清「段语义指目录段下的条目，实际匹配形态以决策 1 的 glob 为准」，或将该形态显式登记 §六 已知边界。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|---|---|---|
| Spec 符合性 | **B6 消除（rm 多 pathspec 原子失败）**：create_snapshot 排除步骤改为每条 glob 独立 rm 调用、各自 try/except GitError 容忍（git_snapshots.py:105-116，双层循环保留 r2-B5 的 excludes 单一来源）。WSL 独立实证（同 test_deep_runtime_excluded_at_create 场景：fixture 的 `/.glaucous/` 忽略 + `sub/.glaucous/audit.log` 于 create 前已存在 + 根级 .glaucous 为空目录无索引条目）：旧实现等价命令（单次 rm 双 pathspec）exit 128 整条失败、write-tree 快照树含 `sub/.glaucous/audit.log`（原缺陷复现）；新实现等价（拆分调用）第一条 exit 0 删除深层条目、第二条 exit 128 容忍、快照树无任何 .glaucous 条目（树中其余条目为实验自身残留文件，非 .glaucous 运行时内容）。新增回归用例的场景构造与断言（ls-tree 断言）真实触发原缺陷路径，267 全量中通过；spec 决策 1 已回写「两条 glob 拆独立调用……合并调用在『深层命中 + 根级无条目』组合下整条失败；无匹配容忍」 | ✓ |
| Spec 符合性 | **B7 消除（restore 删除用户 tracked 的 .glaucous 文件；作者裁决为代码路线）**：restore_from 恢复 exclude pathspec——`--source=<ref> --worktree --staged -- . ':(exclude,glob)**/{e}/**' ':(exclude,glob){e}/**'`（git_snapshots.py:173-178），pathspec 限定 restore 触碰路径集合。WSL 独立实证：新实现 restore 后 tracked 的 `.glaucous/keep.txt` 与 `sub/.glaucous/keep2.txt`（根级+深层）均保持修改后内容、`a.txt` 正常还原为快照前版本、exit 0——`:(exclude,glob)` 语法在 WSL git 2.25.1 生效（Windows git 2.42 同支持，该 magic 组合 git 2.13+ 即可用）；旧实现等价对照（无 exclude pathspec 的 `-- .`）下 keep/keep2 被静默删除（git status 呈 staged `D .glaucous/keep.txt`、`D sub/.glaucous/keep2.txt`）——原缺陷复现，与 r3 实证一致。新增回归 test_user_tracked_glaucous_not_rolled_back 场景真实触发原缺陷路径且通过；spec 决策 5「回退面同语义（restore 必须保留 exclude pathspec……快照排除仅覆盖『新条目不进快照』，两者缺一不可）」与 §3.1 restore 行「+ exclude pathspec（r3-B7：限定触碰路径集合）」均已回写一致 | ✓ |
| Spec 符合性 | **B7 波及面——三函数 excludes 语义一致性**：`_excludes()` 单一来源 `(".glaucous",)`（store.py:85-89），三个调用点一致（create→create_snapshot:166、preview_changes/rollback→diff_against:249/257、rollback→restore_from:258）；create 的 rm glob 两条与 restore 的 exclude glob 两条完全镜像；diff_against 的段语义过滤覆盖 M/D 路与 A 项路（git_snapshots.py:154-155/161-162）——变更清单（确认卡数据源）与 restore 实际触碰面在 .glaucous 语义上闭合：清单排除的路径 restore 必不触碰、restore 还原的路径必在清单；`.glaucous/checkpoints.json` 与 audit.log 在回退中不受触碰（test_workspace_subdir_excludes_runtime 既有断言继续通过） | ✓ |
| Spec 符合性 | S10：spec §3.2 已回写 `def _evict(self, index: dict) -> None  # 超 max_keep 删最旧 ref + 清索引行`，与实现 store.py:215 一致 | ✓ |
| Spec 符合性 | S11：git_snapshots.py 模块 docstring（1-11 行）已更新为「read-tree → add -A → rm --cached（任意层级排除 .glaucous，r3-B6：两条 glob pathspec 拆独立调用……）→ write-tree → commit-tree → update-ref」，与实现一致，旧方案描述消除 | ✓ |
| 逻辑正确性 | S12：_load_index 对 seq 错型归一化——`int(data.get("seq") or 0)`，TypeError/ValueError → 0（store.py:100-104），关闭「seq 错型 → create 永久失败、快照 commit 白建」路径（r2-B4 同构残余）；test_corrupt_seq_not_fatal（seq:"x" → create 成功且 seq=1）通过；归一化位于结构校验（dict + checkpoints list）通过之后，与既有「损坏 → 空索引起步」语义叠加自洽；合法数字串 "5" 仍被保留为 5 | ✓ |
| 工程卫生 | S13（行尾治理）：WSL git diff = 18 files（raw 3405+/3144-；`--ignore-cr-at-eol` 271+/10-），与声明「93 files → 18 files、内容等价」吻合。构成：7 文件实质改动（TODO.md 的 r1 建议债务登记 + M4 六源文件，共 271+/10-）+ 11 文件纯行尾归一化（.gitignore、models.toml.example、8 个 docs，ignore-eol 后 0 hunk，为一次性随提交入库的行尾统一，属治理本体而非残留噪声）；Windows git（autocrlf=true）口径 diff 仅 7 文件，佐证提交面已无行尾噪声；HEAD aee703f 未变（M4 未提交，与前几轮时点一致） | ✓ |
| 文档一致性 | S14：spec 状态行改为「已批准（spec 评审 4 轮：r1 B1~B3、r2 B4/B5、r3 B6 修复后 r4 通过，0 建议；代码评审轮次另见 docs/reviews/202608311100-*）」，区分 spec 评审轮次与代码评审轮次，r3-S14 预写终态问题消除 | ✓ |
| Spec 符合性 | 范围核查：7 个实质改动 tracked 文件均在 M4 范围与 r1 债务登记内；11 个 untracked 新增（checkpoint/ 包、两测试文件、spec 文档与 7 份评审报告）均在 spec §一 表列范围；无 spec 之外的功能实现 | ✓ |
| 运行验证 | WSL pytest tests/ -q → 267 passed in 5.30s（基线 239 + 28 新增 = r3 的 25 + 本轮 3：test_deep_runtime_excluded_at_create、test_user_tracked_glaucous_not_rolled_back、test_corrupt_seq_not_fatal，与声明一致）；静态审读无新引入风险：create 双层循环对 excludes 扩展安全、restore pathspec 的 `--` 分隔与参数顺序正确（source/worktree/staged 在 `--` 前，`.` 与 exclude 均在 `--` 后）、tmp_index 的 finally 清理保留、GitError 上抛链符合 spec §五「报错保持现状」 | ✓ |

## 四、复审要求

无。r3 全部阻塞项（B6、B7）消除并经独立双向实证（旧实现等价命令复现缺陷 / 新实现等价命令修复缺陷），建议项 S10~S14 全部处置，全量测试 267 passed。M4 可进入提交流程；建议提交说明中标注 11 个文件为一次性行尾归一化（内容等价），保持历史可审查性；S15 为 spec 表述澄清项，随 M5 或下次 spec 修订顺带处理即可。
