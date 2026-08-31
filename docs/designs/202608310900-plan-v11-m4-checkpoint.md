# V1.1-M4 Checkpoint Spec（Git 快照 / 保留策略 / /rollback / 拒绝联动回退）

- 创建日期：2026-08-31
- 状态：已批准（spec 评审 4 轮：r1 B1~B3、r2 B4/B5、r3 B6 修复后 r4 通过，0 建议；代码评审轮次另见 docs/reviews/202608311100-*）
- 上游依据：
  - [编程智能体需求文档v1.1.md](../编程智能体需求文档v1.1.md) §2.2（FR-40~43）、§5（约束 6：checkpoint 依赖 Git 子进程，零新依赖）
  - [编程智能体概要设计说明书v1.1.md](../编程智能体概要设计说明书v1.1.md) §2.3（Checkpoint 存储选型）、§5（Checkpoint 模块：快照机制/关键语义）
  - [Glaucous开发计划表v1.1.md](../Glaucous开发计划表v1.1.md) V1.1-M4 任务 4.1~4.5 与验收标准（场景 H）
- 前置状态：v1.1-M1/M2/M3 已交付并推送（`aee703f`）；基线测试 **239 passed**（WSL 环境 `~/miniconda3/envs/glaucous/bin/python -m pytest tests/ -q`）；当前分支 `main`（单主干）
- 决策记录（本 spec 关键取舍，评审重点）：
  1. **快照用临时索引 write-tree 而非 `git stash create`（对概设 §5.1/§5.2 与计划表 4.1 的显式修正，S7）**：`git stash create` 只快照已跟踪文件、无法捕获 untracked（概设 §5.2 性能行「untracked 经 `--include-untracked` 语义由 stash create 处理」的技术表述不成立——`git stash create` 无此参数，计划表 4.1 的「stash create」措辞同源）——两个验收点因此落空：①「checkpoint 之后新增的文件 → 移除」需要快照里有 untracked 的"有无"信息；②上一轮创建的 untracked 文件在本轮被修改后，tracked-only 快照无法还原其 checkpoint 时刻内容。改为：`GIT_INDEX_FILE=<临时索引> git read-tree <HEAD 或空树>` → `git add -A -- .` → 对每个排除名**分别**执行 `git rm -q --cached -r -- ':(glob)**/.glaucous/**'` 与 `':(glob).glaucous/**'`（r3-B6：两条 glob 拆独立调用——git rm 多 pathspec 的匹配检查是原子的，合并调用在「深层命中 + 根级无条目」组合下整条失败；无匹配容忍）→ `git write-tree` → `git commit-tree <tree> -p <HEAD>` → `git update-ref refs/glaucous/checkpoints/<seq> <commit>`。全程不触碰用户工作树与真实索引、不污染 stash 列表、对象有 ref 引用不被 gc——满足 FR-40 全部约束且严格强于原方案。`commit-tree` 经 `-c user.name=glaucous -c user.email=glaucous@local` 注入身份，不依赖用户全局 git 配置。
  2. **快照创建接线在 AgentLoop.run() 入口（计划表 4.2 口径）**：AgentLoop 构造器增可选 `checkpoint_store=None` 与 `on_checkpoint=None`；run() 入口（push_user 之前）`store.create(task, message_count=len(messages), anchor_digest=…)`，成功则调 `on_checkpoint(cp)` 外泄。子 agent loop（SubagentRunner）两者都不注入 → 天然不产生子 checkpoint；M5 任务级 checkpoint 复用同一 store API（`create(summary, …)`）。repl 作用域在 rebuild_loop 时把 store 与回调传入主 loop（回调写 `ctx.turn_checkpoint_seq`，S9 术语：Checkpoint 无 id 字段，全用 seq）。
  3. **上下文回退映射**：checkpoint 索引记录 `message_count`（创建时刻 `len(history.messages)`，即本轮入口）+ `anchor_digest`（B3）；「同时回退上下文」= `History.truncate_to(count, anchor_digest)`（校验通过后内存截断 + JSONL 重写为 meta 行 + 截断后消息，复用既有追加式落盘的格式约定）。回退到更早的 checkpoint 即截断更多轮次——单一机制覆盖任意深度。
  4. **拒绝联动（FR-43，B2 修复后的挂接路径）**：`ApprovalDecision.choice` 扩展 `"reject_rollback"` 字面量；审批卡在「主 agent 且 `ctx.turn_checkpoint_seq` 非空」时提供第四选项。回调侧选择该项时立即执行文件回退（回退到 `ctx.turn_checkpoint_seq`，只回文件不动上下文；回退失败降级为普通拒绝并提示，S5），gate 将 `reject_rollback` 映射为拒绝分支：审计 + 回喂「用户拒绝并已回退：{reason}」。子 agent（`ctx.active_agent != "主 agent"`）与非 Git 工作区不提供该选项（子 agent 无本轮入口 checkpoint）。
  5. **快照排除 `.glaucous/`（任意层级，r2-B5 裁决；r3-B7 补充回退面）**：运行时目录（会话/审计/索引/输出）不进快照——防止审计与会话文件进入回退面（回退审计日志 = 审计失真）；实现为目录名段语义：路径中任一段等于 `.glaucous` 即排除，覆盖仓库根级、子目录工作区、同仓其他实例；**已知边界（r4-S15）**：glob 排除形态（`**/.glaucous/**`）不命中「名为 .glaucous 的裸文件」——按文件而非目录使用该名字的极端形态不进排除面，接受；**回退面同语义**（r3-B7）：`restore` 必须保留 exclude pathspec（pathspec 限定 restore 触碰的路径集合，省略则「用户 tracked 的 .glaucous 文件」会被整体删除）；快照排除仅覆盖「新条目不进快照」，两者缺一不可；
  6. **权限矩阵联动声明**：M3 决策「区内写维持现状，checkpoint 落地后再评估放宽」——本 spec 落地 checkpoint 后该评估窗口打开，但**本次不改动权限矩阵**（2026-08-31 用户决策：选 A 维持现状，评估关闭；登记 TODO 销账）。
  7. **概设 §10/§11 的 `auto_rollback_on_reject` 配置项与 checkpoint「可关」开关不在本批实现**（S3）：拒绝联动默认开启且不可关——auto-approve 默认下拒绝卡本身就是底线时刻，多一选项无风险；登记 TODO 偿还（M6 测试与评测期统一收口）。env 变量替代 config.toml 配置机制沿用 M3 既有口径（config.toml 是 M5+ 议题，不在本批引入第二配置源）。

