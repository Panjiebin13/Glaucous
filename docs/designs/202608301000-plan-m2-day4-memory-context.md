# Glaucous M2 Day4 记忆与上下文 - 技术设计方案

> 创建日期：2026-08-30
> 关联规格：[编程智能体需求文档.md](../../编程智能体需求文档.md)（FR-17/18/19/20/21/22/24/25/31）、[Glaucous开发计划表.md](../../Glaucous开发计划表.md)（Day 4/M2 任务 2.1~2.8）、[编程智能体概要设计说明书.md](../../编程智能体概要设计说明书.md)（§4.2 上下文管理、§7 记忆与扩展、§5.2 轻量锚、§10 工程结构）
> 关联前案：[Day 3 Plan](202608291000-plan-m1-day3-permission.md)（已实施，为本次改造基线）
> 状态：已批准（第 2 轮评审通过，阻塞 0；S-11~S-15 表述精度项已随手修订）

## 0. 本轮范围裁剪声明（用户明确约束）

沿用 Day3 约束，本轮**只进行代码开发**：

- **不产出新增测试、不执行新增验证**（任务 2.8 单测全部登记 §9 测试债务，M4 偿还）；
  **既有用例回归属基线保护，允许并要求执行**（`python -m pytest tests/ -q` 全绿）——本轮将改写 `history.view()`（所有 LLM 请求必经路径），回归是防止 Day3 基线被静默破坏的唯一验证手段，不属于「新增验证」
- **不做端到端验证**（M2 验收「场景 B 求助→记忆沉淀 / 场景 E 规则拦截」留待用户在 WSL 环境自行验证）
- 会话结束「自动提炼记忆候选」属概设 §12 裁剪顺序第 2 位（可裁剪项），**未排期**（FR-22 后半句「自动提炼须用户确认」因此本轮不触发，非遗漏）
- 编码策略统一为 Code-First（跳过新增 Test 产出的裁剪变体）
- `/memory`、`/rules`、`/compact` 斜杠命令归 M3（FR-21 的"可查看/删除"经 /memory 补全）；本轮记忆管理以存储与注入为主

## 1. 总体架构

Day 4 在 Day 3 基线上新建 **extensions 包**、**context/budget.py**、**context/compactor.py**、**safety/output_limit.py**（概设 §10），并扩展 tools 与 loop：

```
src/glaucous/
├── cli.py ※                    # rules/memory 读取装配、ask_user 回调、占用条渲染
├── agent/
│   └── loop.py ※               # 守卫点扩展：预算评估 → L1/L2 压缩 → 预算耗尽终止；
│                               # dispatch 后 budget 记账；tool 入史前 L0 截断
├── extensions/ ※（新包）
│   ├── __init__.py
│   ├── rules.py ※              # 2.1 glaucous.md 双层读取（全局 + 项目，全量不裁剪）
│   └── memory.py ※             # 2.2 事实记忆双作用域存储与 Top-N 注入
├── context/
│   ├── history.py ※            # 入史扩展键 _meta（L1 摘要数据源）；view() 视图变换：
│   │                            #   剥除内部键 + submit_plan arguments 锚替换（§4.7/D11）
│   ├── budget.py ※（新）       # 2.4 token 估算 + 三档占用档位（与压缩共用阈值常量）
│   └── compactor.py ※（新）    # 2.6/2.7 L1 历史裁剪 + L2 摘要压缩（保留方案锚）
├── safety/ ※（新包）
│   ├── __init__.py
│   └── output_limit.py ※       # 2.5 L0 工具输出截断（落盘 + 头尾保留 + 回取提示）
├── tools/
│   ├── interactive.py ※（新）  # 2.3 ask_user（回调注入，与 submit_plan 同范式）
│   ├── memory_tool.py ※（新）  # 2.2 memory_save（双作用域写入）
│   ├── output.py ※（新）       # 2.5 read_output（L0 落盘输出的分段回取）
│   └── planning.py ※           # 2.7 submit_plan 方案落盘 plans/<id>.md + 回喂含轻量锚
│                               # + read_plan 工具（锚的回取通道，两模式）
└── ui/
    └── prompts.py ※            # 注入段扩展：基础准则 → 工作区 → glaucous.md 规则 → 事实记忆
```

核心数据流（Day 3 链路上叠加，概设 §4.2）：

