# Glaucous M0 Day1 原型闭环 - 技术设计方案

> 创建日期：2026-08-27
> 关联规格：[编程智能体需求文档.md](../../编程智能体需求文档.md)、[Glaucous开发计划表.md](../../Glaucous开发计划表.md)（Day 1 任务 0.3~0.8）、[编程智能体概要设计说明书.md](../../编程智能体概要设计说明书.md)（§3/§4/§5.6/§10）
> 状态：已批准（经 4 轮 Plan Review，末轮通过）

## 0. 本轮范围裁剪声明（用户明确约束）

按用户指示，本轮 **只进行代码开发**：

- **不做环境配置**（跳过任务 0.1 WSL2 环境检查、0.2 GitHub 仓库创建；不执行 `pip install -e .`）
- **不进行测试与验证**（不运行 pytest、不做端到端验收「对 agent 说看看项目结构」）

因此本轮编码步骤统一采用 **Code-First（仅 Code 产出，跳过 Test 产出）** 的裁剪变体；测试欠账显式登记到 §9 测试债务清单，由计划表 M4（任务 4.1/4.2）统一偿还——这与开发计划表本身「测试集中在 M4 补齐」的安排一致。

## 1. 总体架构

本轮实现概设 §10 工程结构的 Day 1 子集（标注 ※ 的为本轮新增）：

```
glaucous/
├── pyproject.toml ※            # 打包 + pytest 配置 + openai 依赖
├── .gitignore ※                # models.toml/config.toml/.glaucous/ 不入库（概设 §9）
├── TODO.md ※                   # 计划表「每日节奏」要求的进度勾选文件
├── src/glaucous/
│   ├── __init__.py ※
│   ├── config.py ※             # 环境变量 → LLMProfile/Config（models.toml 注册表在 M3.4）
│   ├── cli.py ※                # 简版 CLI：input 循环 + print 输出（无主题）
│   ├── __main__.py ※           # python -m glaucous 入口
│   ├── agent/
│   │   ├── __init__.py ※
│   │   └── loop.py ※           # 主循环 v0：自然终止 + 步数上限两类终止条件
│   ├── context/
│   │   ├── __init__.py ※
│   │   └── history.py ※        # 消息模型 + view() 转 OpenAI API 格式（JSONL 落盘在 Day2 0.14）
│   ├── llm/
│   │   ├── __init__.py ※
│   │   └── client.py ※         # OpenAI 兼容请求 + 重试退避 + SSE 流式 + tool-call 拼装
│   ├── tools/
│   │   ├── __init__.py ※
│   │   ├── base.py ※           # Tool 协议 + ToolResult + ToolRegistry + schema 校验
│   │   ├── files.py ※          # read_file / list_dir
│   │   └── search.py ※         # grep
│   └── ui/
│       ├── __init__.py ※
│       └── prompts.py ※        # system prompt 组装（记忆/skill 注入点留待 M2/M3）
└── tests/                       # 空占位，M4 补齐
```

数据流（概设 §2.3 的 Day 1 简化版）：

```
CLI input → AgentLoop.run(task)
  → History.push_user
  → 循环：LLMClient.chat(history.view(), registry.tool_schemas(), on_text=流式打印)
      → 无 tool_calls → History.push_assistant(终答) → 返回文本（自然终止）
      → 有 tool_calls → History.push_assistant(msg)   # 先入史：tool 消息必须紧跟含 tool_call_id 的 assistant 消息
          → 逐个 Registry.dispatch(call)
              → JSON 解析 → schema 校验 → Tool.execute → ToolResult(ok, content, metadata)
              → 错误（工具不存在/JSON 非法/校验失败/执行异常）→ ok=False 的 ToolResult 回喂自纠（解析失败连续第 3 次熔断，见 §4.1）
              → History.push_tool(call, result)       # 记录 call_id 配对，view() 生成 role=tool 消息
```

## 2. 分层影响分析

| 层级 | 受影响模块 | 变更说明 |
|------|-----------|---------|
| CLI 交互层 | cli.py、prompts.py | 新建：简版 REPL、基础 system prompt |
| Agent 编排层 | agent/loop.py | 新建：主循环 v0（守卫仅步数上限） |
| 上下文管理 | context/history.py | 新建：消息模型与 API 格式转换（budget/compactor 留待 M2） |
| 工具系统 | tools/base.py、files.py、search.py | 新建：Tool 协议、registry、三个只读工具（暂无沙箱/分类器/审批） |
| LLM 网关 | llm/client.py | 新建：重试退避 + 流式读取 + tool-call delta 拼装 |
| 本地执行环境 | files.py、search.py | pathlib + 显式 UTF-8，路径以 workspace 为基准解析 |
| 不涉及 | permission/、extensions/、safety/、ui/theme.py | 分别属于 M1（权限成型，Day 2 仅雏形）、M2（记忆与上下文）、M2 任务 2.5（L0 截断）、M3.1（主题）任务 |

