# 代码评审报告：V1.1-M4 Checkpoint Spec（第 1 轮）

> 评审日期：2026-08-31 11:00
> 评审对象：spec `docs/designs/202608310900-plan-v11-m4-checkpoint.md`；代码全量（新增 src/glaucous/checkpoint/{__init__,git_snapshots,store}.py、tests/test_checkpoint_git.py、tests/test_rollback_context.py；修改 src/glaucous/context/history.py、config.py、permission/approval.py、agent/loop.py、commands.py、cli.py）
> 模式：全量评审
> 运行验证：WSL `~/miniconda3/envs/glaucous/bin/python -m pytest tests/ -q` → **256 passed in 4.31s**（基线 239 守恒 + 17 新增，与任务声明一致）；`python -m glaucous --help` 入口正常；另以 WSL python 导入项目真实代码完成 5 项边界实证（空仓库快照、CJK 文件名 A 项、仓库子目录 workspace、git mv 重命名、CJK M/D 输出形态），全程只读、未修改任何源码
> 结论：**不通过**（阻塞 3 项，建议 6 项）

## 一、阻塞问题

### B1. A 项移除对非 ASCII 文件名静默失效（git quoting 未处理），回退后假报成功
- **维度**：逻辑正确性（核心回退行为对常见输入静默失效 + 假成功）
- **代码位置**：src/glaucous/checkpoint/git_snapshots.py:116/121-123——`git diff --name-status`、`git ls-files`、`git ls-files --others --exclude-standard`、`git ls-tree -r --name-only` 均未加 `-z`、未注入 `-c core.quotepath=false`；git 默认 `core.quotepath=true`，非 ASCII 路径输出为 C 引用的八进制转义形态。src/glaucous/checkpoint/store.py:215-220——`target = self._root / item["path"]` 以转义串拼路径，`if target.is_file(): target.unlink()` 对不存在的转义路径静默跳过且不记入 `failed`。
- **spec 位置**：§3.1「A 项另行计算：tracked = git ls-files ∪ untracked非忽略 − git ls-tree ref 集合 → 逐项 status=A」；§3.2「对 diff_against 中 status==A 的文件逐一 Path.unlink（缺失容忍）」；§七用例 1「回退 → …B 被移除」；§七验收「场景 H：/rollback 回退 → git status 干净」。
- **冲突/缺陷说明**：WSL 实测（CheckpointStore 真实代码链路）：checkpoint 后新增 untracked 文件「中文新增.txt」→ `preview_changes` 的 A 项路径为 `"\344\270\255\346\226\207\346\226\260\345\242\236.txt"`（外层引号+八进制转义）→ `rollback` 后该文件**仍然存在**，且返回项 `failed: false`、审计 `failed_remove: 0`——汇总提示「已回退（N 项变更）」无任何「未能移除」警示。即 FR-42「移除 checkpoint 之后新增的文件」与场景 H「git status 干净」对中文文件名（本产品主要用户群的高频场景）静默落空；§3.2 的「缺失容忍」被扩大为「路径不匹配容忍」，把可发现的失败变成假成功。附带：M/D 项路径同样带转义（实测 `M\t"\347\225\214\351\235\242.txt"`），确认卡/汇总对中文路径显示为转义乱码（还原本身走 pathspec 不受影响，属显示层）。
- **修复方向**：四处 git 输出统一加 `-z` 并按 NUL 切分（或 `_run` 注入 `-c core.quotepath=false`），A/M/D 路径还原为真实文件名后再参与集合运算与 unlink；A 项 `is_file()` 为 False 时不应静默——按 §五「新增文件移除失败」同路径汇入 failed；补 CJK 文件名建/改/回退全链回归用例。