```
loop.run(task)
  → 守卫检查点（每次请求 LLM 前，概设 §4.1）：
      budget.estimate(history.view())
        ├─ 占用 ≤70%  → 直接请求
        ├─ 70%<占用≤85% → L1 裁剪（compactor，本地派生摘要）→ 请求
        ├─ 占用 >85%  → L1 → L2 压缩（调 LLM 生成摘要，保留方案锚）→ 请求
        └─ 压缩后仍超限 → 优雅终止（终止条件③，diagnostic 事件交付）
  → dispatch(call)：ToolResult 入史前过 L0 截断（output_limit）
      ├─ ≤300 行且 ≤50KB → 原样入史（metadata 记账已在 base.py）
      └─ 超限 → 完整输出落盘 .glaucous/outputs/<call_id>.log，
              content 替换为「头 200 行 + 省略标记 + 尾 50 行 + read_output 回取提示」
  → 方案全文不经历史常驻（概设 §5.2）：submit_plan 的 plan 全文仅存在于
      dispatch 参数与回喂瞬间，发给 API 的视图（view()）中 arguments.plan
      被确定性替换为锚文本（§4.7/D11）
  → 每轮 run() 结束 emit "budget" 事件 → CLI 渲染占用条（FR-25）
```

## 2. 分层影响分析

| 层级 | 受影响模块 | 变更说明 |
|------|-----------|---------|
| CLI 交互层 | cli.py | rules/memory 装配传入 build_system_prompt；ask_user 决策回调；budget 事件渲染占用条（纯文本，M3 升级 rich） |
| Agent 编排层 | loop.py | 守卫点叠加压缩管线；L0 截断挂接入史路径；budget 事件 |
| 上下文管理（新） | context/budget.py、compactor.py、history.py（扩展键） | 记账、L1/L2、_meta 数据源 |
| 扩展子系统（新） | extensions/rules.py、memory.py | 规则双层读取、记忆双作用域 |
| 安全 | safety/output_limit.py（新） | L0 截断与落盘 |
| 工具系统 | tools/interactive.py、memory_tool.py、output.py（新）、planning.py（落盘+read_plan） | 四个新工具 |
| 配置 | config.py | 新增 GLAUCOUS_CONTEXT_LIMIT（默认 128000）、GLAUCOUS_MEMORY_TOP_N（默认 50） |
| 不涉及 | llm/、permission/、ui/theme.py（M3） | 无变更（L2 压缩复用 LLMClient.chat） |

## 3. 数据模型