## 3. 数据模型

无数据库。核心内存对象：

```python
ToolCall:        id, name, arguments(str 原始 JSON)          # LLM 输出
AssistantMessage: text | None, tool_calls: list[ToolCall]     # LLM 输出
ToolResult:      ok, content, metadata{tool,args_brief,ok,duration_ms,lines}  # 工具输出
ToolMessage:     call_id, name, content, ok                   # 入史形态：由 push_tool(call, result) 生成
```

metadata 按 §4.2「执行时记账」设计：dispatch 时顺手记录结构化元数据，为 M2 的 L1 裁剪派生摘要预埋零成本数据源。

消息序列约束（OpenAI 协议硬性要求）：`role=tool` 消息必须紧跟包含对应 `tool_call_id` 的 `assistant(tool_calls)` 消息之后——因此 `push_assistant` 在 dispatch 循环**之前**执行，`push_tool(call, result)` 携带 call_id 与 name。

## 4. 接口设计（模块间契约）

### 4.1 LLM 网关

| 接口 | 签名 | 说明 |
|------|------|------|
| 构造 | `LLMClient(profile: LLMProfile)` | 内部持有 AsyncOpenAI（仅作 HTTP 通道） |
| 对话 | `async chat(messages, tools, on_text=None) -> AssistantMessage` | 流式；on_text(text) 回调逐段转发正文（CLI 实时打印） |

**LLMProfile 字段**（config.py 从环境变量加载）：

| 字段 | 环境变量 | 缺失行为 |
|------|---------|---------|
| base_url | `GLAUCOUS_BASE_URL`（默认 `https://api.deepseek.com/v1`） | 用默认值 |
| api_key | `GLAUCOUS_API_KEY` | 启动即报错退出（附提示：请设置 GLAUCOUS_API_KEY） |
| model | `GLAUCOUS_MODEL`（默认 `deepseek-chat`） | 用默认值 |
| temperature | `GLAUCOUS_TEMPERATURE`（默认 0.2，可省略） | 用默认值 |

> 命名说明：Day 1 的 `GLAUCOUS_MODEL` 是 M3.4 models.toml 注册表落地前的过渡方案；M3.4 落地后由注册表接管模型路由，`GLAUCOUS_DEFAULT_MODEL`（概设 §9）作为注册表的默认档案选择项，与这里的单模型环境变量形成映射（M3.4 实施时统一迁移，避免双轨长期并存）。

重试退避：429 / 5xx / 连接错误 / 超时 → 指数退避 + 抖动，**最多重试 4 次**（概设 §4.4）；4xx（鉴权/参数错）不重试直接抛出。

流式 tool-call 拼装（概设 §4.3）：按 `delta.tool_calls[i].index` 累积，`function.name`/`function.arguments` 为增量片段需拼接；arguments 的 `json.loads` 与 schema 校验**延迟到 dispatch 层**，使错误统一走 ToolResult 回喂通道。**解析失败回喂修正上限 2 轮**（概设 §4.3）：注意标准协议下模型修正重发携带**新的** call_id，故熔断计数不能按 call_id——由 `ToolRegistry` 维护**全局连续解析失败轮数**：任一 dispatch 因 JSON 非法/schema 校验失败返回 ok=False 时 +1，任一次成功执行或非解析类错误（工具不存在/执行异常）时清零；连续第 3 次解析失败时 dispatch 抛 `ParseCircuitBroken`，loop 捕获后**先为该轮已入史但未完成 dispatch 的悬空 call_id 补推 ok=False 的 ToolMessage（错误信息注明解析熔断终止），保证 History 序列满足 OpenAI tool_call_id 配对约束**，再返回诊断文本终止本轮——这是步数上限 50 之外的第二道熔断。计数器在每次 `run()` 入口重置（熔断语义限定在单任务内，避免上一任务尾部残留导致下一任务首次失败即误熔断）。

### 4.2 工具系统

| 接口 | 签名 | 说明 |
|------|------|------|
| 协议 | `Tool.name/.description/.parameters` + `async execute(**kwargs) -> ToolResult` | parameters 为 JSON Schema dict |
| 注册 | `ToolRegistry.register(tool)` | — |
| 声明 | `tool_schemas() -> list[dict]` | OpenAI tools 格式 |
| 执行 | `async dispatch(call: ToolCall) -> ToolResult` | 解析→校验→执行→记账，四类错误全部 ok=False 回喂；解析失败连续 3 次抛 `ParseCircuitBroken`（全局连续计数：成功或非解析类错误即清零） |

