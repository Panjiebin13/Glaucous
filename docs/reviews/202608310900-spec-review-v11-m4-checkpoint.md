# Spec 一致性评审报告：V1.1-M4 Checkpoint Spec（Git 快照 / 保留策略 / /rollback / 拒绝联动回退）

> 评审日期：2026-08-31 09:30
> 评审对象：`docs/designs/202608310900-plan-v11-m4-checkpoint.md`（状态：草稿）
> 对照文档：`docs/编程智能体需求文档v1.1.md`、`docs/编程智能体概要设计说明书v1.1.md`、`docs/Glaucous开发计划表v1.1.md`
> 代码对照：`src/glaucous/`（agent/loop.py、permission/approval.py、cli.py、commands.py、context/history.py、context/compactor.py、config.py、sessions/index.py、agent/subagent.py）
> 结论：**不通过**（阻塞 3 项，建议 9 项）

## 一、评审范围

- 「关联规格」声明范围：需求文档 §2.2（FR-40~43）、§5（约束 6）；概设 §2.3（Checkpoint 存储选型）、§5（Checkpoint 模块：快照机制/关键语义）；开发计划表 V1.1-M4 任务 4.1~4.5 与验收标准（场景 H）。
- 链接可达性：三份上游文档相对链接（`../编程智能体需求文档v1.1.md` 等）经核实全部存在可达；spec 正文引用的内部决策编号 R3（live_hooks 协议）、R6（箭头选择）、D8（闭包不捕获旧对象）在 `cli.py`/`commands.py` 注释中均有出处，引用有效。
- 实际触及内容（超出声明范围一并评审）：`History.truncate_to` 与 JSONL 落盘格式（context/history.py）、审批卡四选项与 gate 映射（cli.py / permission/approval.py）、L1/L2 压缩对消息列表的改写（context/compactor.py）、Config 冻结数据类扩展（config.py）、原子写先例（sessions/index.py）、子 agent 不注入 store 的可行性（agent/subagent.py）。

## 二、阻塞问题

### B1. `diff_against` 的 `git diff --name-status <ref>` 产出不了 A 项，「新增文件移除」机制失效

- **维度**：概设一致性（回退编排机制）＋ 结构与可执行性（内部正确性）
- **spec 位置**：
  - §3.1：`def diff_against(root: Path, ref: str) -> list[dict]        # diff --name-status <ref> → [{status: M/D/A, path}]`
  - §3.2：`rollback：restore_from → 对 diff_against 中 status==A 的文件逐一 Path.unlink（缺失容忍）→ 审计 rollback（seq/变更数）`
  - §3.4 第 4 步：「M/D 项『将还原』、A 项『将移除』」；§六 2：「`diff_against` 的 A = 工作树有、快照树无」
- **上游位置**：
  - 概设 §5.1：「└─ checkpoint 之后新增的文件 → 列入"将移除"清单」
  - 开发计划表 M4 验收：「回退后 `git status` 干净」；spec §七用例 1：「新建 B → 回退 → …… B 被移除」
- **冲突说明**：`git diff --name-status <commit>` 只比较 commit 与 index/工作树中的**已跟踪文件**，untracked 文件（checkpoint 之后由 agent 新建的文件未经 `git add`）不会出现在该命令输出中。因此按 spec §3.1 的签名实现，diff_against 永远返回不了 status==A 的条目：①确认卡「将移除」清单缺失该类文件（用户在信息不全下确认，违反 FR-42「回退前展示将变更的文件清单并确认」的意图）；②rollback 的 unlink 循环不会执行，新建文件残留 → 测试用例 1「B 被移除」必然失败、验收标准「回退后 git status 干净」无法达成；③概设 §5.1 的「新增文件列入将移除清单」设计意图落空。spec 决策 1 声称新方案「严格强于原方案」并以此支撑对概设 §5.1 的修正，但快照五步解决了 untracked **入快照**问题，diff 一步未解决 untracked **被发现**问题，修正不完整。
- **修复方向**：为 diff_against 补充 untracked 发现机制，使其真正满足 spec §六 2 自我声明的「A = 工作树有、快照树无」语义。可行方向：复用决策 1 已引入的临时索引技术做对称比较（`GIT_INDEX_FILE=<临时索引> git read-tree <ref>` → `git add -A -- . ':(exclude).glaucous'` → `git diff --cached --name-status <ref>`，此时 A/M/D 全集齐备），或采用等价的 `git status --porcelain` 组合解析；并在 §3.1 签名注释处更新实现描述，使 §七用例 1 可通过。

