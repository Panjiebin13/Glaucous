# V1.1-M5 Spec 子系统 Spec（状态机 / 澄清起草 / 双评审循环 / 执行管线 / 命令面）

- 创建日期：2026-08-31
- 状态：已批准（经 2 轮评审；r1 不通过 B1+S1~S12 修复后，r2 有条件通过，3 条建议随编码落地同步销项）
- 上游依据：
  - [编程智能体需求文档v1.1.md](../编程智能体需求文档v1.1.md) §2.4（FR-52~59）、§4（裁剪底线：Spec 双评审循环不可裁）
  - [编程智能体概要设计说明书v1.1.md](../编程智能体概要设计说明书v1.1.md) §7（Spec 子系统：状态机/文档结构/双评审循环/执行管线/命令面）、§11（工程结构增补：spec/ 包）、§10 配置样例（[review].max_rounds，见决策 9）
  - [Glaucous开发计划表v1.1.md](../Glaucous开发计划表v1.1.md) V1.1-M5 任务 5.1~5.7 与验收标准（场景 G）
- 前置状态：v1.1-M1~M4 已交付（M4 及验收增强未提交，本地工作树）；基线测试 **273 passed**（WSL 环境 `~/miniconda3/envs/glaucous/bin/python -m pytest tests/ -q`）；当前分支 `main`（单主干）
- 决策记录（本 spec 关键取舍，评审重点）：
  1. **编排形态 = 命令层命令式流水线**（`spec/pipeline.py`，决策核心）：`/spec` 命令进入 `SpecPipeline` 异步编排，在**单个命令处理器内**串行推进各阶段；状态经 frontmatter 落盘，**可重入**（中断后 `/spec` 恢复执行）。不走「模型自主驱动状态机」路线——轮次上限、升级点、交互门都是确定性代码，可 mock 回放（任务 5.7 的前提）。
  2. **主 agent 轮次复用 `ctx.loop.run()` + 任务级 checkpoint 显式创建（r1-B1 定案）**：澄清/起草/修订/任务执行/修复五类「主 agent 动作」全部以合成任务消息走现有主 loop（事件渲染、权限管线零改动继承）。任务级 checkpoint 采用**双保险口径**：① **权威快照**——每任务 `loop.run` **之前**由 pipeline 显式调用 `hooks.checkpoint(任务标签)`（真实接线 = `store.create`）：保证快照恰在任务动作发生前、可被测试计数断言、`/rollback` 可精确回到「该任务前」；② `loop.run` 入口的 M4 既有接线仍照常产出一份入口快照（无害冗余，淘汰策略统一处理）。**不采用**「仅靠 loop 入口接线免费获得」方案：mock 回放下不可观测（无法计数断言）、任务标签精度不足（入口快照标签=任务原文而非统一任务标签），且与概设 §7.4「每任务开始前打任务级 checkpoint」的字面时序不严格对齐（r2-S1 修订：入口快照本身在 push_user 之前，否决依据为可观测性/标签精度/字面对齐三点）。
  3. **Spec 入口基线 checkpoint 由 pipeline 显式创建**：批准进入执行时 `store.create("Spec <id> 执行入口", …)`，作为代码评审循环 diff 的基线（概设 §7.3「自任务入口 checkpoint」= 自执行入口）。**基线淘汰降级（r1-S8）**：超长任务清单（任务级快照累计超保留上限）下入口快照可能被 `_evict` 淘汰 → `store.get(基线 seq)` 为 None 时，代码评审降级为仅验收标准模式（同决策 8 的无 diff 口径），报告卡注明「基线快照已被淘汰」。非 Git / store 不可用 → 同降级。
  4. **「任务清单与 todo 联动」语义收窄（对概设 §7.4 的显式修正）**：代码库无 `todo_write` 工具（既有事实，grep 全仓零命中）；本 spec 将概设 §7.4「任务清单与 todo_write 联动」解释为**勾选直接写回 Spec 文档 checkbox**（同句「状态即文档」是定义性表述）——不新增 todo 工具（避免范围蔓延）。进度可见性由：文档 checkbox + `/spec status` 卡 + 执行期每任务抬头行三者承担。
  5. **评审报告机器可读契约**：评审子 agent 任务提示要求首行必为 `评审结论：通过` 或 `评审结论：不通过`，随后 `【阻塞级问题】`/`【建议级问题】` 两节（空则写「无」）。解析规则：前 200 字符内找不到结论行 → **保守判不通过**（报告全文作阻塞证据进修订轮）。该契约与 M2 四段报告规范不冲突（契约写在任务提示层，不改 `build_report`）。
  6. **评审报告超 1000 字截断的回读**（与 M2 报告外置化决策一致）：`SubagentRunner.run` 返回截断报告时，pipeline 依 `metadata.sub_agent` 直读 `.glaucous/outputs/spawn_agent-<agent_id>.log` 取全文（修订喂给与卡片渲染用全文；入史的仍是 ≤1000 字报告，FR-61/63 不变量不破坏）。归档文件缺失 → 用截断文本继续（尽力而为）。
  7. **SubagentRunner 挂账 `ReplContext.subagent_runner`**：`rebuild_loop` 装配后写入（D8：经 ctx 间接引用，/clear、/resume 重建后仍有效）；pipeline 复用同一 runner（agent_id 序号、归属标注、审批同源全部共享）。子 agent 命名经任务提示体现角色（评审员/代码评审员/验收核验员），不改 `spawn_agent` 工具签名（FR-60 契约不动）。
  8. **非 Git 工作区降级**：checkpoint store 不可用 → 执行阶段跳过全部 checkpoint（抬头提示一次）、代码评审循环无 diff 输入（评审输入仅验收标准 + 任务完成报告，报告卡注明）；其余流程不变。
  9. **轮次预算与耗尽升级（全部确定性）**：澄清访谈 ≤3 轮、批准反馈修订 ≤3 轮、Spec 评审 ≤3 轮、代码评审 ≤3 轮（FR-55/57；概设 §10 配置样例 `[review].max_rounds=3` 本次**硬编码 3，不接配置管道**——Config 现无该项，引入属范围蔓延，登记为后续增强）；每类耗尽 → ask 卡升级用户（选项见 §四各节），绝不静默继续或静默终止。
  10. **FR-52「agent 亦可主动建议」= 提示词层**：`BASE_PROMPT` 增一句「大任务可建议用户以 /spec 发起」，不做代码级触发判定（判定标准无法确定性定义，防范围蔓延）。
  11. **验收裁决（FR-58）**：代码评审通过后追加一次「验收核验」子 agent 轮（输入=结构化验收标准 + 任务完成清单 + diff 摘要）→ 逐条 `✓/✗`；全部 ✓ → `verified`，存在 ✗ → `archived` 附未决清单。不引入人工逐条勾选（保持全自动口径，深度介入仅在评审轮提供建议）。
  12. **中断与恢复**：pipeline 顶层捕获 `KeyboardInterrupt/Exception` → 状态停驻当前 frontmatter（如 executing 带已勾选进度）、打印恢复提示；`/spec` 无参且存在 `executing` Spec → 提供「继续执行/取消归档」入口（FR-59「无参且存在执行中 Spec 则显示进度」的落地形态）。`reviewing`/`code_review` 中断不做自动续跑（轮内上下文已失）→ 显示状态并建议 `/spec cancel` 后重发起（已执行任务的代码改动不受影响，有 checkpoint 兜底）。
  13. **ask 卡返回 None 的统一语义（r1-S4）**：`AskCallback` 未响应/中断返回 None（interactive.py 既有契约）。pipeline 各确定性门统一映射：澄清门→取消归档；模式选择→全自动；深度介入建议→空建议继续；批准卡→视为「提修改意见」但意见为空时按归档处理；任务失败三选→归档中止；耗尽升级卡→选最保守项（取消/归档）。原则：**None 一律导向停驻或归档，绝不导向静默推进**（与决策 9 一致）。
  14. **评审子 agent 任务提示词写入「不得调用 ask_user」（r1-S7，概设 §8.3 字面要求）**：三套评审角色提示（评审员/代码评审员/验收核验员）统一附该行；子 agent 的 spawn registry 本含 ask_user（继承父全集去 spawn_agent），提示词约束为声明层防线。
  15. **read_spec 落位 `tools/spec_tool.py`（r1-S3 取舍声明）**：概设 §11 工程结构增补的模块清单写 `spec/tools.py`；本 spec 选择与 `tools/planning.py`（read_plan 同款）并列于工具层，保持「工具注册/风险级/审批」单一归属惯例——偏离已登记，语义不变。
  16. **hooks.ask 装配适配（r1-S2）**：`make_ask_callback`/`AskCallback` 为同步签名；`PipelineHooks.ask` 声明为 `Callable[[str, list[str]], Awaitable[str | None]]`，装配处以 `async def _ask(q, opts): return sync_ask(q, opts)` 薄包装（同步交互内无 await 点，包装无语义损失）；测试 fake 直接给异步函数。