---

## 一、总体架构与分层影响分析

```
repl 任务轮（cli.py）
  ctx.checkpoint_store（CheckpointStore，repl 装配，重建/切换时沿用）
    └─ 传入主 AgentLoop（子 agent loop 不传）
        └─ loop.run(task) 入口 → store.create(task, …) → on_checkpoint(cp) → ctx.turn_checkpoint_seq = cp.seq
/rollback（commands.py）→ 列表选择 → 变更清单确认卡 → store.rollback / History.truncate_to
审批卡（cli.py make_decision_callback）→「拒绝并回退」→ store.rollback + ApprovalDecision("reject_rollback")

checkpoint 外泄路径（B2）：loop.run 入口 store.create 成功 → store.last_created 更新
  → AgentLoop.on_checkpoint 回调（主 loop 接线为 `lambda cp: setattr(ctx, "turn_checkpoint_seq", cp.seq)`）
  → 轮开始时 ctx.turn_checkpoint_seq 置 None → 审批卡与轮末 finally 读/清该值
```

| 层 | 模块 | 影响 |
|---|---|---|
| 新模块 | `src/glaucous/checkpoint/`（新增包） | `git_snapshots.py`（git 子进程封装）、`store.py`（索引/保留淘汰/回退编排） |
| 权限层 | `permission/approval.py`（S1） | `ApprovalDecision.choice` Literal 增 `"reject_rollback"`；gate 增该分支（映射拒绝语义：审计 + 回喂「用户拒绝并已回退」）；审计 decision 字段取 choice 原样（含 reject_rollback） |
| 配置 | `config.py` | `Config` 增 `checkpoint_max_keep: int = 50`（env `GLAUCOUS_CHECKPOINT_MAX_KEEP`，复用 `_load_positive_int`） |
| 持久层 | `context/history.py` | 增 `truncate_to(count, anchor_digest)`（锚校验 + 内存截断 + JSONL 重写）；其余零改动 |
| 状态层 | `commands.py` | ReplContext 增 `checkpoint_store`/`turn_checkpoint_seq`；新命令 `_cmd_rollback`；COMMAND_META/USAGE/分派扩充 |
| 接线层 | `cli.py` | repl 装配 store；rebuild_loop 传入主 loop + on_checkpoint 回调；审批卡第四选项；轮开始置 `turn_checkpoint_seq=None`、轮末 finally 清理 |
| Agent 层 | `agent/loop.py` | AgentLoop 构造器增 `checkpoint_store=None`/`on_checkpoint=None`；run() 入口创建（失败降级警告，不阻断） |
| 测试 | `tests/` | 新增 `tests/test_checkpoint_git.py`（真 git 临时仓库）+ `tests/test_rollback_context.py`（对齐概设 §11 清单命名，S6） |