### B2. `ctx.turn_checkpoint_id` 无任何挂接路径，决策 4（拒绝联动）无法闭环

- **维度**：结构与可执行性（存在导致无法开工的未决项／内部矛盾）
- **spec 位置**：
  - §一 接线图：「`loop.run(task) 入口 → store.create(task) → checkpoint_id 挂 ctx.turn_checkpoint_id`」
  - 决策 2：「run() 入口（push_user 之前）`store.create(task)`」
  - §四：「`# cp 存活期由 repl 侧经 ctx.turn_checkpoint_id 消费；store.create 返回 Checkpoint，` `# loop 不持有（拒绝联动读 ctx，职责分离）`」
  - §3.5：「选中 → `store.rollback(store.get(ctx.turn_checkpoint_id))`（只回文件）→ 返回 `ApprovalDecision(choice="reject_rollback", reason=...)`」；提供条件含「`ctx.turn_checkpoint_id is not None`」
- **上游位置**：需求 FR-43：「用户拒绝审批时可选择"拒绝并回退"，自动回退到本轮任务开始前的 checkpoint」；概设 §5.2 拒绝联动行：「回退到本轮任务入口 checkpoint」
- **冲突说明**：按 spec 自身设计，checkpoint 在 `AgentLoop.run()` 入口创建，而 `AgentLoop.__init__` 与 `CheckpointStore.__init__`（§3.2：`workspace, audit, max_keep`）均不持有 ReplContext 引用，且 loop 与 repl 之间仅有 `on_event` 事件通道（spec §四只为失败降级定义了「"note" 伪事件或静默 + 审计」，成功路径未定义任何通知）。审批卡在 `run()` 进行中弹出，此时 `make_decision_callback` 读取 `ctx.turn_checkpoint_id`——但 spec 未定义**谁在什么时机把 create 返回的 checkpoint 写入 ctx**：loop「不持有」、store 无引用、repl 侧只在轮末 finally 清理（§3.5）。结果是 `turn_checkpoint_id` 恒为 None → 第四选项永不出现 → FR-43 无法工作；§一 接线图中「checkpoint_id 挂 ctx.turn_checkpoint_id」一步没有实现主体，属于无法开工的机制缺口。
- **修复方向**：三选一并显式写入 spec：①定义 on_event 新事件类型（如 `checkpoint`，payload 带 seq），loop 在 create 成功后 emit，repl 侧挂 `ctx.turn_checkpoint_id`（与既有 mode_changed/diagnostic 事件机制同构）；②改为 repl 在调用 `run()` 之前自行 `store.create` 并把 count 语义对齐（需同步修改决策 2 的接线位置声明）；③审批回调侧经 `store.list()` 取最新条目（需在 spec 声明跨会话并发创建时「最新 ≠ 本轮入口」的边界及取舍）。

### B3. L2 压缩改写消息列表后 `message_count` 映射失效（静默 no-op/切断点错位），且 `truncate_to` 省略概设明确要求的配对校验

- **维度**：概设一致性（机制偏离）＋ 结构与可执行性（内部正确性）
- **spec 位置**：
  - 决策 3：「checkpoint 索引记录 `message_count`（创建时刻 `len(history.messages)`，即本轮入口）……回退到更早的 checkpoint 即截断更多轮次——**单一机制覆盖任意深度**」
  - §3.3：「`count >= len(messages)` → no-op；否则内存截断 + JSONL 重写（首行 meta 不动，其余行重写为截断后消息）」「写失败（OSError）→ 抛出……部分回退须显式告知，禁止静默」
- **上游位置**：
  - 概设 §5.1 第 4 步：「『同时回退上下文』= 截断 History 至该 checkpoint 对应轮次前（JSONL 同步截断，**配对校验复用 load 的修复逻辑**）」
  - 需求 FR-42：「提供『同时回退上下文』选项」
  - 代码佐证 `src/glaucous/context/compactor.py` L195-202：L2 压缩将早期消息**原位替换**为一条合成摘要消息——`synthetic: dict[str, Any] = {"role": "user", ...}`、`messages[:split] = [synthetic]`，列表长度骤减