## 一、总体架构

```
用户 → /spec <需求>
        │
        ▼
  SpecPipeline（spec/pipeline.py，命令层命令式编排，决策 1）
        │
        ├─ 澄清访谈：ctx.loop.run(澄清指令)（模型经 ask_user 访谈）
        │     └─ 确定性门：ask 卡「进入起草/继续澄清/取消」（≤3 轮，决策 9）
        ├─ 起草：ctx.loop.run(起草指令+模板) → 终答=Spec 正文 → store.save(draft)
        ├─ Spec 评审循环（≤3 轮）：
        │     模式选择（全自动/深度介入，ask 卡）
        │     每轮：ctx.subagent_runner.run(评审任务, 全文+清单+用户反馈)
        │           → 解析报告契约（决策 5；超长按决策 6 回读）→ 评审报告卡
        │           → 不通过：深度介入先收用户建议（ask）→ ctx.loop.run(修订) → store.save
        │     通过/耗尽升级 → 批准卡（批准/反馈修订 ≤3/归档）→ approved
        ├─ 执行管线：入口基线 checkpoint（决策 3）→ 逐任务：
        │     ① hooks.checkpoint(任务标签)（权威任务级快照，决策 2）
        │     ② loop.run(任务指令+Spec 锚)
        │     → 成功：checkbox 写回（决策 4）；失败：ask（重试/跳过/归档中止）
        │     全部完成 → code_review
        ├─ 代码评审循环（≤3 轮）：
        │     diff = store.get(基线 seq) → preview_changes（非 Git/基线淘汰降级，决策 3/8）
        │     每轮：评审子 agent（验收标准+diff 摘要+清单）→ 发现卡
        │           → 不通过：ctx.loop.run(修复) → 复审
        │     通过/耗尽升级 → 验收核验子 agent（决策 11）→ 验收卡
        └─ verified / archived（frontmatter 闭环，/spec status 可查）
```

