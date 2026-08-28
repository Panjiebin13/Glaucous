# Glaucous M0 Day2 原型闭环补全 - 技术设计方案

> 创建日期：2026-08-28
> 关联规格：[编程智能体需求文档.md](../../编程智能体需求文档.md)（FR-01/02/03/05/06/08）、[Glaucous开发计划表.md](../../Glaucous开发计划表.md)（Day 2 任务 0.9~0.15）、[编程智能体概要设计说明书.md](../../编程智能体概要设计说明书.md)（§4.2 持久化、§5.1~5.3 双模式与切换、§5.6 工具清单）
> 关联前案：[Day 1 技术设计方案](202608270900-plan-m0-day1-prototype.md)（已实施，代码为本次改造基线）
> 状态：已批准（经 2 轮 Plan Review，末轮通过）

## 0. 本轮范围裁剪声明（用户明确约束）

按用户指示，本轮**只进行代码开发**：

- 不做环境配置、不安装依赖
- **不运行任何测试与端到端验证**（含任务 0.15 的真实项目修 bug 演练——仅保留验收口径说明，执行留待用户环境就绪后自行进行）
- 编码策略统一为 Code-First（跳过 Test 产出的裁剪变体），测试债务登记 §9，M4 偿还

任务 0.15 按计划表备注「可留到 M1 结束后合并验证」的既有弹性处理，不构成本轮范围遗漏。

## 1. 总体架构

Day 2 在 Day 1 基线上扩展（标 ※ 为本轮新增/修改）：

```
src/glaucous/
├── cli.py ※               # --resume、三选一交互、y/n 审批回调、模式提示符
├── config.py              # （不变）
├── agent/
│   ├── loop.py ※          # mode 快照、dispatch 传 mode、Build 自然终止回归 Plan、mode_changed 统一出口
│   └── state.py ※         # SessionState：mode + approval_policy
│                          #   结构归属说明：概设 §10 将 plan/build 状态归 permission/modes.py、
│                          #   submit_plan 处理归 agent/planner.py；Day 2 简化归属（state 放 agent/、
│                          #   submit_plan 以 Tool 形态放 tools/planning.py 以便注册进 registry）。
│                          #   M1 权限成型时 state 迁往 permission/modes.py 并吸收切换协议；
│                          #   M2 若引入方案落盘/轻量锚，planner.py 承接方案文档职责
├── context/
│   └── history.py ※       # JSONL 追加落盘 + load 恢复（任务 0.14）
├── tools/
│   ├── base.py ※          # Tool.modes 属性、tool_schemas(mode)、dispatch(call, mode) 执行层校验
│   ├── shell.py ※         # BashTool：超时/UTF-8/kill（任务 0.9）
│   ├── files.py ※         # 新增 WriteFileTool / EditFileTool（任务 0.10/0.13）
│   └── planning.py ※      # SubmitPlanTool：方案提交 + 三选一（任务 0.12）
└── ui/
    └── prompts.py ※       # system prompt 双模式行为引导（Plan 产出方案→submit_plan；Build 按方案执行）
```

Day 2 核心数据流（在 Day 1 循环上叠加模式与审批）：

```
run(task)
  → 循环 { 守卫 → mode 快照 → LLM chat(view, tool_schemas(mode))
      → 无 tool_calls → 终答入史；若 mode==build → 回归 plan（概设 §5.1 模式回归）
      → push_assistant → 逐个 dispatch(call, mode 快照)
          → 执行层模式校验（plan 下写工具幻觉调用 → 回喂"当前为 Plan 模式"）
          → write/edit 工具内部：生成 diff → approve 回调（CLI：per-action 弹 y/n / auto-approve 放行）
          → submit_plan 工具内部：confirm 回调（CLI 打印方案 + 三选一）→ 决策回喂 + state 切换 }
```

## 2. 分层影响分析

| 层级 | 受影响模块 | 变更说明 |
|------|-----------|---------|
| CLI 交互层 | cli.py | --resume 参数、三选一交互、写审批回调、模式化提示符、Banner 更新 |
| Agent 编排层 | loop.py、state.py※ | mode 快照语义、Build 自然终止自动回归 Plan、mode_changed 事件 |
| 上下文管理 | history.py | JSONL 追加写 + 会话恢复加载 |
| 工具系统 | base.py、shell.py※、files.py、planning.py※ | Tool.modes 声明、registry 双层模式过滤、bash/write/edit/submit_plan 四个新工具 |
| UI | prompts.py | 双模式行为引导注入 |
| 不涉及 | llm/、permission/（M1）、extensions/（M2）、safety/（M2） | 客户端无改动；分类器/沙箱/审批三选项按计划表属 M1 |