---

## 二、数据模型

`.glaucous/checkpoints.json`（工作区内，与 audit.log 同级）：

```json
{ "version": 1, "seq": 7,
  "checkpoints": [
    { "seq": 7, "ref": "refs/glaucous/checkpoints/7",
      "commit": "abc1234...", "created_at": "2026-08-31T09:00:00",
      "task": "修复登录 bug", "message_count": 42,
      "anchor_digest": "a1b2c3d4e5f6" } ] }
```

- `anchor_digest`（B3 修复）：创建时刻 `messages[-1]` 内容的 sha256[:12]——L2 压缩会在轮间把 `messages[:split]` 原位替换为摘要消息，使 `message_count` 失效；截断前校验锚（§3.3），不一致则拒绝截断，绝不静默错位；**空历史首轮（新会话//clear 后第一个任务）为空串哨兵**（B4，§四）。

- `seq` 全局单调递增（跨会话共享——同工作区的所有会话回退同一文件面，与「会话切换不回退文件」的职责分离决策一致）；
- `message_count` 供上下文回退映射（决策 3）；
- Git ref 命名空间 `refs/glaucous/checkpoints/<seq>`：不被 gc（有 ref 即有引用），`update-ref -d` 删除即淘汰。

---

## 三、接口定义

### 3.1 `checkpoint/git_snapshots.py`（git 子进程封装，全部返回值/异常显式）

```python
def _run(root: Path, *args: str, timeout: int = 30, env_extra: dict[str, str] | None = None) -> str:
    # 全命令注入 core.quotepath=off（r2-B1：非 ASCII 路径不被八进制转义）

class GitError(RuntimeError): ...          # 非零退出码/找不到 git/rev-parse 失败
# S1 处置（交付后作者确认）：不引入独立 NotGitWorkspace——非 Git 工作区由
# is_git_workspace 探测先行拦截，rev-parse 失败统一并入 GitError，行为等价

def is_git_workspace(workspace: Path) -> bool
def repo_root(workspace: Path) -> Path                      # rev-parse --show-toplevel
def head_commit(root: Path) -> str | None                   # rev-parse HEAD；空仓库 → None
def create_snapshot(root: Path, message: str, excludes=(".glaucous",)) -> str  # 决策 1 临时索引五步（含 rm --cached 任意层级排除，r2-B5）
def diff_against(root: Path, ref: str, excludes=(".glaucous",)) -> list[dict]  # → [{status: M/D/A, path}]；M/D 来自 diff --name-status（R/C 重命名取旧路径列为还原项，r2-S6），A 项 = ls-files ∪ others --exclude-standard − ref 树；两路均按 excludes 目录名段语义过滤（r2-B5）；
def restore_from(root: Path, ref: str, excludes=(".glaucous",)) -> None        # restore --source=<ref> --worktree --staged -- . + exclude pathspec（r3-B7：限定触碰路径集合，tracked 的 .glaucous 文件不进回退面）
def delete_ref(root: Path, ref: str) -> None                # update-ref -d
```