| 层 | 模块 | 影响 |
|---|---|---|
| 新模块 | `src/glaucous/spec/`（新增包） | store.py（文档读写/frontmatter/状态机）、templates.py（模板+两套清单）、pipeline.py（编排） |
| 工具层 | `tools/spec_tool.py`（新增） | ReadSpecTool（read_spec，同 read_plan 机制） |
| 命令层 | `commands.py` | _cmd_spec（含 status/cancel 子命令）/_cmd_specs；COMMAND_META/_COMMAND_USAGE/分派新增条目（补全与 /help 的单一数据源，r2-S3）；ReplContext 增 `subagent_runner` 字段 |
| 接线层 | `cli.py` | rebuild_loop 挂账 `ctx.subagent_runner`；build_registry 注册 ReadSpecTool；ARG_COMPLETIONS 增 `/spec` 子命令补全；命令名候选经 commands.COMMAND_META 单一数据源（r1-S12；SLASH_COMMANDS 为零引用遗留清单，不登记，r2-S3） |
| 提示层 | `ui/prompts.py` | BASE_PROMPT 增 /spec 主动建议一句（决策 10） |
| 测试 | `tests/` | 新增 test_spec_store.py / test_spec_pipeline.py（mock LLM + fake runner，任务 5.7） |

**零改动面**（复用即满足，评审可逐条核对）：`agent/loop.py`（checkpoint 接线已在）、`checkpoint/`（store.create/diff 复用）、`agent/subagent.py`（runner 复用，契约不动）、`permission/`（任务轮内审批照常）、`sessions/`、M2 报告机制（决策 6 只是消费既有归档）。

## 二、数据模型：Spec 文档与状态机

落盘位置 `.glaucous/specs/<id>.md`（FR-54），id = `spec-<YYYYMMDD-HHMMSS>`（进程内冲突追加 `-2/-3`）。

### 2.1 frontmatter（YAML 风格，`---` 围栏，解析容错：损坏行跳过取默认）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | str | 与文件名 stem 一致 |
| name | str | 目标一行（起草时模型产出，≤40 字） |
| status | str | 状态机取值（§2.2） |
| created_at / updated_at | ISO8601 | 创建/最近状态变更 |
| approved_at | str \| "" | 批准时刻 |
| round | int | 当前所处轮次（评审/代码评审共用字段，语义随 status） |
| mode | str \| "" | `auto`/`deep`（评审介入模式，批准后留档） |
| entry_checkpoint | int \| null | 执行入口 checkpoint seq（决策 3；非 Git 为 null） |

### 2.2 状态机（概设 §7.1）

```
draft → reviewing → approved → executing → code_review → verified
                        │            │            │
                        └──(修订回环 ≤3 轮)──┘      └→ archived（任何阶段可归档终止）
```