## 3. 数据模型

```python
SessionState:  mode("plan"|"build"), approval_policy("per-action"|"auto-approve")   # 简版状态机
PlanDecision:  choice(1|2|3), feedback(str|None)                                    # 三选一结果
Tool.modes:    frozenset({"plan","build"}) 默认；write/edit={"build"}；submit_plan={"plan"}
```

JSONL 会话文件格式（`.glaucous/sessions/<id>.jsonl`）：

```
首行：{"type":"session_meta","session_id":...,"created_at":...,"workspace":...}
后续：每行一条 OpenAI 消息 dict（role 含 user/assistant/tool；system 不落盘，恢复时重建）
```

## 4. 接口设计（模块间契约）

### 4.1 工具系统（base.py 扩展）

| 接口 | 变更 | 说明 |
|------|------|------|
| `Tool.modes` | 新增类属性 | 默认 `frozenset({"plan","build"})`；写工具仅 `{"build"}`；submit_plan 仅 `{"plan"}` |
| `tool_schemas(mode=None)` | 增加过滤参数 | 按模式生成声明层工具定义（None=全量，供测试/调试） |
| `dispatch(call, mode)` | 增加执行层校验，**mode 必填**（无默认值，漏传即接口错误而非静默放行） | 工具不在当前 mode 声明中 → ok=False 回喂（非解析类错误，清零熔断计数）：写工具回喂"当前为 Plan 模式，请先调用 submit_plan 产出方案并经用户确认"；submit_plan 在 build 下回喂"方案已确认，无需再次提交" |

### 4.2 bash 工具（任务 0.9）

```python
BashTool(command: str, timeout: int = 120) -> ToolResult
```

- `asyncio.create_subprocess_shell(command, cwd=workspace, stdout/stderr=PIPE)`（Linux 一等公民；Windows 走 cmd，符合 FR-34「基本兼容不崩」）
- **超时**：`asyncio.wait_for(communicate(), timeout)`；超时 → `proc.kill()` → 再次 `communicate()` 收尸 → ok=False 回喂"命令超时（{t}s）已被终止"+ 已产生的部分输出
- **kill**：超时 kill 之外，捕获 `asyncio.CancelledError`（用户 Ctrl+C 中断本轮）时同样 kill 进程后 re-raise——不留僵尸进程
- **UTF-8**：输出按 `utf-8` 解码，`errors="replace"`（二进制输出不崩）
- **输出防爆**：stdout+stderr 合并，超过 300 行时保留尾部 300 行 + 头部标注"（输出已截断，仅保留尾部 300 行）"（L0 正式策略是 M2 任务 2.5）
- 返回 content 格式：`exit_code={n}\n{输出}`；非零退出码仍算 ok=True（命令执行成功，退出码是业务信息，模型据此判断测试失败原因）
- **写工具 content 格式约定**：write_file 成功 → `已写入 {path}（{n} 行）`；edit_file 成功 → `已修改 {path}：替换 {n} 处`（replace_all 时）/ `已修改 {path}`（单处）；拒绝 → ok=False + 拒绝文案
- **edit 目标非 UTF-8 文件**：读取阶段 `UnicodeDecodeError` → ok=False 回喂"无法以 UTF-8 解码（可能是二进制文件）"，与 read_file 惯例一致（files.py 既有处理）
- **Build per-action 下 bash 写命令不经审批**：已知偏差——y/n 审批仅覆盖 write_file/edit_file；bash 的命令级审批依赖 M1 危险命令分类器（任务 1.2），与「Plan 模式 bash 可写」同属 M1 收口范围
- **先全部放行**：无分类器、无白名单（M1 任务 1.2/1.5）；两模式均注册均放行
- timeout 参数上限 600s（schema minimum=1，实现层钳制），防模型传超大值挂死会话
- **background 参数（FR-03）无排期悬空**：概设 §5.6 bash 含 `background?`，但计划表 0.9 及 M1~M5 均无排期——沿用 Day 1 对 glob 的悬空登记惯例，供用户后续决策