- **`diff_against` 的 A 项来源（B1 修复）**：`git diff --name-status <ref>` 只覆盖已跟踪文件的 M/D，产不出「工作树有、快照树无」的新增项。A 项另行计算：`tracked = git ls-files` ∪ `untracked非忽略 = git ls-files --others --exclude-standard`，减去 `git ls-tree -r --name-only <ref>` 集合 → 逐项 status=A。M/D 项来自 `git diff --name-status <ref>`；
- 统一 `subprocess.run(cwd=root, timeout=30)`；超时/非零码 → `GitError`（带 stderr 摘要）；
- 快照排除步骤（r2-B5）：`add -A -- .` 后经 `rm -q --cached -r -- ':(glob)**/.glaucous/**' ':(glob).glaucous/**'` 从临时索引移除（决策 5）；空仓库（head=None）时 `read-tree` 读空树、`commit-tree` 省略 `-p` 参数；
- **gitignored 文件语义（S8 显式设计）**：快照不捕获（`add` 尊重 ignore 规则）、A 项排除（`--others --exclude-standard`）——gitignored 文件（构建产物/依赖目录）不进回退面，`__pycache__` 之类不会被误删。

### 3.2 `checkpoint/store.py`

```python
@dataclass
class Checkpoint:
    seq: int; ref: str; commit: str; created_at: str; task: str; message_count: int
    anchor_digest: str   # B5：与 §二 索引字段同源（空历史首轮为空串）

class CheckpointStore:
    def __init__(self, workspace: Path, audit: AuditLog, max_keep: int = 50): ...
    @property
    def available(self) -> bool            # 惰性探测一次（rev-parse），失败即 False（缓存）
    def unavailable_reason(self) -> str    # 「当前工作区不是 Git 仓库，checkpoint 不可用」/ GitError 摘要
    def create(self, task: str, message_count: int, anchor_digest: str) -> Checkpoint | None   # 失败返回 None（含非 Git 早退审计 ok=false）
    last_created: Checkpoint | None        # B2：create 成功即更新（loop 经 on_checkpoint 回调外泄给 repl）
    def take_warning(self) -> str | None   # B3：首次创建失败的一次性告警文案（之后 None，loop 经 note 事件呈现）
    def list(self) -> list[Checkpoint]     # 新→旧（损坏条目过滤）
    def get(self, seq: int) -> Checkpoint | None
    def rollback(self, cp: Checkpoint) -> list[dict]   # restore + 移除 A 项 + 审计；返回变更清单（含 failed_remove 子清单）
    def _load_index(self) -> dict          # 损坏/缺失 → 空索引起步；seq 错型归一化（r3-S12）
    def _evict(self, index: dict) -> None  # 超 max_keep 删最旧 ref + 清索引行
```

- `create`：非 Git/异常 → 审计 `checkpoint_create`（ok=false）+ 返回 None；成功审计 `checkpoint_create`（ok=true，seq/commit）；
- `create` 内部先 append 索引再 `_evict()`——**第 51 个创建后最旧被淘汰**（验收口径）；
- `rollback`：`restore_from` → 对 `diff_against` 中 status==A 的文件逐一 `Path.unlink`（缺失容忍）→ 审计 `rollback`（seq/变更数）；
- 索引读写：原子写（tmp + replace），损坏/缺失 → `{version:1, seq:0, checkpoints:[]}` 重新起步（ref 仍在 Git 侧，无孤儿危害；重建不扫描 ref——索引轻量可丢，Git ref 才是快照真相源，但 ref 无元数据故不逆向重建，接受丢失索引即失去对应快照入口的边界，登记 §六）。