合法迁移表（`store.transition` 强校验，非法迁移抛 `SpecStateError`）：

| from | 允许 to |
|---|---|
| draft | reviewing / archived |
| reviewing | approved / draft（修订回环落盘保持 reviewing，仅 approved/cancel 产生迁移；此处 draft 仅留给「批准反馈-重写」路径不使用，保留完备性） / archived |
| approved | executing / archived |
| executing | code_review / archived |
| code_review | verified / archived |
| verified / archived | （终态，无出边） |

修订回环**不迁移状态**（reviewing 内轮次自增，frontmatter `round` +1），与概设「修订回环 ≤3 轮」箭头语义一致。

### 2.3 正文结构（概设 §7.2，模板见 §3.2）

`## 需求与边界` / `## 澄清记录` / `## 约束` / `## 设计` / `## 任务清单`（`- [ ]` checkbox 行）/ `## 验收标准`（`- [标准] …（验证方式：…）` 结构化行）/ `## 风险与回退`。

## 三、模块接口定义

### 3.1 `spec/store.py`

```python
class SpecStateError(RuntimeError): ...        # 非法状态迁移 / 文档缺失

@dataclass
class SpecDoc:
    meta: dict[str, Any]                        # frontmatter
    body: str                                   # 正文 markdown
    path: Path

    @property
    def spec_id(self) -> str: ...
    @property
    def status(self) -> str: ...
    def tasks(self) -> list[tuple[int, bool, str]]: ...   # (行内序号, 已完成, 任务文本)
    def acceptance(self) -> list[str]: ...                # 验收标准行原文

class SpecStore:
    def __init__(self, workspace: Path): ...    # 目录 .glaucous/specs/
    def create(self, name: str, body: str) -> SpecDoc          # status=draft，id 生成
    def load(self, spec_id: str) -> SpecDoc                    # 缺失 → SpecStateError
    def save_body(self, doc: SpecDoc, body: str) -> None       # 修订回环写回（原子写）
    def transition(self, doc: SpecDoc, to: str, **meta_fields) -> SpecDoc  # 迁移+updated_at
    def check_task(self, doc: SpecDoc, task_no: int) -> None   # checkbox 勾选写回（决策 4）
    def list_all(self) -> list[SpecDoc]                        # 按 created_at 倒序；损坏文件跳过+返回告警串
    def active(self) -> SpecDoc | None                         # 最新非终态（供 /spec 无参）
```

原子写（tmp + replace，同 sessions 索引口径）；`check_task` 按「第 N 个 `- [ ]`/`- [x]` 行」定位（行内序号 = tasks() 序号，修订不改已勾选语义：修订轮只允许改未勾选任务文本，勾选保持——解析层不做强制，文档级说明）。

### 3.2 `spec/templates.py`

```python
SPEC_TEMPLATE: str                 # 七节骨架（§2.3），起草指令注入
SPEC_REVIEW_CHECKLIST: str         # 概设 §7.3：需求边界完整？验收标准可验证？任务清单覆盖且粒度合理？约束无冲突？风险与回退已识别？
CODE_REVIEW_CHECKLIST: str         # 概设 §7.3：逐条对照验收标准？diff 越界？违反 Spec 约束？测试覆盖？
REVIEW_CONTRACT: str               # 报告契约（决策 5）：首行「评审结论：通过/不通过」+ 两节
def render_report_card_lines(report: str) -> list[str]   # 卡片分行（阻塞/建议分节提取，容错）
```

### 3.3 `spec/pipeline.py`

```python
@dataclass
class PipelineHooks:                       # 测试注入点（任务 5.7 回放的前提）
    run_turn: Callable[[str], Awaitable[str]]       # = ctx.loop.run
    run_review: Callable[[str, str], Awaitable[tuple[str, dict]]]  # = ctx.subagent_runner.run → (报告, metadata)
    ask: Callable[[str, list[str]], Awaitable[str | None]]         # 确定性交互门（同步 AskCallback 经薄包装，决策 16；None 语义见决策 13）
    checkpoint: Callable[[str], int | None]          # = store.create(标签,…).seq；不可用 → None。
                                                     # 调用点：执行入口基线 1 次（决策 3）+ 每任务前 1 次（决策 2 权威快照）；测试以调用计数断言

class SpecPipeline:
    def __init__(self, ctx: "ReplContext", hooks: PipelineHooks | None = None): ...
        # hooks 缺省自 ctx 装配（loop/runner/ask 卡回调/ checkpoint_store）
    async def start(self, requirement: str) -> None   # /spec <需求>：澄清→…→终态
    async def resume(self, doc: SpecDoc) -> None      # /spec 无参续跑（仅 executing）
    async def cancel(self, doc: SpecDoc) -> None      # /spec cancel → archived
```