自研轻量 schema 校验（支持 type/required/enum/properties/minimum 子集），不引入 jsonschema 依赖——保持「工具定义与本地执行自研」的约束边界。

### 4.3 工具清单（Day 1 三个只读工具）

| 工具 | 参数 | 行为 |
|------|------|------|
| `read_file` | path*, offset?, limit? | 带行号输出（`  12: content`）；默认上限 2000 行，截断时尾部标注 |
| `list_dir` | path?（默认 "."） | 目录以 `/` 结尾排在文件前，按名排序 |
| `grep` | pattern*, path? | Python re 逐行匹配，输出 `path:line:content`；上限 200 条命中；跳过 .git、二进制（解码失败）、>5MB 文件 |

路径解析：相对路径一律相对 `workspace`（构造时注入各工具），绝对路径原样——为 M1 沙箱（realpath + 前缀校验）预留统一入口。

**glob 不在 Day 1 范围**：概设 §5.6 将 glob 与 read_file/list_dir/grep 并列，但计划表任务 0.6 只列三个只读工具且后续阶段亦无 glob 排期——按计划表执行，glob 悬空排期已登记到测试债务表（供用户后续决策）。

### 4.4 主循环与 CLI

```python
AgentLoop(llm, registry, history, max_steps=50, on_event=None)
async run(task: str) -> str
```

终止条件（Day 1）：①自然终止（无 tool_calls）；②步数上限（默认 50，可配）；③解析失败熔断（`ParseCircuitBroken` 异常终止路径，见 §4.1——前两类为正常终止，第三类为异常终止，loop 需捕获并做 History 善后）。守卫检查点固定在每次请求 LLM 之前（概设 §4.1）。

**on_event 事件契约**（loop → CLI 的渲染通道，Day 1 纯文本，M3 升级 rich）：

| 事件 | payload | CLI 动作 |
|------|---------|---------|
| `tool_start` | call: ToolCall | 打印 `  ⏺ {name} {参数摘要}` |
| `tool_end` | call: ToolCall, result: ToolResult | 打印 `    ⎿ {结果摘要一行}`（ok=False 时输出错误行） |

**History REPL 语义**：CLI 与 AgentLoop 共享同一 History 实例，跨轮次累积（每轮 run 追加 user + assistant/tool 消息，多轮上下文连续）；`/exit` 退出时随进程丢弃（JSONL 持久化是 Day 2 任务 0.14）。

CLI：argparse（`--workspace`，默认当前目录）→ 环境变量加载配置 → 构建 loop → REPL（`/exit`、`/quit`、Ctrl+C/Ctrl+D 退出）。工具调用行打印 `⏺ 工具名 参数摘要` + `  ⎿ 结果摘要`（纯文本版符号语言，M3 升级 rich 主题）。

## 5. 关键设计决策

| 决策点 | 选项 A | 选项 B | 选择 | 理由 |
|--------|--------|--------|------|------|
| LLM HTTP 通道 | openai SDK | httpx 自建 SSE | A | 需求文档约束 3 明确「允许使用模型厂商 API 客户端库」；概设 §3「只把 SDK 当 HTTP 客户端」；重试/退避/流式拼装/解析容错仍全部自研，满足「自研」约束 |
| arguments 解析位置 | client 层 | dispatch 层 | B | 错误统一走 ToolResult(ok=False) 回喂通道，client 保持纯传输职责；与「幻觉工具→回喂可用工具列表」共享同一条路径（概设 §4.3） |
| Day 1 模型配置 | 环境变量 | models.toml | A | 注册表是 M3.4 任务；环境变量单模型兜底也是计划表风险预案的裁剪方向 |
| grep 实现 | 子进程调系统 grep | 纯 Python re 遍历 | B | 跨平台（Linux 一等公民 + Windows 兼容）；规避 shell 注入面；性能以大小/数量上限兜底 |
| 输出防护 | 无限制 | read 2000 行 / grep 200 条命中上限 | B | L0 截断（M2）前的最小上下文防爆措施，截断时显式标注 |
| stream 拼装位置 | 独立 llm/stream.py（概设 §10） | 并入 client.py | B | Day 1 仅一个消费方，单文件集中拼装逻辑（风险缓解也依赖此点）；M2 若出现第二消费方再按概设拆分 |
| 流式拼装容错上限 | 无限回喂 | 解析失败连续 3 次熔断（全局计数） | B | 概设 §4.3 明确「最多 2 轮」修正（第 3 次失败终止）；模型修正重发携带新 call_id，故按全局连续失败轮数计数而非 call_id；仅靠步数上限 50 兜底时最坏空转约 47 步 |

## 6. 编码策略决策

按 §0 裁剪声明，本轮所有步骤为 **Code-First（跳过 Test 产出的裁剪变体）**：