```python
# context/budget.py（2.4）
@dataclass
class BudgetReport:
    used: int            # 估算 token 数（system + 全部历史）
    limit: int           # 上下文上限（GLAUCOUS_CONTEXT_LIMIT，默认 128000）
    percent: float       # used / limit
    level: str           # "low" | "warn"（>70%）| "critical"（>85%）

def estimate_tokens(text: str) -> int
    # ASCII 字符 /4 + CJK 字符 /1.5（概设 §4.2：字符数/4，中文按/1.5）
def estimate_messages(messages: list[dict]) -> int   # 逐条求和
def level_of(percent: float) -> str                  # 阈值常量单一出口（概设 §4.2「共用同一套阈值常量」）

# context/compactor.py（2.6/2.7）
L1_TRIM_RATIO = 0.70     # 定义于 budget.py，compactor 导入复用（S-12：阈值单一出口）
L2_COMPACT_RATIO = 0.85  # 同上；budget 与 compactor 均从 budget.py 导入，禁止字面量副本
L1_KEEP_RECENT_ROUNDS = 2   # 最近 K 轮原文保留（K=2）

def trim_history(messages: list[dict]) -> int
    # L1：从旧到新，对最近 KEEP_RECENT_ROUNDS 轮之外的 role=tool 消息，
    # 将 content 替换为 _meta 派生的一行摘要；已裁剪（_trimmed）跳过（幂等）；
    # assistant 文本一律保留。返回裁剪条数。只改内存，不重写 JSONL（§5 决策 D1）

async def compact_history(messages, llm, plans_dir: Path) -> bool
    # L2：取「早期历史」（除最近 KEEP_RECENT_ROUNDS 轮外的全部消息）序列化为文本，
    # 经 llm.chat 请求生成任务摘要；方案锚段由 compactor 内部自 plans_dir 构造（S-05/S-11）；
    # 成功→早期消息替换为一条合成消息：
    #   {"role": "user", "content": "【会话阶段摘要·系统压缩生成】\n<摘要>\n" + 方案锚段}
# 轮的定义（S-13，L1/L2/配对断言共用）：一个完整轮 = 一条 assistant(tool_calls) 消息
#   及其之后全部配对的 role=tool 消息（按 tool_call_id 配对）；无 tool_calls 的
#   assistant 消息（终答）单独成轮。轮边界由 history 顺序结构唯一确定。
    # 失败（LLM 异常/空摘要）→ 降级为仅 L1 加深裁剪，返回 False 不阻断主流程

# safety/output_limit.py（2.5）
L0_MAX_LINES = 300
L0_MAX_BYTES = 50 * 1024
KEEP_HEAD, KEEP_TAIL = 200, 50

def truncate_output(content: str, call_id: str, outputs_dir: Path) -> tuple[str, bool]
    # 未超限 → (原样, False)；超限 → 落盘 outputs/<call_id>.log（call_id 经 sanitize），
    # 返回（头 200 行 + 省略标记 + 尾 50 行 + 回取提示, True）

# extensions/memory.py（2.2）
@dataclass
class MemoryEntry:
    content: str
    category: str        # 自由分类，默认 "general"
    created_at: str      # ISO 时间戳
    last_used: str       # 注入命中时刷新（加权依据）

class MemoryStore:
    def __init__(self, global_path: Path, project_path: Path)
    def add(self, content, scope: str, category: str) -> None      # scope: "global"|"project"；
        # 去重作用域 = 单一存储文件内（S-14）：global 与 project 各自独立去重，跨作用域不去重
    def load_injection(self, top_n: int) -> str
        # 双作用域合并 → 按 (last_used, created_at) 降序取 Top-N（存储不裁剪，注入裁剪）
        # 格式化为「- [项目/全局][category] content」列表文本；无记忆返回 ""

# extensions/rules.py（2.1）
def load_rules(workspace: Path) -> str
    # 全局 ~/.glaucous/glaucous.md + 项目 <workspace>/glaucous.md 依序读取，
    # 全量拼接不裁剪；两文件均缺失返回 ""；
    # 任一文件超 4000 字符时在该段尾部附「规则过长，建议精简（规则被裁剪等于没规则）」提示

# 历史扩展键（context/history.py）
push_tool(call, result, metadata: dict | None = None)
    # entry 额外写入 "_meta": metadata（ToolResult.metadata，base.py 已记账）与
    # "_trimmed": False（L1 幂等标记）；submit_plan 的 tool 结果额外写 "_anchor": True
    # view() 视图变换（输出前）：
    # 1) 剥除全部 "_" 前缀内部键——对含内部键的 entry 返回浅拷贝再过滤，
    #    无内部键的 entry 沿用原引用（不原地删改，保住 D2 单一数据源）
    # 2) submit_plan arguments 锚替换（D11，§4.7）：对 role=assistant 且
    #    tool_calls[].function.name=="submit_plan" 的 arguments，JSON 解析后将
    #    plan 字段替换为锚文本常量（解析失败则整体替换为锚文本，不抛错）
    #    ——确定性与幂等由「源数据不变、变换纯函数」保证，resume 重放同样生效
    #    （工具入参在 dispatch 时仍为全文：confirm 卡片展示完整方案不受影响）
```

## 4. 接口设计（模块间契约）

### 4.1 glaucous.md 双层注入（extensions/rules.py，任务 2.1，FR-20）

- **读取顺序**：全局 `~/.glaucous/glaucous.md` → 项目 `<workspace>/glaucous.md`，段首分别标注「【全局规则】」「【项目规则】」。
- **全量注入永不裁剪**（概设 §7.2「规则被裁剪等于没规则」）；单文件超 4000 字符时**附提示而不截断**。
- **缺失容错**：文件不存在/读取失败 → 该段省略，不报错（首用用户无 glaucous.md 是常态）。
- **不生成**：项目 glaucous.md 不存在时不创建草稿（/init 归 M3 任务 3.6）。

### 4.2 事实记忆（extensions/memory.py + tools/memory_tool.py，任务 2.2，FR-21/22）