### 3.3 `History.truncate_to(count, anchor_digest)`（FR-42 上下文回退）

```python
def truncate_to(self, count: int, anchor_digest: str) -> None
```

- **锚配对校验（B3 修复，落实概设 §5.1「配对校验」要求）**：`count > len(messages)` 或（`count > 0` 且 `sha256(messages[count-1])[:12] != anchor_digest`）→ 抛 `ContextAnchorMismatch`（截断点已被 L2 压缩/轮转改变，无法精确截断）——调用方提示「对话上下文已变更（已被压缩或轮转），本次仅回退文件」，绝不静默 no-op 或错位截断；`anchor_digest == ""` 为空历史首轮哨兵（B4），仅允许与创建时 `message_count == 0` 的 checkpoint 配对——**此时截断到 0 条 = 清空对话上下文（JSONL 仅 meta 行），是真实截断操作而非 no-op**（B6 消歧：与决策 3「单一机制」及概设「截断至对应轮次前」一致）；
- 校验通过 → 内存截断 + JSONL 重写（首行 meta 不动，其余行重写为截断后消息）；
- 写失败（OSError）→ 抛出，由调用方报错（文件已回退、上下文未动——部分回退须显式告知，禁止静默）。

### 3.4 `/rollback` 命令（commands.py `_cmd_rollback`，FR-42）

1. `store.available` 为 False → `unavailable_reason()` 提示，返回（不自动 `git init`，概设 §5.2）；
2. `store.list()` 为空 → 「暂无可用 checkpoint」；
3. **列表选择**：`select_with_arrows`（复用 v1.1 R6 机制），行 = `seq · 时间 · 任务摘要`，Esc 取消；
4. **变更清单确认卡**：`store` 侧 `diff_against(cp.ref)` → M/D 项「将还原」、A 项「将移除」，make_card 呈现（≤10 行逐条 + 溢出计数摘要）；箭头确认/取消；
5. 确认后 `store.rollback(cp)` → 汇总提示（含「未能移除」子清单）；若抛 `GitError` → 报错保持现状 + 提示 `git status` 检查，**不继续上下文二问**（S5）；追加**上下文二问**（箭头 是/否，默认否）：是 → `history.truncate_to(cp.message_count, cp.anchor_digest)`（`ContextAnchorMismatch` → 提示「对话上下文已变更（已被压缩或轮转），本次仅回退文件」，S6 错误面用例）+ `session_usage`/`last_budget` 重算提示 + rebuild_loop（与 /clear 同路径，D8）；否 → 提示「对话上下文已保留，模型仍记得后续操作」；
6. 阻塞交互前后遵守 live_hooks pause/resume 协议（v1.1 R3 既有约定）。

### 3.5 审批卡「拒绝并回退」（cli.py `make_decision_callback`，FR-43）

- 选项集（S4 对齐 r2-S3「呈现不分列」既有裁决：DANGEROUS 卡也呈现全部选项，批量豁免安全性由 gate 守卫兜底）：箭头形态统一 `["同意", "同意同类型", "拒绝", "拒绝并回退"]`；数字形态在现状基础上增 `[d]` 键 = 拒绝并回退（`[b]` 键维持现状仅非 DANGEROUS 可用）；
- 提供条件：`ctx.active_agent == "主 agent"` 且 `ctx.turn_checkpoint_seq is not None`（非 Git/创建失败/子 agent 均不提供，退化为三选项现状）；
- 选中 → try `store.rollback(store.get(ctx.turn_checkpoint_seq))`（只回文件；`GitError` → 提示「回退失败，已按普通拒绝处理」并降级返回 reject，S5）→ 返回 `ApprovalDecision(choice="reject_rollback", reason=...)`；
- 数字回退形态：`[d]` 键；箭头形态多一行；取消语义同现状（Esc/取消 = 普通拒绝，不回退）；reason 经既有 `_reject_reason()` 输入保护；
- 轮末 finally 清理 `ctx.turn_checkpoint_seq = None`。`turn_checkpoint_seq` 生命周期：轮开始置 None → loop.run 入口 on_checkpoint 写入 → 轮末 finally 清理。`ctx.checkpoint_store` 经 ctx 间接引用（D8，/clear、/resume 重建后仍有效）。