- **冲突说明**：三层问题。①**映射失效**：checkpoint 记录的 `message_count` 是纯长度序号；checkpoint 之后任意轮次内触发 L2 压缩（长任务高频路径，正是 checkpoint 的目标场景）会把 `messages[:split]` 替换为 1 条摘要，此后 `truncate_to(count)` 要么因 `count >= len(messages)` 走 **no-op**——用户明确选择了「同时回退上下文」却静默未截断（且 no-op 无任何提示设计，违反 spec §3.3 自己的「禁止静默」原则），要么截断点落在错位位置；②**序列合法性**：错位切断点可能把 assistant(tool_calls) 与其配对 tool 消息分离，同进程内 `view()` 直接把非法序列发给 API → 400（history.py 头部声明的「消息序列硬约束」），而概设 §5.1 明确要求截断时「配对校验复用 load 的修复逻辑」，spec §3.3 未包含任何配对校验/修复步骤——对概设机制的静默省略；③单一长度序号机制在 L2 压缩后不再「覆盖任意深度」，决策 3 的声明过强。
- **修复方向**：a) 登记 L2 压缩对映射的影响并给出策略（如 checkpoint 额外记录内容锚——本轮入口 user 消息的定位信息——truncate 时按锚定位；或 L2 压缩时重映射/显式作废既有 checkpoint 的上下文回退能力仅保留文件回退）；b) `truncate_to` 补配对校验与修复（对齐概设与 `History.load` 的 `_repair_dangling_tool_calls` 语义）；c) no-op 场景显式提示「上下文已被压缩，无法精确回退到该点」，与 §3.3「禁止静默」原则对齐。

## 三、建议问题

### S1. §一 分层影响表遗漏 `permission/approval.py`；gate 审计 decision 字段取值未明确

- **维度**：结构与可执行性／概设一致性
- **位置与摘录**：§一 影响表仅列「新模块 checkpoint/、config.py、context/history.py、commands.py、cli.py、agent/loop.py、tests/」；而决策 4 明确「`ApprovalDecision.choice` 扩展 `"reject_rollback"` 字面量」「gate 将 `reject_rollback` 映射为拒绝分支」——现网 `approval.py` L56 为 `choice: Literal["approve", "approve_type", "reject"]`，gate 分支判断在 L136-163，必改。另「审计 + 回喂」未明确审计事件 decision 字段记 `reject` 还是 `reject_rollback`（影响 /stats 审批决策分布等审计消费方口径）。
- **建议**：影响表补 `permission/approval.py` 行（Literal 扩展 + gate 分支 + 审计 decision 取值），消除 §一 与决策 4 的登记不一致。

### S2. 非 Git 工作区创建侧静默，与概设 §5.2「创建时提示」偏离未声明；FR-40「明确提示」时机未显式对齐

- **维度**：概设一致性／需求一致性
- **位置与摘录**：spec §五：「非 Git 工作区 | store.available=False（缓存）；/rollback 与『拒绝并回退』降级为不可用提示；**启动不告警（探测惰性，避免非 Git 用户每轮被打扰）**」；概设 §5.2：「非 Git 工作区 | **创建时检测** `git rev-parse --is-inside-work-tree` 失败 → 提示『当前工作区不是 Git 仓库，checkpoint 不可用』」；需求 FR-40：「非 Git 工作区明确提示不可用原因」。
- **建议**：spec 的「避免每轮打扰」取舍合理，但属对概设 §5.2 提示时机的偏离，应显式声明；并补齐 FR-40 提示的落点（如首次创建失败提示一次——对齐「git 命令失败」场景的「轮内一次 warning」口径，或声明「提示落在使用路径（/rollback/审批卡）」并论证其满足验收）。另 §五 写「拒绝并回退降级为不可用提示」与 §3.5「不提供该选项（退化为三选项）」口径不一致（选项消失 vs 出现提示），建议统一表述。

### S3. 概设 §10 配置增补未落实且未声明（auto_rollback_on_reject 缺失、config.toml 机制被 env 替代、「可关」开关缺失）