- **双作用域存储**：全局 `~/.glaucous/memory.json`、项目 `<workspace>/.glaucous/memory.json`，JSON 数组格式（可读可手工编辑）；写入原子化（临时文件 + replace），损坏时容错为空表重建（宁丢不崩，与审计「尽力而为」一致）。
- **memory_save 工具**：参数 `content`（必填）、`scope`（enum: "global"/"project"，必填）、`category`（可选，默认 "general"）。两模式可用，risk=SAFE（写入的是系统内部存储 `.glaucous/` 与 `~/.glaucous/`，类比 audit.log 的系统写入，不属沙箱审批面；重复内容去重：content 完全一致时刷新 last_used 而非新增）。
- **注入裁剪（Top-N）**：存储全量保留（M3 /memory 可查看/删除全量），注入时按 (last_used, created_at) 降序取前 `GLAUCOUS_MEMORY_TOP_N`（默认 50）条——「按 category 与最近使用加权裁剪」的简化实现：category 以标注形式保留，最近使用为主权重。
- **last_used 刷新**：注入即视为使用（每次 system prompt 组装时刷新被选中条目），无需语义匹配。

### 4.3 ask_user 工具（tools/interactive.py，任务 2.3，FR-17/18/19）

- **契约**：`ask_user(question, options?)`——question 必填；options 为字符串数组（0–6 个）。两模式可用，risk=SAFE。
- **交互范式**：与 submit_plan 的 confirm 回调同构——工具持 `AskCallback`，CLI 注入实现：打印「🕊 请教」提问卡 + 候选列表（[1]..[n]），读入序号或自由文本；EOF/Ctrl+C 返回「用户未响应」控制信号（非交互环境不挂死）。
- **options 校验归属**：base.py 轻量校验子集不支持 items/maxItems，"0–6 个字符串" 约束由工具 execute 内自校验——非字符串元素过滤、超 6 个取前 6 个，options 为空列表视同未提供。
- **回喂**：`用户回答：<原文>`；选了候选序号时附注所选选项。用户回答即上下文事实，FR-19 的沉淀由 system prompt 引导模型主动调 memory_save（不强制）。
- **求助节奏引导（FR-18）**：BASE_PROMPT 增补——环境类失败（依赖缺失/命令不存在/凭证不可得）先自行重试 **2 次**，仍无果再调 ask_user；提问须具体可答、附候选；获得环境事实后应调 memory_save 沉淀到对应作用域。

### 4.4 Token 记账与占用条（context/budget.py，任务 2.4，FR-25/31）

- **估算函数**：`estimate_tokens(text) = ascii/4 + cjk/1.5`（概设 §4.2）；按消息粒度对 `view()` 全序列求和。支持后续接入精确 tokenizer（函数单一出口）。
- **三档阈值**：与压缩策略共用常量（概设 §4.2「用户看到的与系统执行的一致」）——`low`（≤70%）、`warn`（70–85%）、`critical`（>85%）。
- **估算范围偏差（已接受）**：仅对 `view()` 序列求和，tools 声明与本轮输出预留不计入——占用系统性略偏低、压缩触发略晚；阈值可经 GLAUCOUS_CONTEXT_LIMIT 调低补偿，精确化留 M4（概设未强制要求）。
- **占用条事件**：loop 每轮 `run()` 结束（含终止路径）emit `"budget"` 事件（payload: used/limit/percent/level）。
- **CLI 渲染**（纯文本，M3 升级 rich）：单行状态条，如 `  ctx 34% [██████░░░░░░] 43k/128k`；warn 档附「建议压缩对话」、critical 档附「上下文即将压缩」。块字符在 cp936 下由既有 errors=replace 兜底。

### 4.5 L0 输出截断（safety/output_limit.py + tools/output.py，任务 2.5，FR-24）

- **挂接点**：loop 在 `dispatch` 返回后、`push_tool` 之前对 `result.content` 执行 `truncate_output`——只影响入史正文，metadata 记账的 lines 字段按原始行数（工具行 UI 摘要不受影响）。
- **触发阈值**：>300 行或 >50KB（概设 §4.2 L0）。
- **截断形态**：头 200 行 + `…（中间 N 行已截断，完整输出已落盘）…` + 尾 50 行 + 一行回取提示（「完整输出已保存，可调用 read_output(call_id, offset, limit) 分段查看」）。
- **落盘**：`.glaucous/outputs/<call_id>.log`；**call_id 净化**：仅保留 `[A-Za-z0-9_-]`，其余替换为 `_`（防路径注入）；写入失败降级为仅截断不回取提示（尽力而为）。
- **事件一致性**：loop 在 push_tool 前替换 result.content，故 tool_end 事件携带的即截断后内容——UI 展示与入史一致，无需二次处理。
- **read_output 工具**：参数 `call_id`（必填）、`offset`（起始行，默认 0）、`limit`（默认 200，上限 1000）。按行分段读取落盘文件；文件不存在回喂明确错误。两模式可用，risk=SAFE（只读，路径由系统派生不经模型，无沙箱面）。