### B2. workspace 为 git 仓库子目录时决策 5 的排除不变量失效：运行时文件进入快照与回退面，回退将删除 checkpoints.json/会话文件并回卷审计日志
- **维度**：Spec 符合性（决策 5 不变量）/ 逻辑正确性（边界输入 → 破坏性静默失败）
- **代码位置**：src/glaucous/checkpoint/git_snapshots.py:89-93（`add -A -- .` 与 `reset -q -- .glaucous` 以 repo_root 为 cwd，仅匹配根级 `.glaucous`）、116（diff 的 `:(exclude).glaucous` 同）、133（restore 的 exclude 同）、124-126（A 项跳过仅判 `path == ".glaucous" or path.startswith(".glaucous/")`）；src/glaucous/checkpoint/store.py:69（`_root = git.repo_root(self._workspace)`——设计上明确支持 workspace 为仓库子目录）。
- **spec 位置**：决策 5「快照排除 `.glaucous/`：运行时目录（会话/审计/索引/输出）不进快照——防止审计与会话文件进入回退面（回退审计日志 = 审计失真）」；§3.1 快照五步与 restore pathspec。
- **冲突/缺陷说明**：WSL 实测：repo 根仅提交 root.txt，workspace=repo/sub（glaucous 运行时目录在 sub/.glaucous，仓库 .gitignore 未覆盖——工具自身不写 .gitignore，顶层场景的排除全靠 pathspec，用户无动机自行 ignore）：①快照树包含 `sub/.glaucous/audit.log`（决策 5 失效）；②checkpoint 之后追加审计、新建会话文件、写入 checkpoints.json 后执行 rollback：`sub/.glaucous/checkpoints.json` 作为 A 项**被删除（索引自毁**，此后 /rollback 恒「暂无可用 checkpoint」，Git ref 成为不可达孤儿——注意 spec §六 1 登记的「索引丢失」边界指外部损坏，不应由 /rollback 自身触发）、`sub/.glaucous/sessions/s1.jsonl` **被删除**、`audit.log` 被还原为快照时刻内容（**审计失真**——恰是决策 5 立条要防止的后果）。代码与 spec 字面 pathspec 一致，但 spec 声明的不变量在代码自身支持（repo_root 即为此而设）的配置下不成立，且后果为破坏性静默失败。
- **修复方向（提请作者确认修复归属：改代码或修订 spec）**：①排除规则改跨层级——add 后的 reset、diff/restore 的 exclude 统一增 `:(glob)**/.glaucous` 类 pathspec，A 项跳过改为按路径段判断（如 `"/.glaucous/" in f"/{path}"`）；或 ②workspace ≠ repo_root 时 checkpoint 降级不可用并提示原因；或 ③spec 修订显式声明「workspace 须为仓库根」边界并在代码中拒绝子目录。
### B3. 创建失败的「warning 一次」用户提示未实现；非 Git 早退分支未按 §3.2 审计 ok=false
- **维度**：Spec 符合性（错误处理策略，FR-40 落地口径）
- **代码位置**：src/glaucous/checkpoint/store.py:124-125（`if not self.available: return None`——无审计、无提示）；store.py:159-168（GitError 分支仅审计 ok=false）；src/glaucous/agent/loop.py:90-100（`except Exception: cp = None` 静默降级，无任何 on_event 提示）；全库检索确认无 checkpoint 失败的用户可见提示（仅 /rollback 按需展示 unavailable_reason）。
- **spec 位置**：§五「非 Git 工作区｜……启动不探测不告警，**首次创建尝试失败时 warning 一次**（S2：满足 FR-40『明确提示不可用原因』且不每轮打扰，对概设 §5.2『创建时检测失败 → 提示』的落地口径）」；§3.2「create：**非 Git/异常 → 审计 checkpoint_create（ok=false）**+ 返回 None」。
- **冲突/缺陷说明**：非 Git 工作区下 checkpoint 永远静默不发生：①首轮任务后无任何提示，用户仅能在主动执行 /rollback 时才得知不可用原因——FR-40「明确提示不可用原因」的 S2 落地口径（spec 评审轮专门修复项）未实现；②非 Git 早退分支不写 `checkpoint_create ok=false` 审计，§3.2 明文要求未满足。附注：git 命令失败（raise 路径）的「轮内一次 warning」，§四允许「或静默 + 审计 ok=false」替代，该路径现状尚可辩；但非 Git 路径不 raise、直接返回 None，§四的替代口径不适用。
- **修复方向**：store.create 失败/不可用时经回调通道（或 loop.run 得到 None 后）发一次用户可见提示（如 on_event note：「checkpoint 不可用：{unavailable_reason/错误摘要}」），会话内去重仅提示一次；非 Git 早退分支补审计 ok=false。