### 4.3 write_file / edit_file（任务 0.10/0.13）

```python
WriteFileTool(path: str, content: str) -> ToolResult
EditFileTool(path: str, old: str, new: str, replace_all: bool = False) -> ToolResult
```

- **write_file**：父目录不存在自动创建（`mkdir(parents=True)`，对齐业界工具行为，减少模型往返）；写入前生成「全文 vs 新文件」diff；approve 回调拒绝 → ok=False"用户已拒绝该写操作"，文件不落盘
- **edit_file 唯一匹配校验**（概设 §5.6）：`old` 在文件中出现次数 ==1 才执行；==0 回喂"未找到匹配文本，请先 read_file 确认内容"；>1 且未传 replace_all 回喂"匹配 {n} 处，请提供更长上下文使匹配唯一或传 replace_all=true"——强迫先读后改，歧义回喂而非静默选择
- **replace_all=true**：全部替换并回喂替换处数
- **diff 生成**：`difflib.unified_diff(旧行, 新行, fromfile, tofile)`，写操作执行前经 approve 回调展示
- **approve 回调**：`Callable[[action, path, diff], bool]`，由 CLI 注入实现（per-action 弹 y/n / auto-approve 直接放行）；工具层不感知 UI（保持 tools 层无终端依赖，与 Day 1 分层原则一致）
- edit 的 diff 基于"旧全文 vs 替换后全文"
- 文件为 UTF-8；edit 目标不存在 → 回喂"文件不存在，请用 write_file 新建"

### 4.4 submit_plan（任务 0.12）

```python
SubmitPlanTool(plan: str) -> ToolResult   # plan 为方案全文（模板引导：目标/步骤/风险）
```

- 仅 Plan 模式声明（`modes={"plan"}`）；方案模板由 system prompt 引导（概设 §7.4：目标/澄清/设计/步骤/风险，简单任务轻量产出）
- 工具执行时调用 **confirm 回调**：`Callable[[str], PlanDecision]`，CLI 实现为「打印方案全文 + 三选一」：
  - `① 开始构建，每次请求权限` → `state.mode=build, state.approval_policy=per-action`
  - `② 开始构建，同意所有权限` → `state.mode=build, state.approval_policy=auto-approve`
  - `③ 继续讨论一下` → 留在 Plan，可附反馈文字
- 决策回喂（ToolResult content）：`用户选择②：开始构建（同意所有权限），已切换 Build 模式` / `用户选择③：继续讨论。用户反馈：{feedback}`——结构化回喂让模型理解决策而非原样重试
- 回调内部改 state（闭包持有 SessionState）；工具层不感知 state 模块
- **方案落盘**：简版不做 `.glaucous/plans/<id>.md`（概设 §5.2 轻量锚/read_plan 属 M2 上下文管理），方案全文经对话历史天然保留——Plan 中登记为 M2 前的已知简化

### 4.5 主循环与状态（loop.py / state.py）

```python
SessionState(mode="plan", approval_policy="per-action")
AgentLoop(llm, registry, history, state, max_steps, on_event)
```

- **mode 快照语义**：每次 LLM 请求前取 `mode_snapshot = state.mode`；本轮声明层（tool_schemas）与执行层（dispatch 校验）都用快照——submit_plan 在轮中切换 mode 后，同轮后续幻觉的写调用仍按 Plan 快照拦截回喂，下一轮起 Build 声明生效。避免「声明层与执行层同轮不一致」的微妙窗口
- **模式回归**（概设 §5.1 任务完成自动回归）：Build 模式下自然终止（无 tool_calls 终答）→ `state.mode=plan`、approval_policy 重置 → emit `mode_changed` 事件（payload: mode/policy/reason），CLI 更新提示符。**异常终止（步数上限/熔断/Ctrl+C）不回归**：异常终止 ≠ 任务完成，留在 Build 便于用户驱动未竟构建；连带风险已登记 §8（auto-approve 跨轮残留）。**回归时模型侧信号**：mode_changed 事件只到 CLI，模型侧通过执行层拦截文案 + 声明层缺写工具自纠即可（回归后模型试探写调用会被回喂"当前为 Plan 模式"），无需额外注入历史消息
- 事件契约扩展：新增 `mode_changed`；Day 1 的 text/tool_start/tool_end/diagnostic 不变。**触发方式**：loop 在每轮 LLM 响应处理结束后（含无 tool_calls 的自然终止分支，视作空 dispatch 循环）比对 state.mode 与本轮快照，不一致即 emit mode_changed——覆盖 submit_plan 三选一①②切换与自然终止回归两条路径，统一出口避免提示符与实际模式相反
- **dispatch 的 mode 为必填参数**（无默认值）：loop 唯一调用方必须显式传快照——若给默认值 None，漏传时执行层校验将静默失效，双层权限的兜底层被无声绕过