- **维度**：概设一致性
- **位置与摘录**：概设 §10：「`[checkpoint]` `max_keep = 50`」「`auto_rollback_on_reject = true     # FR-43 默认开`」；概设 §11：「`agent/loop.py ※  # run() 入口打 checkpoint（可关）`」；spec §一：「`Config` 增 `checkpoint_max_keep: int = 50`（env `GLAUCOUS_CHECKPOINT_MAX_KEEP`，复用 `_load_positive_int`）」——仅此一项，且机制由 config.toml 改为环境变量（与现网 config.py 全 env 加载一致，方向合理），`auto_rollback_on_reject` 与「可关」开关未出现、未声明裁剪。
- **建议**：在 §八 或决策记录中显式登记：「概设 §10/§11 的 `auto_rollback_on_reject` 与 checkpoint 可关开关不在本轮实现，FR-43 按默认开启固化」，并说明 env 替代 config.toml 的理由（现网无 config.toml 加载器）；或补实现。

### S4. DANGEROUS 卡选项集变更与既有 r2-S3 裁决（呈现不分列）冲突未声明，且与现网箭头形态不符

- **维度**：结构与可执行性（与既有代码接口吻合）
- **位置与摘录**：spec §3.5：「DANGEROUS 卡 `["同意", "拒绝", "拒绝并回退"]`」；现网 `cli.py` L312-314：「`v1.1 R6：统一三选项箭头选择（DANGEROUS 呈现不分列，安全语义由 gate 守卫兜底，r2-S3 决策）`」、L359：`select_with_arrows("请选择：", ["同意", "同意同类型", "拒绝"])`——现网箭头形态下 DANGEROUS 卡已含「同意同类型」（gate 侧兜底不豁免），spec 的新选项集意味着**移除**该选项的呈现，是对既有交互裁决的静默推翻（spec 未引用 r2-S3、未给理由）。
- **建议**：显式声明 DANGEROUS 卡交互变更及理由（去掉「同意同类型」呈现 vs 保留并追加第四项成四选项），二者取一写清；避免实现者对「三选项形态」与现网三选项（含同意同类型）的差异产生困惑。

### S5. 审批卡路径的 rollback 失败行为未定义（GitError 会击穿本轮任务）

- **维度**：结构与可执行性（错误处理完备性）
- **位置与摘录**：spec §3.5 选中流程「`store.rollback(...)` → 返回 ApprovalDecision」无异常分支；§五「git 命令失败（回退时）| 报错保持现状……」仅覆盖 /rollback 路径。现网 `loop.py` L127-133 的 `BaseException` 处理会 salvage 后 re-raise，`cli.py` repl 顶层捕获后本轮终止（「✘ 本轮执行失败」）——即用户只是选了「拒绝并回退」而回退遇到 git 失败时，整轮任务被杀死，而非降级为普通拒绝。
- **建议**：§3.5/§五 补充审批卡路径 rollback 失败的行为定义（建议降级为 `reject` + 显式提示「回退失败，请 git status 检查」，拒绝语义本身不丢失），并纳入 §七异常用例。

### S6. 测试计划偏差：错误路径无用例；测试文件命名与概设 §11 清单不一致

- **维度**：结构与可执行性
- **位置与摘录**：spec §七 7 个用例均为正路径（含非 Git 降级），§五 的「回退时 git 失败／索引损坏重新起步／truncate 写失败／A 项移除失败（只读）」四类错误面无用例对应；spec §一/§七：「新增 `tests/test_checkpoint.py`（真 git 临时仓库）」单文件，而概设 §11 测试增补清单为「`test_checkpoint_git.py`（快照/回退/保留淘汰/非 Git 降级）」＋「`test_rollback_context.py`（上下文回退选项）」两个文件，M6.1 任务「概设 §11 增补清单（7 个新测试文件）＋ 全量回归」按清单对照时会错位。
- **建议**：§七补 1~2 个异常路径用例（至少「回退 git 失败」与「索引损坏重建」）；文件组织二选一显式对齐——要么拆为概设清单命名，要么在 spec 声明合并为单文件、同步更新概设 §11/M6.1 的对照口径。

### S7. 决策 1 修正声明的覆盖面不全；空仓库分支细节建议补明