## 二、建议问题

### S1. spec §3.1 声明的 NotGitWorkspace 异常类未实现（提请作者确认）
- **维度**：Spec 符合性（签名级，无行为影响）
- **代码位置**：src/glaucous/checkpoint/git_snapshots.py:20-21（仅定义 GitError）；49-55（is_git_workspace 捕获 GitError 返回 False）；58-60（repo_root 对 rev-parse 失败统一抛 GitError）。
- **spec 位置**：§3.1「class NotGitWorkspace(RuntimeError): ... # rev-parse 失败（非 Git 工作区）」。
- **冲突/缺陷说明**：「非 Git」语义由 store._probe 的 reason 文案（store.py:66「当前工作区不是 Git 仓库，checkpoint 不可用」）承担，无调用方依赖该异常类型，行为面完整；但 spec 接口清单声明的公开符号缺失，按签名一致性口径登记。
- **修复方向**：实现该类（探测性 rev-parse 失败抛出），或提请 spec 修订移除该声明。

### S2. store.create 仅把 GitError 转为「审计 + None」，索引写 OSError/损坏条目形状异常会向上抛出
- **维度**：Spec 符合性（API 契约）
- **代码位置**：src/glaucous/checkpoint/store.py:122-168——try 范围未覆盖 `_save_index`（90-94）的 OSError 与 `_load_cp`（108-118）对错型条目的 AttributeError/ValueError。
- **spec 位置**：§3.2「def create(...) -> Checkpoint | None # 失败返回 None」。
- **冲突/缺陷说明**：.glaucous 目录被删/只读等 IO 失败时 create 抛 OSError 而非返回 None；loop 兜底捕获（loop.py:97）任务不阻断但无审计留痕。决策 2 声明 M5 任务级 checkpoint 复用该 API，届时将直接面对异常。
- **修复方向**：try 范围扩至索引读写段，非 GitError 异常同样审计 ok=false 后返回 None（可与 B3 的提示通道合并实现）。

### S3. 索引「合法 JSON 但结构错型」未容错，/rollback 的 store.list() 无兜底可致 REPL 退出
- **维度**：逻辑正确性（健壮性）
- **代码位置**：src/glaucous/checkpoint/store.py:81-88（_load_index 仅校验 checkpoints 为 list，不校验元素形状）；src/glaucous/commands.py:894（`cps = store.list()` 位于 repl 命令分派无 try 区，cli.py:1647 的 handle_command 无兜底）。
- **spec 位置**：§五「索引损坏/缺失 → 空索引重新起步」。
- **冲突/缺陷说明**：`{"checkpoints": [1]}`、`{"seq": "x"}` 等手工损坏形态使 list()/get() 抛 AttributeError/ValueError，/rollback 未捕获 → 穿透 repl → 进程退出。非法 JSON（spec 用例 6a）已覆盖且测试通过；原子写下自然损坏窗口极小，此为残余边界。
- **修复方向**：_load_index/_load_cp 增逐条目类型守卫（错型行丢弃或整体空索引起步）；或 _cmd_rollback 对 list/preview 包 try/except 报错保持。

### S4. 测试缺口（对照 spec §七）
- **维度**：Spec 符合性（测试完备性）
- **代码位置**：tests/test_checkpoint_git.py——无「A 项 unlink 失败 → failed 子清单」用例（store.py:211-220 的 failed 标记零覆盖）；/rollback 命令层行为（用例 4 的「/rollback 提示文案」、用例 6 的「restore 失败 → 报错不继续二问」）仅覆盖到 store 层（test_rollback_git_error_raises:127-140 只断言 GitError 上抛）；无 CJK 文件名用例（B1 由此漏网）。
- **spec 位置**：§七用例 4「非 Git 降级：……/rollback 提示文案」、用例 6「A 项 unlink 失败 → failed_remove 子清单」。
- **冲突/缺陷说明**：已实现的 failed 标记与命令层错误面缺验证手段，B1/B3 类缺陷难以被现有用例拦截。
- **修复方向**：补 monkeypatch Path.unlink 抛 OSError 的 failed 标记用例与 CJK 文件名全链用例；命令层行为可经 select_with_arrows(read_key=…) 注入驱动覆盖。