阶段函数私有拆分（各自可单测）：`_clarify / _draft / _review_loop / _approve / _execute / _code_review_loop / _acceptance`。顶层 `try/except (KeyboardInterrupt, Exception)` → 状态停驻 + `renderer.error` 恢复提示（决策 12）。

### 3.4 `tools/spec_tool.py`（read_spec，FR-56）

与 `ReadPlanTool` 同款机制：`name="read_spec"`；缺省读**最新活跃 Spec**（`store.active()`，无则最新任意状态），可传 `spec_id`；SAFE 风险、全模式。**只在主 registry 注册一次**（r1-S9：子 registry 由 `build_sub_registry` 自父派生，自然继承）——子 agent 评审时可回读（评审输入已含全文，此为其二通道）。返回 = frontmatter 摘要行 + 全文。

### 3.5 命令面（commands.py，FR-59）

| 命令 | 行为 |
|---|---|
| `/spec <需求>` | 启动全流程（`start`）；`<需求>` 为空串视为无参 |
| `/spec`（无参） | 存在 `executing` Spec → 进度卡 + ask「继续执行/取消归档」；存在其他非终态 → 状态卡 + 提示（决策 12）；全终态/无 → 用法提示 |
| `/spec status` | 最新活跃 Spec 的状态卡：frontmatter + 任务勾选矩阵 + （若 code_review 后）验收核验结果；无则提示 |
| `/spec cancel` | 最新活跃 Spec → 确认 ask → archived（附取消备注节）；无活跃 → 提示 |
| `/specs` | 全量列表卡：id / name / status / round / updated_at（倒序） |

补全：命令名候选经 commands.COMMAND_META 单一数据源（r1-S12；命令层需同步新增 COMMAND_META 与 _COMMAND_USAGE 条目，否则不进补全菜单与 /help，r2-S3）；ARG_COMPLETIONS 增 `/spec` 条目（新增 kind，候选 `status`/`cancel`）；SLASH_COMMANDS 为零引用遗留清单，不登记（r2-S3）。

## 四、关键流程细则

### 4.1 澄清访谈（FR-53）

1. `run_turn` 合成消息：「[Spec 流程·澄清] 需求原文：<需求>。请用 ask_user 逐点澄清关键决策点（目标边界/输入输出/约束/验收预期），用户表示清楚后，以一行『澄清完成：<目标一行>』作为终答。」
2. 终答返回后确定性门：ask 卡「需求是否已澄清、可进入起草？」→ `[进入起草 / 继续澄清 / 取消归档]`；继续澄清 → 再一轮 `run_turn`（携带上轮终答）；≤3 轮耗尽 → ask 卡 `[仍要起草 / 取消]`（升级，决策 9）。
3. 澄清记录素材 = 会话中 ask_user 问答（模型起草时自行整理进 `## 澄清记录`）。

### 4.2 起草（FR-54）

`run_turn`：注入 `SPEC_TEMPLATE` 与澄清终答，要求终答 = 完整 Spec 正文（不含 frontmatter）。pipeline 校验：缺任一必需节标题 → 一次自动补写轮（携带缺失清单，仅此一次；仍缺 → 以现有内容落盘并在风险节追加「模板节缺失」注记，不阻断）。`store.create` → `draft`。

### 4.3 Spec 评审循环（FR-55）

1. ask 卡选模式：`[全自动 / 深度介入（每轮可提建议）]` → frontmatter `mode`。
2. 每轮（round 1..3）：
   - 输入 = Spec 全文 + `SPEC_REVIEW_CHECKLIST` + `REVIEW_CONTRACT` + 用户反馈（深度介入：每轮报告卡后 ask 收一句建议，可空；全自动：空）；
   - `run_review` → 报告（超长按决策 6 回读全文）→ **评审报告卡**（阻塞/建议分节）；
   - 解析通过 → 出循环；不通过 → `run_turn` 修订（输入=阻塞清单全文）→ 终答=修订后全文 → `save_body` + `round+1`。
3. 3 轮耗尽 → ask 卡 `[呈请批准（带未决阻塞） / 再修订一轮（仅一次） / 取消归档]`（再修订仅允许一次，防死循环）。

### 4.4 批准与执行（FR-56）