### 4.6 会话持久化（任务 0.14）

```python
History(system_prompt, session_file: Path | None = None)
History.load(session_file, system_prompt) -> History   # classmethod
```

- 每个 `push_*` 后立即追加写一行 JSON（`ensure_ascii=False` + UTF-8）；崩溃/中断不丢已发生消息（FR-05 中断不丢现场）
- `load`：读首行 session_meta 校验格式；逐行 json.loads 还原 `_messages`；损坏行（非法 JSON）停止读取并保留已还原部分——宁可少恢复也不崩
- **尾部配对校验与修复（B1 修复，关键设计）**：还原后校验 assistant.tool_calls 与 tool 消息的配对完整性——进程在 dispatch 执行期间被硬杀时，JSONL 尾部会留下完整 assistant(tool_calls) 行而无配对 tool 行（Day 2 引入 bash 后该窗口可达数百秒），恢复出的序列必被 API 400 拒绝、会话永久报废。修复策略：自尾部向前扫描，为未配对的 tool_call_id 补推 ok=False 的 ToolMessage（content：`会话中断，该调用结果未落盘`），与 loop 内存态的 `_salvage_dangling_calls` 善后语义对齐（Day 1 基线已确立该约束）。合法性论证：loop 逐个顺序 push_tool 且异常路径均有内存善后，崩溃只可能在尾部留下悬空、已落盘 tool 消息必为前缀——故补推行追加至尾部即满足配对约束，扫描至首个完整配对的 assistant 即可终止；修复行同时写回 JSONL（防止二次恢复重复修复）；修复时打印告警
- `--resume`（CLI）：`--resume` 不带参数恢复最新会话（按文件名排序取末位）；`--resume <id>` 指定会话
- **resume 后 SessionState 语义**：恢复会话时 state 重置为 `plan/per-action`——依据概设 §5.2「策略不跨会话持久化」，重置到 plan 是安全侧且闭环成立（历史含方案全文，模型可再次 submit_plan 走确认）
- 恢复时 system prompt 按当前 workspace 重建；meta.workspace 与当前不一致时打印警告继续（不阻断）
- 新会话：`.glaucous/sessions/<yyyyMMdd-HHmmss>-<rand4>.jsonl`，目录不存在自动创建

### 4.7 CLI（cli.py）

- argparse：`--workspace DIR`、`--resume [SESSION_ID]`（nargs="?"，const="latest"）
- 提示符随模式：`🌊 plan >` / `🌊 build >`
- 三选一交互：方案全文打印 + `[1/2/3]` 输入（③可追加反馈文字，回车空反馈允许）；非法输入重问
- 写审批回调：per-action → 打印 diff + `[y/n]`（n 拒绝）；auto-approve → 直接放行（打印一行"auto-approve 放行"保持可见性）；**Day 2 无分类器，auto-approve 不拦任何命令**（危险命令拦截是 M1 任务 1.2/1.4，属既定排期）
- 恢复会话时打印最近 N 条消息摘要（`🌅 已恢复上次会话`）

## 5. 关键设计决策