- **维度**：概设一致性
- **位置与摘录**：决策 1 标题「**快照用临时索引 write-tree 而非 `git stash create`（对概设 §5.1 的显式修正）**」——登记显式、核心论证成立（untracked 内容还原必须入快照；概设 §5.2 性能行「untracked 经 `--include-untracked` 语义由 stash create 处理」在技术上不成立，spec 的修正方向正确）。但修正声明只点名 §5.1，未覆盖同样描述 stash create 的概设 §5.2「性能」行与开发计划表 4.1「`stash create / update-ref refs/glaucous/* / diff 清单 / restore 封装`」的任务措辞——后续实现者翻上游文档会被误导。另决策 1 五步中「`git read-tree <HEAD 或空树>`」已覆盖 read-tree 的空仓库分支，但「`git commit-tree <tree> -p <HEAD>`」在 `head=None`（§3.1：空仓库 rev-parse HEAD → None）时应省略 `-p` 的分支未写明。
- **建议**：决策 1 的修正声明补挂概设 §5.2 对应行与计划表 4.1 措辞；补一行「head=None 时 read-tree 用空树（`--empty`），commit-tree 省略 `-p`」。

### S8. gitignored 文件的回退面语义未定义

- **维度**：结构与可执行性（边界登记）
- **位置与摘录**：spec §六 2 仅登记「A = 工作树有、快照树无」并声明 `.glaucous/` 不进回退面，但未讨论被用户 .gitignore 排除的文件：`git add -A` 不会把 ignored 文件写入临时索引（决策 1 的快照面 = tracked + 非 ignored untracked），则 checkpoint 后新建的 ignored 文件（如 .env、构建产物）按 §六 2 的字面定义属于「A 项」却又不入 diff/status 的常规输出——是否移除取决于实现细节，存在歧义（用户对「新建的 .env 被删/没删」都会有预期）。
- **建议**：显式登记 ignored 文件语义（建议声明为「不进快照、不进回退面，回退保持不动」，这与临时索引方案的天然行为一致），消除实现歧义。

### S9. 「checkpoint_id」与 `seq` 术语不统一

- **维度**：结构与可执行性（术语统一）
- **位置与摘录**：§一 接线图与决策 4 用「checkpoint_id 挂 ctx.turn_checkpoint_id」，而 §3.2 的 `Checkpoint` 数据类字段为 `seq`（无 id 字段）、§3.5 实际调用 `store.get(ctx.turn_checkpoint_id)` 且 `get` 签名为 `get(self, seq: int)`——`turn_checkpoint_id` 实际承载的是 seq。
- **建议**：统一命名为 `turn_checkpoint_seq`（或给 Checkpoint 加 id 别名声明），避免实现时类型误解。