### 4.6 L1 历史裁剪（context/compactor.py，任务 2.6，FR-24）

- **触发**：预算占用 >70%（守卫点内，L2 之前先行执行）。
- **动作**：从旧到新淘汰「最近 KEEP_RECENT_ROUNDS=2 轮」之外的已完成轮次 tool 结果正文，替换为 `_meta` 本地派生的一行摘要：`⎿ edit_file src/auth/session.py · 成功 · 120ms · 24 行`（概设 §4.2 格式）；assistant 文本保留（submit_plan 的 arguments 锚替换已在 view() 视图层完成，见 §4.7/D11，L1 无需处理 arguments）。**摘要不调用模型**——零 token、确定性、无幻觉。
- **方案锚行保留**：`_anchor: True` 标记的 tool 消息（submit_plan 决策回喂，含路径+目标一行的锚行）不被裁剪为元数据摘要，而以锚行原文充当摘要——保证 Build 执行期间「方案在哪、目标是什么」始终在上下文中可达（概设 §4.2「压缩时显式保留方案轻量锚」的 L1 层落实）。
- **数据源**：push_tool 时入史的 `_meta`（base.py 已在 dispatch 成功/失败路径统一记账五字段，Day3 预埋）。`_meta` 缺失的条目（如熔断善后的 push_raw_tool）派生为 `⎿ <name> · <成功/失败>`。
- **幂等**：已裁剪条目标记 `_trimmed: true` 跳过；重复触发不二次改写。
- **只改内存**：JSONL 会话文件保留全量原文（§5 决策 D1）。

### 4.7 L2 摘要压缩与方案轻量锚（context/compactor.py + tools/planning.py，任务 2.7，FR-24、概设 §5.2）

- **触发**：L1 后占用仍 >85%。
- **方案全文不常驻（B-01 修复，概设 §5.2 核心语义）**：submit_plan 的 plan 全文仅存在于 dispatch 参数与回喂瞬间；发给 API 的视图经 view() 确定性变换（§3/D11）——`tool_calls[].function.name=="submit_plan"` 的 arguments 中 plan 字段被替换为锚文本常量（`【方案锚】全文已存档 .glaucous/plans/，可调用 read_plan 回读`）。落盘 plans/<id>.md 与 confirm 卡片展示用原文（工具入参在 dispatch 时仍为全文，用户看到完整方案）；历史内存与 JSONL 保留原文（与 D1 全量落盘一致，非模型上下文）；选③继续讨论时模型可经 read_plan 回读旧版再修订（概设「细节需要时再回读」）。变换为纯函数、幂等，resume 重放同样生效——任何路径下发給 API 的上下文中方案全文都不常驻。
- **动作**：早期历史（除最近 2 轮外）序列化为带角色标注的文本，经 `llm.chat`（不携带 tools）请求生成 ≤500 字任务摘要；成功后早期消息**整体替换**为一条合成消息 `{"role": "user", "content": "【会话阶段摘要·系统压缩生成】…"}`——user 角色对全部网关合法，显式标注防模型误认为真实用户输入。
- **方案轻量锚（概设 §5.2/§7.4）**：
  - submit_plan 执行时将方案全文落盘 `.glaucous/plans/<id>.md`（id=时间戳+随机后缀），决策回喂文案尾部追加锚行：「方案已就绪：.glaucous/plans/<id>.md（目标：<提取规则见下>；未完成任务 N 项），可用 read_plan 回读全文」——对齐概设 §5.2 锚定义「目标一行 + 未完成任务清单」（S-15：方案落盘时全部任务尚未开始，「任务 N 项」即未完成任务清单；Build 中勾选进度后锚不刷新——锚是回读指针，最新进度由历史与 todo 反映）；
    目标提取规则：自方案全文取首个以「目标」开头的行（容忍 `## 目标`/`**目标` 等标记前缀），去除标记后截取 ≤80 字符；无匹配行则省略目标段；该 tool 结果入史时标记 `_anchor: True`（L1 保留，§4.6）；
  - **read_plan 工具**：参数 `plan_id`（可省略=最新方案）。两模式可用，risk=SAFE；路径系统派生，无沙箱面；
  - **锚段构造归属（S-05）**：compactor 内部自 plans 目录构造锚段（解析最新方案文件头部的目标与任务清单，解析失败仅附路径），loop 只注入 plans 目录路径；resume 后「最新方案」按文件名（含时间戳）字典序取最大；
  - L2 摘要消息**显式拼接方案锚段**——「压缩后任务目标与当前方案不丢失」（FR-24），Build 执行不跑偏。