---

## 四、loop.run() 入口接线（agent/loop.py）

```python
class AgentLoop:
    def __init__(..., checkpoint_store: CheckpointStore | None = None,
                 on_checkpoint: Callable[[Checkpoint], None] | None = None): ...

async def run(self, task: str) -> str:
    self._registry.reset_parse_counter()
    if self._checkpoint_store is not None:
        try:
            # B4：空历史（新会话//clear 后首轮）messages[-1] 不存在 → 锚为空串哨兵，
            # 不允许 IndexError 被 except 吞掉（否则每会话第一个任务永远无 checkpoint，
            # 违反 FR-40「每轮入口」）
            anchor = history_digest(self._history.messages[-1]) if self._history.messages else ""
            cp = self._checkpoint_store.create(
                task,
                message_count=len(self._history.messages),   # push_user 之前（决策 3）
                anchor_digest=anchor,
            )
        except Exception:                                   # noqa: BLE001 —— 兜底设施失败不阻断任务轮
            cp = None
        if cp is not None and self._on_checkpoint is not None:
            self._on_checkpoint(cp)                          # B2：主 loop 接线为写 ctx.turn_checkpoint_seq
    self._history.push_user(task)
    ...
```

- `store.create` 抛任何异常 → 捕获降级为 warning（经 on_event "note" 伪事件或静默 + 审计 ok=false），**不阻断任务轮**（checkpoint 是兜底设施，不可用性不应杀死会话）；
- message_count/anchor_digest 在 push_user 之前取——「本轮入口」语义与决策 3 一致；
- B2 外泄链：store.create 成功即更新 `store.last_created`（供测试断言）；loop 经 `on_checkpoint(cp)` 回调外泄，主 loop 接线为 `lambda cp: setattr(ctx, "turn_checkpoint_seq", cp.seq)`——loop 不持有 ctx（职责分离，与 M3 thinking 挂账同模式）。

---

## 五、错误处理策略

| 场景 | 行为 |
|---|---|
| 非 Git 工作区 | store.available=False（缓存）；/rollback 降级为不可用提示、**「拒绝并回退」不提供该选项（退化为三选项现状）**（S10 口径统一，与 §3.5 一致）；启动不探测不告警，**首次创建尝试失败时 warning 一次**（S2：满足 FR-40「明确提示不可用原因」且不每轮打扰，对概设 §5.2「创建时检测失败 → 提示」的落地口径） |
| git 命令失败/超时（创建时） | 审计 ok=false + 轮内一次 warning 提示；任务照常执行 |
| git 命令失败（回退时） | 报错保持现状（工作树可能部分还原——提示用户 `git status` 检查；restore 单命令原子性有限，显式告知优于假装成功） |
| 索引损坏/缺失 | 空索引重新起步（ref 残留无害；`update-ref -d` 幂等） |
| truncate 写失败 | 抛出 → /rollback 报「文件已回退但对话截断失败」，不重试不静默 |
| 第 51 个 checkpoint | create 内 `_evict()`：`delete_ref(最旧)` + 清索引行（验收口径） |
| 新增文件移除失败（如只读） | 逐文件 try，失败项汇入回退汇总的「未能移除」提示 |

---

## 六、已知边界（显式登记，不做偿还计划项的说明）