1. **批准卡**：目标一行 + 任务数 + 验收标准数 + 未决阻塞提示（若有）；选项 `[批准执行 / 提修改意见 / 归档放弃]`。修改意见 → `run_turn` 修订 → 重新呈批（≤3 轮耗尽 → ask `[仍要批准 / 归档]`）。
2. 批准 → `approved`（approved_at）→ 入口基线 checkpoint（决策 3，非 Git → None + 提示）→ `executing` → 逐任务：① 抬头 `renderer.info(f"▶ 任务 i/N：{text}")`；② **`hooks.checkpoint(任务标签)` 权威任务级快照（决策 2，在任务动作前；返回 None 时继续）**；③ `run_turn`（消息 = 任务文本 + Spec 锚行「Spec 已就绪：.glaucous/specs/<id>.md（<name> · 未完成 M 项），read_spec 可回读全文」+ 约束提醒「仅完成本任务，不越界」）→ 成功 `check_task`；`run_turn` 抛异常 → ask `[重试 / 跳过该任务 / 归档中止]`（重试不重复打快照：仅首次进入该任务时打）。
3. 全部任务处理完毕（完成或跳过）→ `code_review`；跳过项写入 `## 风险与回退` 附注（store 层拼接）。

### 4.5 代码评审循环（FR-57）与验收（FR-58）

1. diff 基线 = `entry_checkpoint` seq：**经既有封装 `store.get(seq)` → `store.preview_changes(cp)`（r1-S11：不直调 `git_snapshots.diff_against`，避免依赖 `store._root` 私有字段）**，产出 M/D/A 三态清单；摘要渲染 ≤4000 字符（超限截断 + 「…共 N 项变更，已截断」，防评审输入爆炸）；基线为 null（非 Git）或 `store.get` 返回 None（基线被淘汰，决策 3）→ 输入注明降级原因。
2. 每轮（≤3）：评审子 agent（输入=验收标准 + diff 摘要 + `CODE_REVIEW_CHECKLIST` + `REVIEW_CONTRACT`）→ 发现卡 → 不通过：`run_turn` 修复（输入=阻塞发现）→ 复审。
3. 通过或耗尽升级（ask `[按现状出验收报告 / 再修复一轮（仅一次） / 取消归档]`）→ **验收核验**子 agent：输入=验收标准逐条 + 任务完成清单 + diff 摘要；契约=逐条 `✓ <标准>` / `✗ <标准>：原因`；
4. **验收卡**：逐条核验结果 + 总结论；全 ✓ → `verified`，存在 ✗ → `archived`（frontmatter 不动正文，验收报告追加至 `## 风险与回退` 尾，未决项明示）。

### 4.6 Spec 锚注入与「全文可回读」（FR-56）

执行期每条任务消息携带锚行（§4.4）；`read_spec` 常备（§3.4）。锚行同时写入任务轮终答后的历史（天然由任务消息承载），无需 history.view 变换（区别于方案锚——Spec 锚只在任务消息内联，不做视图替换，**决策差异说明**：方案锚的视图替换是为压缩多轮方案全文；Spec 全文本就不入父史，无压缩诉求，内联锚足够）。

## 五、错误处理策略

| 场景 | 行为 |
|---|---|
| pipeline 任意阶段抛异常 / Ctrl+C | 顶层捕获：状态停驻当前 frontmatter（已勾选/已轮次保留）→ `renderer.error` 打印停驻点与恢复方式（`/spec` 续跑或 `/spec cancel`）；不击穿 REPL |
| 报告契约解析失败 | 保守判不通过（决策 5），报告全文作阻塞证据进修订轮 |
| 评审报告超 1000 字 | 回读 outputs/ 归档（决策 6）；归档缺失 → 截断文本继续 |
| 子评审任务本身失败（runner 返回 ok=False） | 计为「评审异常」：ask `[重试本轮 / 跳过评审直接呈批 / 取消]`；不静默 |
| checkpoint store 不可用（非 Git） | 决策 8：执行不中断，抬头提示一次；代码评审无 diff |
| 入口基线快照被淘汰（超长任务清单） | 决策 3：`store.get` 返回 None → 代码评审降级为仅验收标准模式，报告卡注明 |
| ask 卡返回 None（EOF/中断） | 决策 13 统一映射：一律导向停驻或归档，绝不静默推进 |
| 任务轮异常 | ask 三选（§4.4）；跳过项登记风险节 |
| frontmatter 损坏 | load 容错（缺字段取默认，status 缺失 → 视为 draft）；`/specs` 跳过损坏文件并列告警 |
| 非法状态迁移（并发/手工改文件） | `SpecStateError` → 当前操作报错提示，不改文件 |
| 起草终答缺节 | §4.2 一次补写轮，仍缺 → 注记不阻断 |

## 六、与既有机制的边界核对（评审重点）