| 决策点 | 选项 A | 选项 B | 选择 | 理由 |
|--------|--------|--------|------|------|
| 模式过滤实现 | registry 按 mode 过滤 schemas + dispatch 校验 | 两个独立 registry 实例按模式重建 | A | 概设 §5.1 双层权限（声明层隐藏 + 执行层兜底）语义直接；重建实例丢失熔断计数等运行态 |
| mode 快照时序 | 请求前快照，整轮一致 | dispatch 实时读 state | A | 避免 submit_plan 同轮切换后，同轮幻觉写调用在声明层缺失下被执行层放行的窗口 |
| 交互注入方式 | 工具构造注入回调（confirm/approve） | 工具内部直接 input() | A | tools 层不感知终端（概设 §2.1 分层：tools 不含 UI）；回调由 CLI 组装，保持可替换 |
| bash 退出码语义 | 非零退出码 ok=False | ok=True，退出码写入 content | B | 退出码是业务信息（测试失败=模型需要看输出定位），ok 表示"执行过程无异常"；工具执行异常（超时等）才 ok=False |
| write 父目录 | 不存在报错回喂 | 自动创建 | B | 对齐业界行为减少往返；目录创建本身无害（Day 2 无沙箱） |
| 方案文档落盘 | submit_plan 时写 .glaucous/plans/<id>.md | 方案保留在对话历史 | B | 计划表 0.12 只要求"简版交互"；落盘+轻量锚+read_plan 是 M2 上下文管理任务，避免超前设计 |
| resume 粒度 | 全量消息回放 | 最近 N 条 | A | History 本就全量内存态；回放全量保证模型上下文完整（裁剪是 M2 的 budget 职责） |
| bash 输出防护 | 无限制 | 尾部 300 行截断 | B | 上下文防爆最小措施；L0 正式落盘回取是 M2 任务 2.5 |
| system prompt 双模式注入 | 静态同时描述两模式 | 随 mode 动态重写 | A | 动态重写破坏消息不可变性（system 在 JSONL 不落盘但内存 view() 每次引用）；静态注入让模型知道完整规则（何时产出方案、何时执行），行为由声明层+执行层硬约束保证而非提示词自觉 |
| dispatch mode 参数 | 默认 None（宽松） | 必填（严格） | B | 漏传即静默绕过执行层校验，双层权限的兜底层失效——契约层面消除该风险（§4.5） |

## 6. 编码策略决策

按 §0 裁剪声明，本轮全部步骤为 **Code-First（跳过 Test 产出的裁剪变体）**：

| 步骤 | 任务描述 | 策略 | 决策依据 |
|------|---------|------|---------|
| Step 1 | 0.11 双模式基座：Tool.modes + SessionState + registry 双层过滤 + loop 快照与回归 | Code-First | 模式矩阵本应 TDD（状态机类逻辑），按用户约束跳过，登记债务 |
| Step 2 | 0.9 BashTool：超时/kill/UTF-8/输出截断 | Code-First | 子进程编排逻辑；债务登记 |
| Step 3 | 0.10 WriteFileTool/EditFileTool：唯一匹配/diff/replace_all | Code-First | 唯一匹配校验本应 TDD（边界密集），按用户约束跳过，登记债务 |
| Step 4 | 0.12 SubmitPlanTool + prompts 双模式引导 | Code-First | 契约简单；三选一状态流转债务登记 |
| Step 5 | 0.13 CLI 审批接线：diff 打印 + y/n + auto-approve | Code-First | 交互胶水层 |
| Step 6 | 0.14 History JSONL 持久化 + --resume | Code-First | 序列化逻辑；债务登记 |

## 7. 实施步骤

- [ ] Step 1：双模式基座（tools/base.py modes 化、agent/state.py 新建、agent/loop.py 快照与回归）（策略：Code-First）
- [ ] Step 2：BashTool（tools/shell.py 新建，cli.py 注册）（策略：Code-First）
- [ ] Step 3：WriteFileTool / EditFileTool（tools/files.py 扩展，含 approve 回调接口）（策略：Code-First）
- [ ] Step 4：SubmitPlanTool（tools/planning.py 新建）+ prompts.py 双模式引导（策略：Code-First）
- [ ] Step 5：CLI 接线（三选一交互、写审批回调、模式提示符、mode_changed 渲染）（策略：Code-First）
- [ ] Step 6：JSONL 持久化 + --resume（history.py 扩展、cli.py 参数）（策略：Code-First）
- [ ] 任务 0.15：**本轮不执行**（用户约束）；环境就绪后的用户验收口径：真实小项目上走「需求 → Plan 探索 → submit_plan → 三选一① → edit y/n 确认 → 修改落盘 → 汇报 → 回归 Plan」全流程

## 8. 风险与注意事项