## 四、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 需求一致性 | 硬约束符合性：§5 约束 6「git 子进程调用、不引入 Git 库依赖、零新依赖」——决策 1 全程 `subprocess.run` 封装，无新依赖；checkpoint 回退属裁剪底线未被裁 | ✓ |
| 需求一致性 | FR-40~43 逐条覆盖：FR-40（每轮入口自动创建＋非 Git 降级，提示时机见 S2）、FR-41（50 可配＋淘汰＋/rollback 可查）、FR-42（默认只回文件＋上下文二问默认否＋变更清单确认卡）、FR-43（第四选项＋回退本轮入口）均有设计落实 | ✓（B1/B2/B3 涉及的达成度除外） |
| 需求一致性 | 任务 4.1~4.5 逐条落实：git_snapshots 封装（4.1）、store＋索引＋保留淘汰＋loop 接线（4.2）、/rollback 三段编排（4.3）、审批拒绝联动（4.4）、单测（4.5） | ✓ |
| 需求一致性 | 验收标准可判定：场景 H 三条验收均有对应用例（用例 1/3/5/6）与 e2e 手工复现安排；「第 51 个创建后最旧淘汰」与 §3.2「先 append 再 _evict」口径一致 | ✓（「git status 干净」受 B1 影响不可达成） |
| 需求一致性 | 无范围蔓延：决策 6 权限矩阵评估显式登记 TODO 不实施；§八 声明 M5 任务级 checkpoint 仅要求 API 可复用、不在本次范围 | ✓ |
| 概设一致性 | 模块落位：`checkpoint/`（git_snapshots.py/store.py）与概设 §11 工程结构增补一致；commands/cli/history/config/loop 改动点均落在概设 §10/§11 声明的位置 | ✓ |
| 概设一致性 | 决策 1 对概设 §5.1 的修正：登记显式（标题注明）、论证核心成立（stash create 无法捕获 untracked，概设 §5.2 的 `--include-untracked` 表述技术性错误）、满足 FR-40「不污染工作树与 stash、ref 不被 gc、零新依赖」约束且严格强于原方案（声明覆盖面见 S7，diff 步缺口见 B1） | ✓（修正本身成立） |
| 概设一致性 | 决策 3/5/6 与概设关键语义吻合：排除 `.glaucous/`（防审计与会话文件进回退面）、不自动 git init（引用概设 §5.2 正确）、回退写审计（概设 §5.2「event: checkpoint_create / rollback」）、seq/ref 命名空间与索引数据模型同构 | ✓ |
| 概设一致性 | 引用正确性：关联规格链接全部可达；R3/R6/D8 内部引用在 cli.py/commands.py 注释有出处；「概设 §5.1/§5.2」引用与实际内容相符 | ✓ |
| 结构与可执行性 | 决策 2 接线点与代码吻合：`loop.py run()` 现状为 `reset_parse_counter()` → `push_user(task)`（L76-77），spec 的 create 插入点与「message_count 在 push_user 之前取」的语义可行；`SubagentRunner`（subagent.py L201-211）构造子 loop 不传新可选参数即可满足「子 agent 不注入 store」，可行性成立 | ✓ |
| 结构与可执行性 | §3.3 与既有落盘格式吻合：History JSONL 首行 session_meta＋逐行消息（history.py L203-214/237-246），「首行 meta 不动、其余行重写」的描述与格式一致；truncate 的 JSONL 重写复用 append 式格式约定成立 | ✓（配对校验缺口见 B3） |
| 结构与可执行性 | §3.4/§3.5 与既有交互协议吻合：`select_with_arrows`（Esc→None 取消语义）、live_hooks pause/resume（decide L326/394 先例、R3 协议）、make_card、rebuild_loop 装配（L1142-1168，参数清单与 AgentLoop 签名匹配、追加 checkpoint_store 可行）、/clear 的 session_usage/last_budget 重置先例、ctx.active_agent == "主 agent" 归属口径（commands.py L166 默认值＋subagent.py L216 置 child-N）、轮末 finally（cli.py L1638-1651 turn_active 复位点）均有现网先例 | ✓ |
| 结构与可执行性 | config 扩展可行：`Config` 为 frozen dataclass，追加带默认值字段不破坏既有构造；`_load_positive_int(source, key, default)` 已存在（config.py L117-122），复用声明成立；原子写 tmp+replace 有 sessions/index.py `_write` 先例 | ✓ |
| 结构与可执行性 | §五 错误处理主面覆盖：非 Git 降级、创建失败审计＋轮内一次 warning、回退失败显式告知、索引损坏重建（ref 残留无害、update-ref 幂等）、淘汰口径、逐文件移除失败汇总——除 S5/S6 指出的两处缺口外覆盖合理 | ✓（部分） |
| 结构与可执行性 | §七 测试计划覆盖验收标准与关键决策：决策 1（用例 2 untracked 跨轮还原）、决策 2（用例 7 接线＋子 loop 不创建）、决策 3（用例 5）、决策 4（用例 6）、保留策略（用例 3）、非 Git（用例 4）、回退精确性（用例 1）；fixture 经 `-c` 注入身份不依赖全局配置 | ✓（错误路径见 S6） |

## 五、复审要求

- 结论为**不通过**：必须修复 **B1、B2、B3** 后复审（B1/B2 触及 FR-40/42/43 的核心达成路径，B3 触及概设 §5.1 明确要求的配对校验机制，按波及面规则，复审时无论章节改动与否均需重查三项的修复闭环与验收标准可达成性）。
- 建议项 S1~S9 登记待办，随本轮修复顺带处理成本最低（尤其 S1/S4/S7 与阻塞项同章节）；不作为放行前置条件。