- **失败降级与终止（S-01）**：LLM 压缩调用异常/返回空 → 降级为 L1 加深裁剪（KEEP_RECENT_ROUNDS 临时减 1，下限 1）后本轮继续；**L2 连续失败达 2 次**且预算仍 >85% → 不再重试压缩，直接走 §4.8 预算耗尽终止（终止条件③）——防止「每轮重走 L1（幂等跳过）→ L2 失败 → 再评估」的压缩调用循环。压缩成功或占用降回阈值以下时失败计数清零。

### 4.8 预算耗尽终止（loop.py，概设 §4.1 终止条件③）

- L2 后（或 L2 连续失败达上限后，§4.7 S-01）`budget.percent` 仍 ≥100% → `_terminate("上下文已达上限，压缩后仍超限。已完成部分保留在会话文件中，可 /exit 后 --resume 继续。")`——复用既有 diagnostic 事件通道，REPL 不中断。
- 检查点位于「每次请求 LLM 之前」的既有守卫位置，与步数上限检查并列。

### 4.9 CLI 装配（cli.py）

- repl 内装配顺序：`load_rules(workspace)` + `MemoryStore(...)` → `build_system_prompt(workspace, rules_text, memory_text)` → History/State → registry 注册新工具（`memory_save`、`ask_user`、`read_output`、`read_plan`）。
- **AgentLoop 依赖注入扩展（S-03）**：构造函数新增 `context_limit: int`（预算评估）、`outputs_dir: Path`（L0 落盘）与 `plans_dir: Path`（L2 锚段数据源）三个参数（均带默认值，既有测试/调用兼容）；compactor/output_limit 以纯函数模块直接导入调用，不做实例注入——保持 loop 构造面最小。
- `build_system_prompt(workspace, rules: str = "", memory: str = "")` 签名扩展，默认参数保持既有测试/调用兼容；注入段顺序：基础准则 → 工作区 → 规则 → 记忆（概设 §4.2），空段省略。
- **resume 路径**：system prompt 重建同样走新签名（rules/memory 每次启动现读现注入，FR-20「每次会话自动生效」）。
- budget 事件渲染函数 `render_budget(report) -> str` 独立可测。

## 5. 关键设计决策

| 决策点 | 选项 A | 选项 B | 选择 | 理由 |
|--------|--------|--------|------|------|
| D1 裁剪/压缩是否写回 JSONL | 重写会话文件为裁剪后序列 | 只改内存，JSONL 保留全量 | B | 会话文件是完整现场（FR-05），裁剪只服务「发给 API 的视图」；resume 时重新按预算裁剪，零信息损失 |
| D2 _meta 存放位置 | 历史旁路 dict（call_id→meta） | 入史扩展键，view() 剥除 | B | 单一数据源随消息生命周期天然一致；旁路 dict 需自理清理与恢复；`_` 前缀键 view() 剥除后对 API 不可见 |
| D3 L2 摘要消息角色 | assistant | user（合成+显式标注） | B | assistant 合成消息会让模型误认是自己说过的话；user+标注语义清晰且全网关兼容 |
| D4 L2 压缩失败处理 | 阻断终止 | 降级 L1 加深，继续 | B | 压缩是优化不是门禁；L1 确定性可用兜底；下轮守卫点重新评估 |
| D5 记忆裁剪位置 | 存储时裁剪 | 注入时 Top-N，存储全量 | B | 存储全量保证 /memory（M3）可管理全部条目；注入裁剪无损可逆 |
| D6 记忆/规则注入时机 | 每轮动态重读 | 启动时读一次 | B | system 消息内存对象全程引用不可变（Day2 Plan 既有约束）；文件变更经重启/--resume 生效，行为可预期 |
| D7 L0 挂接点 | base.py dispatch 内 | loop 层 push_tool 前 | B | 截断属上下文管理职责（概设归 context/safety 层），非工具执行职责；且 base.py 的熔断计数语义不应感知截断 |
| D8 read_output/read_plan 沙箱 | 走 Workspace 校验 | 系统派生路径，不经模型传路径 | B | 路径由系统自 .glaucous/ 派生，模型只传 id/offset，无沙箱面；call_id/plan_id 净化后拼路径，逃逸不可能 |
| D9 budget 估算粒度 | 每消息缓存增量累计 | 每次全量重算 | B（初始）/缓存留 M4 | 历史经 L1/L2 会被改写，增量缓存失效逻辑复杂；全量重算 O(n) 在万级 token 内开销可忽略，先求正确 |
| D10 占用条渲染层 | loop 直接 print | 事件通道 + CLI 渲染 | B | 沿用 Day2 事件契约（loop 不感知 UI）；M3 rich 状态栏只改 CLI 侧 |
| D11 方案全文去常驻时点 | dispatch 后改写入史 entry | view() 视图层确定性替换 | B（view 层） | 入史后改写需同步 JSONL 重写（追加式做不到）；view() 纯函数变换幂等、覆盖 resume、源数据不变保 D1 全量；代价是每次 view() 对 submit_plan 条目做一次 JSON 解析（该条目罕见，开销可忽略） |
| D12 L2 失败上限 | 无限重试（下轮重评估） | 连续失败 2 次后走终止③ | B | 无上限会形成压缩调用循环（S-01）；2 次给足瞬态故障恢复空间，失败属持续性问题（如模型不支持长输入）时应优雅终止而非空转 |