| 风险 | 缓解 |
|------|------|
| Windows create_subprocess_shell 走 cmd，shell 语义与 POSIX 有差异 | FR-34 明确「基本兼容不崩」；Linux 一等公民，演示在 WSL2；不追求 Day 2 命令级等价 |
| Day 2 无分类器/沙箱：bash 任意命令 + auto-approve 无拦截 | 计划表 0.9 明确"先全部放行"；M1 任务 1.1/1.2/1.4 收口；期间仅本人可信环境使用（Day 1 Plan §8 风险延续） |
| Plan 模式下 bash 也能写（M1 前） | 已知偏差：M1 任务 1.5（Plan bash 白名单）收口；Day 2 语义以"写文件工具不可见"为界 |
| 用户在 y/n、三选一交互中 Ctrl+C | 交互函数捕获 KeyboardInterrupt → 视为拒绝/取消并返回对应决策，会话不崩（REPL 顶层兜底延续） |
| JSONL 写入与消息入史非原子（进程崩溃在两步之间） | 追加写即时 flush；半行（非法 JSON）在 load 时丢弃；**悬空 tool_calls（完整行但无配对 tool 行）由 load 尾部配对校验补推修复**（见上表硬崩溃行与 §4.6）——恢复的会话始终满足 OpenAI 协议序列合法 |
| resume 大会话一次性加载内存 | Day 2 会话规模可控；上下文裁剪是 M2 budget/compactor 职责 |
| 同轮 submit_plan + 幻觉写调用的声明/执行不一致窗口 | mode 快照语义消除（§4.5）：同轮仍按 Plan 拦截，下一轮 Build 生效 |
| build + auto-approve 异常终止（步数上限/熔断/Ctrl+C）后跨轮残留：用户后续普通输入可能在无确认下放行写操作 | 回归仅自然终止触发（异常终止留在 Build 的语义见 §4.5）；提示符常驻显示 build·auto 提醒；用户可重启会话恢复 plan 默认态；M1 审批三选项收口该风险面 |
| 硬崩溃落在 push_assistant 与 push_tool 落盘之间（bash 长任务窗口可达数百秒） | load 尾部配对校验 + 补推 ok=False ToolMessage 修复（§4.6 B1 设计），修复行写回 JSONL |
| REPL 对 CancelledError 的捕获差异：asyncio.run 下 SIGINT 产生 CancelledError 而非 KeyboardInterrupt，cli.py 的 `except KeyboardInterrupt` 捕不到 BashTool re-raise 的 CancelledError，REPL 会直接退出 | REPL 循环内显式 `except (KeyboardInterrupt, asyncio.CancelledError)`：中断本轮（loop 已善后悬空 call）继续会话 |

## 9. 测试策略

**本轮不产出、不执行任何测试**（用户约束）。测试债务清单（M4 任务 4.1/4.2 偿还，概设 §11 对应文件标注）：

| 债务项 | 应覆盖 | 对应概设测试文件 |
|--------|--------|-----------------|
| 模式暴露矩阵 | plan×{read/list/grep/bash/submit_plan 可见，write/edit 不可见}；build×{全部可见，submit_plan 不可见} | test_mode_tool_exposure.py |
| 执行层模式校验 | plan 下幻觉 write_file 回喂引导文案；build 下 submit_plan 回喂已确认；计数清零 | test_mode_tool_exposure.py |
| 模式回归 | build 自然终止后 state 回 plan + 事件；步数上限终止不回归（异常终止语义） | test_plan_build_switch.py |
| 三选一切换 | ①→per-action、②→auto-approve、③→留 plan 带反馈；决策回喂文案；mode 快照同轮一致性 | test_plan_build_switch.py |
| bash 工具 | 超时 kill、非零退出码 ok=True、输出 300 行截断、UTF-8 解码、timeout 钳制 | — |
| edit 唯一匹配 | 0 处/1 处/多处/replace_all 全替换；未 read 先 edit 的引导文案 | test_edit_uniqueness.py |
| 写审批 | per-action 拒绝→文件不变+回喂；auto-approve 放行；diff 生成正确 | test_approval_flow.py（M1 前的雏形覆盖） |
| JSONL 持久化 | 追加写格式、load 全量还原、损坏行截断、悬空 tool_calls 尾部修复（含二次恢复不重复修复）、resume latest、跨 workspace 警告 | — |
| resume 状态语义 | 恢复后 state=plan/per-action（策略不跨会话持久化） | — |
| bash CancelledError | Ctrl+C 中断 → 子进程 kill + CancelledError re-raise → REPL 捕获继续会话 | — |
| write_file | 新建/覆盖/父目录自动创建/拒绝不落盘 | — |

Day 2 验收（真实项目修 bug 全流程，任务 0.15）按用户约束**不在本轮执行**；验收口径见 §7。