1. **父上下文隔离（FR-61/64 不变）**：评审/验收全部经 `ctx.subagent_runner` 直调（= M2 通道，不经 spawn_agent 工具 → 报告**不以工具结果入父史**，经卡片呈现、经子会话留痕）；澄清/起草/修订/任务执行/修复轮以合成消息入史；修订轮的评审全文经合成消息喂给（决策 6 回读的只读消费点）；评审轮本身父史零增长。
2. **切换保护**：pipeline 在命令处理器内运行，REPL 不会并发接收其他命令；会话切换/`/clear` 无并发窗口。`ctx.turn_active` 不被 pipeline 置位（它保护的是任务轮，而 pipeline 内每次 `run_turn` 自成轮次）——`run_turn` 前后由 repl 轮末机制负责吗？**不**：pipeline 内直接调 `ctx.loop.run` 不经 repl 轮壳，故 `turn_active`/`turn_checkpoint_seq` 的置复由 loop.run 内既有接线（on_checkpoint 写 seq）自然完成，`turn_active` 保持 False 无碍（无并发命令窗口）。
3. **审批**：任务执行轮内写操作照常走权限管线；审批卡「拒绝并回退」消费 `turn_checkpoint_seq` = 该任务入口（M4 接线），语义恰为「回退到该任务前」，与概设 §7.4 一致。
4. **深度介入模式仅评审轮生效**（FR-55 字面），批准/执行/验收维持全自动口径（决策 11）。
5. **/context、/sessions 等命令**在 pipeline 运行期间不可输入（同一 REPL 串行），无交互冲突。

## 七、测试与验证方式（任务 5.7，全 mock）

`tests/test_spec_store.py`（store 层，无 LLM）：
1. create/load/save_body/transition 合法与非法迁移（SpecStateError）；
2. check_task 勾选写回（第 N 个 checkbox 精确定位、重复勾选幂等）；
3. tasks()/acceptance() 解析（含 `- [x]` 混合、验收行提取）；
4. list_all 倒序 + 损坏文件跳过告警；active() 取最新非终态；
5. frontmatter 损坏容错（缺 status → draft）。

`tests/test_spec_pipeline.py`（PipelineHooks 全 fake：脚本化 run_turn/run_review/ask/checkpoint）：
6. 全流程回放：澄清→起草→评审（1 轮通过）→批准→执行（2 任务勾选 + **checkpoint hook 调用恰 3 次：入口基线 1 + 每任务 1，以调用计数与标签断言**，r1-B1 定案）→代码评审通过→验收全 ✓→verified（断言每步 frontmatter 与卡片要点）；
7. 评审修订回环：两轮不通过后第三轮通过（round 递增、save_body 内容更新）；
8. 轮次耗尽升级（四类各一例，r1-S6）：① Spec 评审 3 轮耗尽 → ask 选项呈现且「再修订仅一次」；② 澄清 3 轮耗尽 → `[仍要起草/取消]`；③ 批准反馈修订 3 轮耗尽 → `[仍要批准/归档]`；④ 代码评审 3 轮耗尽 → `[按现状出验收报告/再修复一轮/取消]`；
9. 深度介入：每轮报告后 ask 收建议并注入下轮评审输入（断言输入含建议文本）；
10. 执行任务失败 → 重试路径（不重复打快照）与跳过路径（跳过项进风险节）；
11. 报告截断回读：fake run_review 返回截断报告 + outputs 归档存在 → 修订轮输入含全文；
12. 契约解析失败 → 保守判不通过；
13. 非 Git 降级：checkpoint hook 返回 None → 执行继续、代码评审输入含降级说明；
14. /spec cancel → archived + 备注；/spec status 卡内容断言（经 FakeRenderer）；
15. ask 返回 None 语义抽查（决策 13）：澄清门 None → 取消归档；批准卡 None 且无意见 → 归档。

命令层接线测试（`handle_command` 分发到 pipeline 的替身，断言 /spec、/specs、/spec status、/spec cancel 路由与无活跃 Spec 提示）。

**验收映射（场景 G）**：澄清（6/8②）→起草（6）→评审（6/7/8①/9）→批准（6/8③）→执行（6/10）→代码评审（6/8④）→验收归档（6）；轮次耗尽升级（8 四类）；每任务 checkpoint（6，hook 调用计数 = 1 + 任务数）。

## 八、风险与裁剪声明