## 6. 编码策略决策

按 §0 裁剪声明，本轮全部步骤为 **Code-First（跳过 Test 产出的裁剪变体）**：

| 步骤 | 任务描述 | 策略 | 决策依据 |
|------|---------|------|---------|
| Step 1 | extensions 包：rules.py 双层读取 + memory.py 存储/注入 + prompts 注入段 + CLI 装配 + config.py 新增 GLAUCOUS_MEMORY_TOP_N | Code-First | 文件读取与格式化，逻辑直白（TOP_N 为本步 load_injection 直接消费项） |
| Step 2 | memory_save + ask_user 工具（interactive.py 回调范式）+ 求助节奏 prompt 引导 | Code-First | 与 submit_plan 同构的胶水层 |
| Step 3 | budget.py 记账/档位 + loop 守卫点接线 + budget 事件 + CLI 占用条 + config.py 新增 GLAUCOUS_CONTEXT_LIMIT | Code-First | 纯函数 + 事件接线（CONTEXT_LIMIT 为本步直接消费项） |
| Step 4 | output_limit.py L0 + loop 入史前截断 + read_output 工具 | Code-First | 截断与落盘，含 call_id 净化 |
| Step 5 | history.py _meta 扩展 + compactor.py L1/L2 + planning.py 方案落盘 + read_plan | Code-First | 本轮最复杂步骤（历史改写 + LLM 压缩） |
| Step 6 | 整体编译/导入验证 + 基线保护回归（既有用例全绿）+ TODO.md 测试债务登记 | Code-First | 回归检查（配置项已在 Step 1/3 归位，此处仅汇总校验） |

## 7. 实施步骤

- [ ] Step 1：extensions 包（rules.py / memory.py）+ prompts.build_system_prompt 注入段 + cli.py 装配 + config.py 新增 GLAUCOUS_MEMORY_TOP_N（任务 2.1/2.2 注入侧）
- [ ] Step 2：tools/memory_tool.py + tools/interactive.py（ask_user + AskCallback CLI 注入）+ BASE_PROMPT 求助节奏与记忆沉淀引导（任务 2.2/2.3）
- [ ] Step 3：context/budget.py（估算/档位）+ loop 守卫点预算评估 + "budget" 事件 + cli.py 占用条渲染 + config.py 新增 GLAUCOUS_CONTEXT_LIMIT（任务 2.4）
- [ ] Step 4：safety/output_limit.py（L0 截断/落盘/call_id 净化）+ loop push_tool 前挂接 + tools/output.py（read_output）（任务 2.5）
- [ ] Step 5：history.py `_meta`/`_trimmed` 扩展键 + context/compactor.py（L1 派生摘要 / L2 模型压缩 + 方案锚）+ planning.py 方案落盘与锚回喂 + tools/planning.py 内 read_plan + loop 守卫点压缩管线与预算耗尽终止（任务 2.6/2.7）
- [ ] Step 6：全模块导入编译验证 + **基线保护回归**（`python -m pytest tests/ -q` 既有用例全绿，§0 声明允许并要求）+ TODO.md 登记测试债务（收尾；配置项已在 Step 1/3 各自归位，此处仅汇总校验）

## 8. 风险与注意事项