### S5. 「未能移除」提示仅显示数量、未列路径
- **维度**：Spec 符合性（提示口径，轻微）
- **代码位置**：src/glaucous/commands.py:940-941——`summary += f"；{len(failed)} 个新增文件未能移除"`。
- **spec 位置**：§3.4 步骤 5「确认后 store.rollback(cp) → 汇总提示（含『未能移除』子清单）」。
- **冲突/缺陷说明**：changes 内已带 failed 标记与真实路径，UI 仅给计数，与 spec「子清单」措辞不一致；失败路径不可见时用户难以处置（如解除只读后重试）。
- **修复方向**：按确认卡同口径 ≤10 行列出路径 + 溢出计数摘要。

### S6. git mv 暂存重命名使确认卡少列「还原」项（功能无损，预览/审计计数低估）
- **维度**：逻辑正确性（变更清单完备性，边界场景）
- **代码位置**：src/glaucous/checkpoint/git_snapshots.py:119（`parts[0][0] in "MD"` 丢弃 R/C 状态行）。
- **证据**：WSL 实测——checkpoint 后 `git mv old.txt new.txt`，`git diff --name-status <ref>` 原始输出为 `R100\told.txt\tnew.txt`（git 默认重命名检测开启），diff_against 仅返回 `[{"status": "A", "path": "new.txt"}]`。
- **冲突/缺陷说明**：old.txt 既不入 M/D 也不入 A → 确认卡与审计变更数低估；实际还原不受影响（restore_from 按 pathspec 恢复 ref 树内缺失的 old.txt，new.txt 走 A 项移除），仅预览/审计口径失真。
- **修复方向**：diff 加 `--no-renames`（退化为 D+A 两行）或解析 R/C 行拆分为 D(old)+A(new)。
## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | 模块与目录结构：src/glaucous/checkpoint/{__init__,git_snapshots,store}.py 位置与 spec §一层表、概设 §10 一致；tests 两文件命名对齐概设 §11 清单 | ✓ |
| Spec 符合性 | §3.1/§3.2/§3.3 签名与数据模型：GitError、七个函数签名、Checkpoint 七字段、索引 JSON {version/seq/checkpoints[]}、truncate_to 签名均一致（NotGitWorkspace 缺失→S1） | ✓ |
| Spec 符合性 | 决策 1：临时索引五步——read-tree(HEAD/空树)→add -A→reset→write-tree→commit-tree(-c 身份注入)→update-ref；空仓库（head=None）实测通过：快照树仅含 app.py、.glaucous 正确排除、unborn HEAD 下 reset 恒成立（spec「恒成功」表述成立）；全程 GIT_INDEX_FILE 临时索引，用户真实索引与工作树不受影响（用例 1 的 `git status --porcelain` 为空佐证）；无 .gitignore 场景下 .glaucous 经 reset 步骤排除 | ✓ |
| Spec 符合性 | 决策 2：loop.run 入口接线与 spec §四伪代码逐行一致（message_count/锚在 push_user 前取、空历史空串锚、异常降级不阻断、on_checkpoint 外泄）；子 agent loop 不注入（subagent.py:201-211 实证）；rebuild_loop 经 ctx.checkpoint_store 间接引用，/clear、/resume、/fork、/sessions 切换后沿用（D8） | ✓ |
| Spec 符合性 | 决策 3/§3.3：truncate_to 三拒绝分支（超界/锚不符/空串锚错配）+ count=0 清空为真实截断（JSONL 仅 meta 行）；先写文件后改内存，写失败内存未动（用例 7 四态全覆盖）；history_digest sha256[:12] 与 §二 一致 | ✓ |
| Spec 符合性 | 决策 4/§3.5：审批卡第四选项——提供条件（主 agent 且 turn_checkpoint_seq 非空）正确；**箭头 idx==2/3 映射在 rollback_ready 两种形态下均正确**（False 时选项集 3 项、idx==3 不可达；True 时 4 项、else 分支恰为 idx==3）；[d]/[b] 键规则符合「[b] 维持现状仅非 DANGEROUS」；取消=普通拒绝不回退；_reject_with_rollback 先回文件、失败降级 reject（checkpoint 丢失与 GitError 双路）；gate reject_rollback 分支审计 decision 原样 + 回喂「用户拒绝并已回退：{reason}」 | ✓ |
| Spec 符合性 | 决策 5：workspace==repo_root 场景 .glaucous 四处置一致（快照 reset 排除/diff exclude/restore exclude/A 项跳过）——**子目录场景失效→B2** | ✓（限根场景） |
| Spec 符合性 | 决策 6/7：权限矩阵未改动；auto_rollback_on_reject 配置与 checkpoint 可关开关未实现（spec §八 登记偿还，属声明裁剪不判缺陷） | ✓ |
| Spec 符合性 | §3.4 /rollback 六步：不可用提示（store None/非 Git 两路）/空列表提示/箭头选择（行=seq·时间·任务摘要，Esc 取消）/确认卡（将还原·将移除计数 + ≤10 行逐条 + 溢出计数）/回退汇总/上下文二问（默认否、Esc 同否、是→truncate_to+last_budget 重算+rebuild_loop 与 /clear 同路径）；live_hooks pause/resume 覆盖全部返回路径；GitError 中断二问；ContextAnchorMismatch/OSError 分支文案符合 §五/S6；turn_active 守卫为防御性（REPL 串行下不触发，无害） | ✓ |
| Spec 符合性 | 配置（§一表/概设 §9）：checkpoint_max_keep 默认 50 + GLAUCOUS_CHECKPOINT_MAX_KEEP 复用 _load_positive_int（config.py:60/119-121） | ✓ |
| 逻辑正确性 | store.create 时序与审计：先 append 索引再 _evict（第 51 个创建后最旧被淘汰——用例 3 实证 ref 删除+索引行移除）；成功审计含 seq/commit；索引原子写 tmp+replace；update-ref -d 对已删 ref 的报错被容忍（幂等口径） | ✓ |
| 逻辑正确性 | diff_against 的 M/D 与 A 来源互斥无重漏：A 要求 ∉ ref 树、M/D 来自 diff（工作树相对 ref）；暂存新增（staged）被 diff 过滤但经 ls-files 进入 A 集，无重复无遗漏（暂存重命名 R 行缺口→S6） | ✓ |
| 逻辑正确性 | 回归风险核查：repl 轮生命周期改动最小化（cli.py:1669 置 None/1684 finally 清理，置于既有 turn_active 置位/复位旁）；gate 既有 approve/approve_type/reject 三分支语义与顺序不变（reject_rollback 插于 reject 之前）；/sessions、/resume、/fork、loop 子 agent 路径无行为变化；thinking/live_hooks 协议未被破坏 | ✓ |
| 逻辑正确性 | 运行验证：pytest 256 passed（基线 239 守恒 + 17 新增，两文件 7+10 用例）；`python -m glaucous --help` 冒烟通过 | ✓ |
| 测试质量 | 17 新增用例与 spec §七清单主体对齐：快照/回退精确（含 A 项来源与 status 干净断言）、untracked 跨轮还原（决策 1 核心动机）、淘汰、非 Git 降级、gitignored 排除（S8）、索引损坏起步（S11）、GitError 上抛、truncate 四态、gate 联动/降级（S5）、loop 接线四态（B2 外泄链/空历史锚 B4/无 store/异常不阻断）；身份经 -c 注入不依赖全局配置（缺口→S4） | ✓ |

## 四、复审要求

**不通过**，必须修复阻塞项 **B1、B2、B3** 后提请第 2 轮复审：

- B1：git_snapshots.py 路径输出编码处理（-z/quotepath）+ store.rollback 的 A 项移除失败口径 + CJK 回归用例；
- B2：排除规则跨层级化（或 workspace≠repo_root 降级策略，或 spec 边界修订——修复归属提请作者确认），并补子目录场景用例；
- B3：创建失败的会话内一次性提示接线（store→loop/cli 通道）+ 非 Git 早退审计。

建议项 S1~S6 随本轮或下轮一并处理（S1/S4 与 B1/B3 修复自然交叠，优先合并解决）；第 2 轮为聚焦复审，范围限 checkpoint 包改动面、loop/cli 提示接线及新增测试。