1. **索引丢失 = 快照入口丢失**：checkpoints.json 损坏后不逆向扫描 refs 重建（ref 无任务摘要/时间元数据，重建出的条目不可用）。Git 侧 ref 残留由下一次 `_evict` 逐渐消化（seq 重新计数后 ref 名重叠，`update-ref` 覆盖写，无泄漏）。接受理由：索引是工作区文件，随 `.glaucous/` 生命周期同损同荣；真快照在 Git 对象库中，`git reflog`/`refs/glaucous` 仍可人工找回。
2. **A 项移除只处理快照后新建文件**：`diff_against` 的 A = 工作树有、快照树无。快照排除 `.glaucous/`（决策 5），故 `.glaucous/` 内增删不进回退面——这是有意设计非缺陷。
3. **回退不触发审批**：/rollback 与拒绝并回退本身不走 permission gate（用户显式操作 + 确认卡已是审批形态）；但写入 audit.log 留痕（概设 §5.2）。

## 七、测试与验证方式（计划表 4.5 + 场景 H）

新增两文件（S6 对齐概设 §11 清单命名；fixture：tmp_path 内 `git init` + 提交初始文件，身份经 `-c` 注入不依赖全局配置）：

`tests/test_checkpoint_git.py`（快照/回退/淘汰/降级）：

1. **快照/回退精确性**：turn1 建文件 A → checkpoint → 修改 A + 新建 B → 回退 → A 内容还原、B 被移除（A 项来自 ls-files ∪ others 减 ref-tree，B1）、`git status` 对 HEAD 无 M/D 残留；
2. **untracked 跨轮还原**：turn1 新建 untracked C → turn2 checkpoint → turn2 修改 C → 回退到 turn2 → C 为 turn1 内容（决策 1 的核心动机用例）；
3. **保留淘汰**：max_keep=3 建第 4 个 → 最旧 ref 消失（`rev-parse <ref>` 失败）+ 索引行移除；
4. **非 Git 降级**：纯目录（无 .git）→ available=False、create 返回 None、/rollback 提示文案；
5. **gitignored 排除**：.gitignore 的产物文件修改/新建 → 不进快照也不进 A 项（S8）；
6. **错误面**：restore 抛 GitError → /rollback 报错不继续二问；A 项 unlink 失败 → failed_remove 子清单；索引损坏（写入非法 JSON 后 create/list）→ 空索引起步不崩（§五对应用例，S11）；

`tests/test_rollback_context.py`（上下文回退/拒绝联动/接线）：

7. **上下文回退**：构造多轮 History → truncate_to(mid, 锚) → messages 长度与 JSONL 行数一致、meta 完好、re-load 等价；**count=0 + 空串锚 → 清空对话（messages 空 + JSONL 仅 meta 行）**（S12）；锚不匹配（模拟 L2 压缩后回退旧 checkpoint）→ ContextAnchorMismatch；truncate 写失败 → 抛出；
8. **拒绝联动**：mock 回调返回 reject_rollback → gate 拒绝分支 + 回喂文案 + 文件已回退（真 store）；回退抛 GitError → 降级普通拒绝（S5）；
9. **loop 入口接线**：FakeLLM 单步任务，断言 store.last_created 更新且 on_checkpoint 收到 seq、message_count 为 push_user 前长度；**空历史首轮（新 History）anchor_digest 为空串且 create 成功**（B4）；子 agent loop（store=None）不创建；create 抛异常 → 任务照常完成。

验收（计划表场景 H）：e2e 手工复现——让 agent 改坏文件 → /rollback 回退 → `git status` 干净、对话保留；第 51 个淘汰最旧；拒绝并回退走通。全量回归 239 基线守恒。

## 八、范围裁剪声明

无裁剪。任务 4.1~4.5 全量实现；M5 的「任务级 checkpoint」仅要求本 spec 的 store API 可复用（`create(summary, …)` 通用签名），不在本次范围；`auto_rollback_on_reject` 配置与 checkpoint 可关开关登记 TODO 偿还（决策 7）。