| 风险 | 缓解 |
|------|------|
| L2 压缩调用真实 LLM 失败/超时 | 降级 L1 加深裁剪继续（D4）；连续失败 2 次后走终止③（D12），不形成压缩调用循环 |
| _meta/内部键泄漏到 API 请求 | view() 统一剥除 `_` 前缀键（含内部键 entry 走浅拷贝，不原地删改）；锚替换同为视图变换；基线保护回归覆盖 view() 序列合法性 |
| JSONL 含 _meta 的向后兼容 | load() 逐行还原 dict 原样保留未知键，旧会话文件无 _meta 时 L1 派生降级格式（§4.6） |
| token 估算偏差导致压缩过早/过晚 | 估算仅用于触发阈值非精确计费；阈值可经环境变量调整；精确 tokenizer 接入点已预留（estimate_tokens 单一出口） |
| L1/L2 改写破坏 tool_call 配对 | L1 仅替换 tool 消息 content 不增删消息；L2 整段替换保持「assistant(tool_calls) 与其全部 tool 消息」同进同出（早期段以完整轮为单位），替换后序列配对合法性由 compactor 内轮次完整性断言保护 |
| resume 后预算状态丢失 | used 不持久化，每次启动按加载的全量历史重估（D9）；恢复后再触发裁剪属预期行为 |
| call_id/plan_id 路径注入 | 字符白名单净化 + 系统派生目录拼接（§4.5/§4.7 D8） |
| cp936 终端下占用条块字符乱码 | 既有 stdout errors=replace 兜底；字符降级可读不崩溃（FR-34） |
| memory.json 损坏/手工编辑出错 | 读取容错为空表重建；写入临时文件+原子 replace |
| 方案全文常驻上下文（概设 §5.2 冲突） | view() 视图层锚替换（D11/B-01）：arguments.plan 确定性替换为锚文本，dispatch 入参与 JSONL 保留原文供 confirm 展示与全量落盘；read_plan 供按需回读 |
| 压缩管线与 mode 快照交互 | 压缩只改历史不碰 state；守卫点内先压缩后请求，与既有 mode 快照逻辑无耦合 |
| 锚替换误伤未来同名工具/字段 | 替换仅针对 `function.name=="submit_plan"` 且含 `plan` 字段的 arguments，白名单常量集中于 history.py 单处定义 |
| 新增 4 工具挤占 system/tools 声明预算 | description 精炼（合计 <400 token）；声明层过滤逻辑不变 |

## 9. 测试策略（本轮不产出，登记债务）

**本轮不产出新增测试、不执行新增验证（既有用例基线保护回归除外，§0 已声明）**。任务 2.8 全部登记为测试债务（M4 任务 4.1/4.2 偿还，概设 §11 对应文件标注）：

| 债务项 | 应覆盖 | 对应概设测试文件 |
|--------|--------|-----------------|
| 规则注入 | 双层读取顺序与标注、全量不裁剪、缺失容错、超长提示、空段省略、resume 重建注入 | test_rules_injection.py |
| 记忆双作用域 | global/project 写入隔离、去重刷 last_used、Top-N 注入排序、损坏文件容错、原子写 | test_memory_scope.py |
| token 记账与档位 | 中英文估算公式、三档阈值边界（70%/85%）、budget 事件 payload | test_budget_compaction.py |
| L1 裁剪 | 派生摘要格式、最近 K 轮保留、幂等（_trimmed）、assistant 保留、_meta 缺失降级、配对合法性 | test_budget_compaction.py |
| L2 压缩 | mock LLM 摘要替换、方案锚拼接、失败降级 L1、压缩后配对合法、压缩后仍超限触发终止③ | test_budget_compaction.py |
| L0 截断 | 行/字节双阈值、头 200+尾 50+省略标记、落盘、call_id 净化、写入失败降级、metadata.lines 记原始值 | test_output_truncation.py |
| read_output 回取 | 分段 offset/limit、越界、文件不存在回喂 | test_output_truncation.py |
| ask_user | 回喂结构（含候选附注）、EOF 控制信号、options 边界（0/6 个） | test_interactive_tools.py |
| 方案落盘与 read_plan | plans/<id>.md 落盘、锚行回喂（含目标一行）、缺省取最新、plan_id 净化、view() 锚替换幂等且 resume 重放生效 | test_plan_anchor.py |
| 预算耗尽终止 | L2 后仍超限走 _terminate 且 diagnostic 事件必达 | test_loop_termination.py |

M2 验收（场景 B 求助→记忆沉淀 / 场景 E 规则拦截 / 长会话占用条与压缩按阈值工作）按用户约束**不在本轮执行**，留待 WSL 环境由用户自行验证。