| 步骤 | 任务描述 | 策略 | 决策依据 |
|------|---------|------|---------|
| Step 1 | 0.3 项目骨架：pyproject.toml【含 `requires-python = ">=3.11"`】/.gitignore/config.py/包结构 | Code-First | 配置与打包文件，无业务逻辑 |
| Step 2 | 0.4 LLM 客户端：client.py | Code-First | 契约为 OpenAI 标准协议（非自研跨服务契约）；完整策略本应 TDD（重试/拼装为纯逻辑），本轮按用户约束跳过测试产出，登记测试债务 |
| Step 3 | 0.5 工具基座：base.py | Code-First | 协议/registry 脚手架代码 |
| Step 4 | 0.6 三个只读工具：files.py/search.py | Code-First | 只读薄封装，逻辑简单 |
| Step 5 | 0.7 主循环 v0：history.py/loop.py | Code-First | 终止条件逻辑本应 TDD，同 Step 2 处理，登记测试债务 |
| Step 6 | 0.8 简版 CLI：prompts.py/cli.py/__main__.py | Code-First | 胶水代码 |

## 7. 实施步骤

- [ ] Step 1：项目骨架（pyproject.toml【含 `requires-python = ">=3.11"`】、.gitignore、TODO.md、src/glaucous 包结构、config.py）（策略：Code-First）
- [ ] Step 2：LLM 客户端 llm/client.py（重试退避 + 流式读取 + tool-call delta 拼装 + on_text 回调）（策略：Code-First）
- [ ] Step 3：工具基座 tools/base.py（Tool 协议、ToolResult、ToolRegistry、轻量 schema 校验、dispatch 容错回喂）（策略：Code-First）
- [ ] Step 4：只读工具 tools/files.py + tools/search.py（策略：Code-First）
- [ ] Step 5：消息模型 context/history.py + 主循环 agent/loop.py（策略：Code-First）
- [ ] Step 6：system prompt ui/prompts.py + 简版 CLI cli.py + __main__.py（策略：Code-First）

## 8. 风险与注意事项

| 风险 | 缓解 |
|------|------|
| openai SDK 流式 delta 结构随版本变化 | pyproject 依赖锁定 `openai>=1.40,<2`；拼装逻辑集中在 client.py 单一函数 |
| 模型不支持 function calling | 本轮不做文本协议兜底（P1）；API 报错信息由 CLI 直接呈现 |
| 大仓库 grep 性能 | >5MB 文件跳过 + 命中上限 200 + .git 排除 |
| Windows 开发 / Linux 目标 | 全程 pathlib + 显式 `encoding="utf-8"`；路径处理不拼接字符串 |
| **Day 1 无工作区沙箱**（绝对路径原样放行，只读工具可读区外文件） | 沙箱属 M1 任务 1.1 既定排期；在此之前 Glaucous 仅用于可信环境、由本人运行 |
| 用户约束导致无测试保护 | §9 测试债务清单显式登记，M4 偿还 |

## 9. 测试策略

**本轮不产出、不执行任何测试**（用户明确约束）。测试债务清单（M4 任务 4.1/4.2 偿还）：

| 债务项 | 应覆盖 | 对应概设测试文件 |
|--------|--------|-----------------|
| 流式 tool-call 拼装 | delta 增量、多 index 并发、无 tool_calls、纯文本 | test_stream_parsing.py |
| 重试退避 | 429/5xx 可重试、4xx 不重试、退避间隔、超限抛出 | —（mock LLM） |
| schema 校验 | required 缺失、类型不符、enum 非法、合法通过 | — |
| dispatch 容错 | 幻觉工具、非法 JSON、校验失败、执行异常，前 2 次解析失败 ok=False 回喂、连续第 3 次抛 ParseCircuitBroken 终止；熔断后 History 序列合法、后续轮次可正常请求 | — |
| 只读工具 | 行号输出、offset/limit、目录排序、grep 命中/无命中/非法正则 | — |
| 主循环终止 | 自然终止、步数上限熔断、解析失败熔断终止（loop 捕获 ParseCircuitBroken + 返回诊断文本） | test_loop_termination.py |
| history.view() 消息序列 | assistant/tool_call_id 配对合法、多轮累积、终答入史 | —（M4 新增，B1 修复对应债务） |
| config 环境变量解析 | 缺 API key 报错退出、默认值回退、自定义覆盖 | — |

另登记两项**无排期悬空项**（上游文档缝隙，需用户后续决策）：① glob 工具（概设 §5.6 有、计划表无排期）；② 重复失败熔断终止条件（概设 §4.1-⑤，计划表 M1~M5 无对应任务编号）。

Day 1 验收（终端说「看看这个项目的结构」得到正确回答）同样按用户约束**不在本轮执行**，留待环境就绪后由用户自行验证。