- **无裁剪**：任务 5.1~5.7 全量落实；概设 §7.4 `todo_write` 联动按决策 4 收窄（非裁剪——进度语义完整保留）。
- 已知边界（登记）：① 入口基线快照在超长任务清单下可能被淘汰 → 降级路径已定义（决策 3）；② 轮次上限硬编码 3，未接 `[review].max_rounds` 配置（决策 9，后续增强）；③ 起草/修订依赖模型遵循「终答=全文」契约，保守兜底链已定义（§4.2/决策 5）。
- 风险：真实模型对「终答=完整 Spec 正文」契约的遵循度——缓解：起草指令含格式强约束 + 缺节补写轮（§4.2）；评审契约同有保守兜底（决策 5）。
- 风险：执行期任务轮消耗主上下文（每任务消息 + 终答入父史）——长清单任务多时占用增长；缓解：既有 /compact 轮间压缩照常生效，`/context` 可调档；不在本里程碑加额外压缩策略。

## 九、实现对齐注记（代码评审 r1 后回写，2026-08-31）

1. **验收裁决保守口径（r1-B3 作者裁决）**：决策 11 落实为「全部标准有 ✓ 且无 ✗ 且 ✓ 行数 ≥ 标准数」才 `verified`；契约违约（无 ✓ 行）与子任务失败与评审环节决策 5 同口径，判 `archived` 附未决；验收结论落 frontmatter `acceptance` 字段（/spec status 卡呈现）。
2. **评审子任务失败重试语义（r1-S1 作者裁决）**：Spec 评审侧「重试本轮」与代码评审侧对齐——重试结果参与判定（通过直进批准链，不通过提示后仍呈批，避免无限拉锯）。
3. **升级「再修复一轮」后的复审（§4.5「复审」字面）**：代码评审耗尽升级选「再修复一轮（仅一次）」后，修复完成追加一次复审轮（结果记入卡片，验收环节照常逐条核验）。
4. **深度介入每轮收建议（r1-S2 对齐 §4.3 字面）**：含第 3 轮未通过时也收一句建议，随升级环节的修订注入。
5. **cancel 顶层捕获（r1-B2）**：`cancel()` 与 start/resume 同款异常兜底，非法迁移/IO 失败报错提示不击穿 REPL；`/spec` 无参续跑询问收敛为公开方法 `ask_continue`（r1-S3）。
6. **健壮性（编码期新增）**：起草/修订/补写终答自动剥离首尾 markdown 代码围栏（真实模型常包裹 ``` 围栏，不剥则节校验误判）；空文件/非 Spec 文档在 `list_all` 中判损跳过。
7. **受管任务轮壳（验收反馈 R1/R3，2026-08-31）**：pipeline 直调 `ctx.loop.run` 会绕过 repl 轮壳（思考区计数跨轮累积不收缩、正文逐字直打不走 md 卡片）——新增 `cli.run_managed_turn` 复刻 repl 同款时序（begin_turn → thinking start_turn/start → loop.run → 轮末收缩摘要行 + 终答 🕊 md 卡片 + 用量行），缺省 `run_turn` 挂钩改经它；全部任务完成后追加一次**总结轮**（主 agent 要点式汇报做了什么，经同款壳层以 md 卡片呈现，失败不致命）。
8. **全自动免批准（验收反馈 R2，2026-08-31）**：评审循环**正常轮次通过**（非升级/失败兜底路径，`_passed_clean`）且模式为全自动 → 跳过批准卡直接进入执行（提示一行）；深度介入模式或升级/兜底路径仍呈批准卡（用户介入点保留）。
9. **验收行列表标记容错（实测修正，2026-08-31）**：核验子 agent 实测输出「- ✓ 标准…」带列表标记，解析先剥首层 `-`/`*` 再判 ✓/✗（此前全部误判「存在未决」）。
10. **思考区间隙段治理（验收反馈 R5，2026-08-31）**：`close()` 后 `was_active` 残留且 `_lines/_text_buf` 不清 → 间隙段（ask 卡 pause/resume、子评审事件）重绘旧块：旧计数跨段累积（「思考中 · 23 步」）+ 上一轮正文尾泄漏（起草说明残影）。修复：`close()` 收缩后复位全部内部状态；`resume()` 未激活不重绘；子评审区间段经 `cli.thinking_enter/thinking_exit` 自有干净生命周期（独立计数 + 轮末收缩摘要）；轮末终答判据改为 close 前先取 `was_active`。
11. **起草/修订终答净化（实测修正，2026-08-31）**：模型违反「不得含解释性前后缀」契约，终答附「起草说明：以上为完整 Spec 正文…请指出」交互性元话语（混入 Spec 文档与评审输入）——`_clean_body` 裁掉首个必需节前的开场白 + 剔除尾部含 ≥2 个元话语标记的段落；`DRAFT_INSTRUCTION` 同步强化禁例。
