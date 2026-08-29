# OpenCode 项目分析

> 分析对象：`c:\Users\thinkpad\Desktop\opencode\opencode`（Go 1.24，约 100 个源文件）
> 分析目的：学习一个"终端 AI 编程助手"的架构思想与工程实践
> 项目状态：已归档，后续以 [Crush](https://github.com/charmbracelet/crush)（Charm 团队）名义继续开发

---

## 一、项目是什么

OpenCode 是一个 **Go 语言编写的终端 AI 编程助手**（类似 Claude Code 的开源实现）：

- 在终端里提供 TUI（Bubble Tea 构建）聊天界面，也支持 `-p` 一次性非交互模式
- 接入多家 LLM（Anthropic / OpenAI / Gemini / Bedrock / Azure / Copilot / Groq / OpenRouter / xAI / 本地端点）
- AI 通过**工具调用**（bash、读写文件、grep/glob、patch、fetch、子 agent…）真正动手改代码
- 集成 **LSP**（编辑后自动回报诊断错误）和 **MCP**（外部工具协议扩展）
- 用 **SQLite** 持久化会话/消息/文件快照，支持自动摘要压缩（auto compact）

一句话定位：**一个极简但五脏俱全的 Agentic Coding Loop 参考实现**。它的价值不在于功能多，而在于用最少的抽象把"LLM Agent"这件事讲清楚了。

---

## 二、整体架构与数据流

```
┌─────────────────────────────────────────────────────────────┐
│                          TUI (Bubble Tea)                    │
│   page(chat/logs) · dialog(permission/models/session...)     │
└──────────────▲──────────────────────────────────┬───────────┘
               │ tea.Msg（经 channel 桥接）        │ 调用服务方法
┌──────────────┴──────────────────────────────────▼───────────┐
│                     App（服务装配层 / 组合根）                 │
│  Sessions · Messages · History · Permissions · CoderAgent    │
│  LSPClients                                                  │
└───┬──────────┬───────────┬────────────┬─────────────┬───────┘
    │          │           │            │             │
┌───▼───┐ ┌────▼────┐ ┌────▼────┐ ┌─────▼─────┐ ┌─────▼─────┐
│Session│ │ Message │ │ History │ │Permission │ │   Agent    │
│Service│ │ Service │ │ Service │ │  Service  │ │  Service   │
└───┬───┘ └────┬────┘ └────┬────┘ └─────┬─────┘ └─────┬─────┘
    │          │           │            │             │
    │      全部内嵌 pubsub.Broker[T]，变更即广播事件    │
    │          │           │            │      ┌──────▼──────┐
┌───▼──────────▼───────────▼────────────▼──┐   │ Provider     │
│              SQLite (sqlc + goose)        │   │(统一事件流)   │
└───────────────────────────────────────────┘   └──────┬──────┘
                                          ┌────────────▼───────────┐
                                          │ Anthropic/OpenAI/Gemini │
                                          │ /Bedrock/Copilot/...    │
                                          └────────────┬───────────┘
                                                       │ 工具调用
                                    ┌──────────────────▼──────────────┐
                                    │ Tools: bash/edit/write/patch/    │
                                    │ grep/glob/ls/view/fetch/agent/  │
                                    │ diagnostics + MCP 动态工具        │
                                    └─────────────────────────────────┘
```

**核心数据流（一次用户提问的完整旅程）：**

1. 用户在 TUI 输入 → 调 `CoderAgent.Run(sessionID, content)`
2. Agent 把用户消息写入 DB，连同历史消息交给 `Provider.StreamResponse()`
3. Provider 把各家 SDK 的流式响应归一化成 `ProviderEvent` 通道
   （`thinking_delta / content_delta / tool_use_start / tool_use_stop / complete / error`）
4. Agent 逐事件更新 assistant 消息并写库 → Message Service 广播事件 → TUI 实时渲染
5. 若结束原因是 `tool_use`：逐个执行工具（先过权限系统），把工具结果作为 `Tool` 角色消息追加
6. **回到第 2 步循环**，直到模型不再请求工具 → 本轮结束
7. 全程任何服务的变更都通过 pubsub 广播，TUI 只是事件的一个消费者

---

## 三、目录结构

```
opencode/
├── main.go                     # 入口：仅 panic 恢复 + cmd.Execute()
├── cmd/
│   ├── root.go                 # Cobra 命令、启动编排、TUI 订阅桥接
│   └── schema/main.go          # 生成配置 JSON Schema
├── internal/
│   ├── app/                    # ★ 组合根：装配所有服务（App 结构体）
│   ├── config/                 # 配置加载（多路径查找 + 默认值）
│   ├── db/                     # SQLite 连接 + goose 迁移 + sqlc 生成代码
│   ├── session/  message/  history/  permission/   # 领域服务（全部带事件广播）
│   ├── pubsub/                 # ★ 泛型事件总线（约 130 行，全项目骨架）
│   ├── llm/
│   │   ├── agent/              # ★ Agent 循环、工具装配、子 agent、MCP 适配
│   │   ├── provider/           # ★ 多 LLM 适配层（统一事件流）
│   │   ├── models/             # 模型注册表（静态元数据：价格/上下文窗口/能力）
│   │   ├── prompt/             # 系统提示词（coder/task/title/summarizer）
│   │   └── tools/              # 11 个内置工具 + 持久化 shell
│   ├── lsp/                    # LSP 客户端（JSON-RPC/stdio）+ 文件监听
│   ├── tui/                    # Bubble Tea UI（page/layout/components/dialog/theme）
│   ├── logging/                # slog + 文件输出 + 事件订阅
│   ├── diff/  fileutil/  format/  completions/  version/   # 工具性包
```

---

## 四、核心设计思想（重点）

### 思想 1：130 行的泛型事件总线，撑起整个系统的解耦

`internal/pubsub/broker.go` 是全项目最值得学习的文件：

```go
type Broker[T any] struct { ... }
func (b *Broker[T]) Subscribe(ctx context.Context) <-chan Event[T]
func (b *Broker[T]) Publish(t EventType, payload T)
```

**设计要点：**

- **泛型**：`Broker[T]` 让每种领域事件有自己的类型安全通道（`Broker[Session]`、`Broker[message.Message]`、`Broker[PermissionRequest]`…），无需 `interface{}` 断言。
- **组合优于继承**：每个服务直接**内嵌** `*pubsub.Broker[T]`：

  ```go
  type service struct {
      *pubsub.Broker[Session]   // Service 接口同时内嵌 pubsub.Suscriber[Session]
      q db.Querier
  }
  ```

  于是"可订阅"成为服务接口的一部分（`Service interface { pubsub.Suscriber[Session]; ... }`），调用方拿到服务就能 `Subscribe`，不需要额外的总线注册。
- **生命周期绑定 context**：`Subscribe(ctx)` 返回的 channel 在 ctx 取消时自动注销并关闭——没有全局注销表，没有泄漏。
- **背压策略简单粗暴但清醒**：发布时 `select { case sub <- event: default: }`——慢消费者直接丢事件（非阻塞）。桥接层（cmd/root.go）再用 2 秒超时二次兜底并记日志。**宁可丢帧不阻塞主流程**，这对实时 UI 是正确的取舍。

**借鉴价值**：任何 Go 应用需要"服务变更 → 多个消费者（UI、日志、同步器）响应"时，这 130 行就是现成模板。

### 思想 2：统一消息模型 —— 一条消息是"部件列表"

`internal/message/content.go` 没有为文本/思考/工具调用建不同的消息类型，而是：

```go
type ContentPart interface{ isPart() }   // 密封接口（sealed interface 的 Go 惯用法）

type Message struct {
    Role  MessageRole    // user / assistant / tool / system
    Parts []ContentPart  // TextContent | ReasoningContent | ToolCall | ToolResult
                         // | BinaryContent(附件) | ImageURLContent | Finish(结束标记)
}
```

**精妙之处：**

- **流式更新 = 部件追加**：`AppendContent(delta)`、`AppendReasoningContent(delta)`、`AddToolCall`、`AddFinish`。模型流式输出被自然建模为"向消息追加/累积部件"，UI 渲染和持久化共用同一套状态。
- **Finish 也是部件**：结束原因（`end_turn / tool_use / canceled / permission_denied / max_tokens`）是消息的一部分而非外部字段，任何时刻从 DB 读回消息都能完整还原当时状态。
- **Provider 无关**：各厂商 SDK 格式不同，但都在自己的适配层里转换成这个中性模型。上层（Agent、DB、TUI）完全不知道 Anthropic 和 OpenAI 的区别。

**借鉴价值**：设计多来源、多形态数据的聚合模型时，"统一容器 + 密封部件接口 + 累积器方法"比继承树健壮得多。

### 思想 3：Agent 循环 = 一个 while 循环 + 一个事件流

`agent.processGeneration` 是全部"智能"所在，去掉细节只有三行逻辑：

```go
for {
    agentMessage, toolResults, err := a.streamAndHandleEvents(ctx, sessionID, msgHistory)
    if agentMessage.FinishReason() == FinishReasonToolUse && toolResults != nil {
        msgHistory = append(msgHistory, agentMessage, *toolResults)  // 把工具结果喂回去
        continue                                                      // 再问一轮
    }
    return ...                                                        // 模型不再要工具，结束
}
```

围绕这个循环的工程细节值得逐一学习：

| 机制 | 实现 | 思想 |
|---|---|---|
| **并发控制** | `activeRequests sync.Map[sessionID]cancelFunc` | 每会话同时只跑一个请求（`ErrSessionBusy`）；cancel 函数存进 map，取消 = 取出并调用 |
| **取消传播** | 每轮循环前 `select <-ctx.Done()`；工具执行循环里逐个检查 | 取消不是"立刻杀死"，而是"在下一个安全点优雅停止"，并把剩余工具调用标记为 `canceled` 写回，保持历史一致 |
| **权限拒绝的语义** | 工具返回 `ErrorPermissionDenied` → 剩余工具全部标记取消，消息以 `FinishReasonPermissionDenied` 结束 | 拒绝是"终止本轮"而不是"报错重试"，语义清晰 |
| **标题生成** | 首条消息时 `go generateTitle()` 用廉价小模型异步生成会话标题 | 不阻塞主流程；用不同模型干不同的事（见思想 5） |
| **上下文压缩** | `Summarize()` 用 summarizer 模型生成摘要 → 存为消息并记 `session.SummaryMessageID`；下次 `Run` 时 `msgs = msgs[summaryIndex:]` 只带摘要之后的历史 | **"摘要即检查点"**：不删除历史（可回溯），只移动上下文窗口的起点。这是 auto compact 的核心 |
| **用量与成本** | `TrackUsage`：按模型注册表里的单价 × token 数（区分缓存读/写）累加到会话 | 成本核算内建在循环里，而非事后统计 |
| **持久化优先** | 每个流事件都 `messages.Update()` 写库 | 崩溃/重启后对话不丢；代价是写放大（后文"取舍"再谈） |

### 思想 4：Provider 适配层 —— 用泛型消灭重复，用事件流抹平差异

`internal/llm/provider/provider.go`：

```go
type Provider interface {
    SendMessages(ctx, messages, tools) (*ProviderResponse, error)
    StreamResponse(ctx, messages, tools) <-chan ProviderEvent   // ★ 核心
    Model() models.Model
}

type ProviderClient interface {   // 每家厂商实现这一对
    send(...)  (*ProviderResponse, error)
    stream(...) <-chan ProviderEvent
}

type baseProvider[C ProviderClient] struct {   // 泛型模板
    options providerClientOptions
    client  C
}
```

**设计要点：**

1. **统一事件流是最高抽象**。不管 Anthropic 的 SSE 还是 OpenAI 的 stream，最终都翻译成 10 种 `EventType`。Agent 只消费事件，永远不碰 SDK。
2. **`baseProvider[C]` 泛型模板**承接公共逻辑（清洗空消息、暴露 Model），每个厂商文件（`openai.go`、`anthropic.go`…）只写两件事：`convertMessages`（内部消息 → SDK 格式）和 `stream`（SDK 流 → 内部事件）。**新增一个厂商 = 一个 ~400 行的转换文件**。
3. **功能选项模式**（`WithAPIKey / WithModel / WithSystemMessage / WithAnthropicOptions…`）装配客户端，各厂商特有选项（reasoning effort、should-think 函数）各自成体系，不互相污染。
4. **复用降低厂商成本**：Groq / OpenRouter / xAI / 本地模型全部复用 `OpenAIClient`，只是换 baseURL 和 header——**"OpenAI 兼容"是一等公民**。
5. **模型注册表是纯数据**（`models.SupportedModels`）：ID、API 名称、四档价格（缓存读/写 × 输入/输出）、上下文窗口、`CanReason`、`SupportsAttachments`。能力开关驱动上层逻辑（如附件支持、推理选项），而不是到处写 `if provider == "xxx"`。

### 思想 5：多 Agent 角色 = 同一套机制的不同配置

项目里没有"标题服务""摘要服务"这样的独立模块，而是：

```
config.AgentCoder     → 主对话（全量工具，可推理）
config.AgentTask      → 子 agent 搜索任务（只读工具）
config.AgentTitle     → 生成标题（小模型，80 tokens）
config.AgentSummarizer→ 会话摘要
```

它们都是 `agent.NewAgent(agentName, ...)` 的实例，区别仅在配置（模型、maxTokens）和**工具集**：

```go
func CoderAgentTools(...)  []tools.BaseTool { /* bash/edit/write/patch/... 全量 */ }
func TaskAgentTools(...)   []tools.BaseTool { /* glob/grep/ls/view 只读 */ }
```

**"能力 = 工具集"** 的等式在这里体现得淋漓尽致：想让一个 agent 变成只读助手？换掉工具列表即可，一行不用改。

### 思想 6：子 Agent 只是一个普通工具（递归式委托）

`agent-tool.go` 的 `agent` 工具：

- 主 agent 调用它 → 它**新建一个 Task Agent + 一个子会话**（`ParentSessionID` 指向父会话）→ 跑完取最终回答返回给主 agent
- 子 agent 成本会累加回父会话（`parentSession.Cost += updatedSession.Cost`）
- 工具描述里明确告诉主模型："子 agent 无状态、只读、结果用户看不见、要写清返回要求"

**借鉴价值**：把"委托子任务"建模为工具而不是特殊控制流，主循环完全不需要知道子 agent 的存在。这是 Claude Code 同款设计（本项目的工具描述文风也明显受其影响）。

### 思想 7：权限系统 = 阻塞式请求 + 事件广播

`internal/permission/permission.go` 只有 ~120 行，却完整解决了"AI 要执行危险操作，先问人"的问题：

```go
func (s *permissionService) Request(opts CreatePermissionRequest) bool {
    if 会话被标记自动批准 { return true }              // 非交互模式
    if 匹配到本会话已授权记录 { return true }           // (工具+动作+路径) 记忆授权
    respCh := make(chan bool, 1)
    s.pendingRequests.Store(permission.ID, respCh)
    s.Publish(pubsub.CreatedEvent, permission)          // TUI 弹出权限对话框
    return <-respCh                                      // ★ 阻塞等待人类决定
}
```

- 工具在自己的 goroutine 里**同步阻塞**等结果，写工具的人不需要懂任何异步逻辑——`if !permissions.Request(...) { return denied }` 一行接入。
- TUI 侧：订阅事件 → 弹框 → 用户按键 → `Grant/Deny` 向 channel 写入 → 工具继续。
- `GrantPersistant` 把授权记入会话级列表，同一目录同类操作不再打扰（对应快捷键 `A`）。
- 危险分级在工具层做：bash 工具维护 `bannedCommands`（curl/wget/nc…直接拒绝）和 `safeReadOnlyCommands`（git status、ls…免审批）两个白/黑名单。

**借鉴价值**：用"同步阻塞 + 事件通知"把人在回路（human-in-the-loop）做成了对业务代码零侵入的横切能力。

### 思想 8：持久层 = sqlc（类型安全）+ goose（迁移）+ 文件快照

- **SQLite + WAL**：`PRAGMA journal_mode=WAL, synchronous=NORMAL`，单文件数据库放 `data.directory`，天然适合本地工具。
- **goose 迁移嵌入二进制**：`//go:embed migrations/*.sql` 打包迁移文件，启动即自动升级——整个应用零外部文件依赖。
- **sqlc 生成查询代码**：`sessions.sql.go / messages.sql.go / files.sql.go` + `Querier` 接口 + `WithTx` 事务支持——手写 SQL 在 `internal/db/sql/*.sql`，是事实来源，Go 代码只是产物，杜绝手写 ORM 漂移。
- **不变式下沉数据库（触发器）**：三张表各有触发器自动维护 `updated_at`；`messages` 增删时触发器自动增减 `sessions.message_count`。**让数据库保证一致性，应用层就不用写一堆"记得同步计数字段"的代码**——本地单写者场景下这是非常划算的取舍。
- **消息部件的存储格式**：`messages.parts` 是 JSON 数组，每个部件包成 `{"type": "text|tool_call|...", "data": {...}}`（`partWrapper`），反序列化按 `type` 分发——自描述的标签联合，与内存模型一一对应。
- **History 服务 = 穷人版版本控制**：`edit/write/patch` 工具改文件前先存旧内容（版本 `initial`），改后存新内容（`v1 → v2…`）；`UNIQUE(path, session_id, version)` 约束冲突时在事务里**自动版本号 +1 重试最多 3 次**——并发写同一路径也不会丢。这给"撤销 AI 改动"提供了数据基础，也支撑了 TUI 侧边栏的文件变更追踪。
- **子会话树**：`sessions.parent_session_id` 让标题会话、子 agent 会话挂在主会话下，成本逐级汇总；子会话 ID 是**确定性**的（标题会话 = `"title-"+父ID`，任务会话 = 工具调用 ID），天然幂等；`ListSessions` 只返回 `parent IS NULL` 的顶层会话。

### 思想 9：提示词即产品

`internal/llm/prompt/coder.go` + 各工具的 `Description` 字段暴露了一个重要事实：**这类项目一半的"代码"是写给模型看的散文**。

- 系统提示词按厂商分版本（Anthropic 版 vs OpenAI 版），并动态拼接环境信息（cwd、是否 git 仓库、平台、日期、目录列表、LSP 说明）。
- bash 工具的描述是一份**操作手册**：禁用命令清单、git commit 六步流程（含 `<commit_analysis>` 结构化思考标签）、HEREDOC 传消息的示例、创建 PR 的完整剧本。
- edit 工具描述用大写警告强调 `old_string` 唯一性、要求带 3-5 行上下文。
- 主提示词直接教模型行为契约："持续工作直到任务完全解决""不要猜，用工具确认""回复控制在 4 行以内"。

**借鉴价值**：工具描述不是文档，是**运行时注入的控制指令**。写工具 = 写接口 + 写给模型的 SOP。

### 思想 10：TUI 与后端只靠事件相连

`cmd/root.go` 的桥接代码是教科书式的"后端事件 → 前端消息"适配：

```go
setupSubscriber(ctx, &wg, "sessions",     app.Sessions.Subscribe,     ch)
setupSubscriber(ctx, &wg, "messages",     app.Messages.Subscribe,     ch)
setupSubscriber(ctx, &wg, "permissions",  app.Permissions.Subscribe,  ch)
setupSubscriber(ctx, &wg, "coderAgent",   app.CoderAgent.Subscribe,   ch)
setupSubscriber(ctx, &wg, "logging",      logging.Subscribe,          ch)
// 每个订阅一个 goroutine → 汇入缓冲 100 的 chan tea.Msg → program.Send(msg)
```

- 5 个泛型订阅器（还是同一个 `setupSubscriber` 泛型函数）把异构事件流归一成 `tea.Msg`。
- TUI 因此可以整体换成 Web/HTTP 前端而不动任何服务代码——非交互模式（`-p`）已经证明了这一点：同一套 App，只是换个消费者。
- 退出时按依赖顺序清理：`app.Shutdown()` → 取消订阅 → 等待 handler → 关 channel，带 5 秒超时兜底。

---

## 五、LSP 与 MCP：两个"协议集成"样本

### LSP（Language Server Protocol）

**手写 JSON-RPC 客户端的极简范式**（`internal/lsp/transport.go` + `client.go`）：

- 一个 `Message` 结构通吃请求/响应/通知三种形态，参数用 `json.RawMessage` **延迟解码**；消息帧严格遵循 `Content-Length` 头 + JSON 体。
- 并发模型只有三件套：`atomic.Int32` 自增分配请求 ID + `map[int32]chan *Message` 存待应答 + **单条读消息循环**三路分发（server 请求 / 通知 / 响应）。约 100 行实现全双工并发。
- 协议类型**不手写**：`protocol/tsprotocol.go` 等文件头部标注 `Code generated for LSP. DO NOT EDIT`——直接复用 gopls 从 LSP 3.17 metaModel.json 生成的类型与编解码代码（谱系上源自 isaacphi/mcp-language-server，README 致谢里也承认了）。**"不手写上千行协议类型，拿成熟生成器的产物"** 是极务实的做法。

**工程细节（处处是防御性设计）：**

- `didChange` 发**全文替换**而非增量 diff——实现简单、不会算错偏移。
- 文件监听链路：server 通过 `client/registerCapability` 注册 glob 过滤规则 → `fsnotify` 递归监听工作区 → 事件先按注册的 glob/WatchKind 过滤 → 写事件 300ms debounce → 分流（已打开文件走 `didChange`，其余走 `workspace/didChangeWatchedFiles`）。
- 启动策略：每个配置的 language server **并行后台启动**，`WaitForServerReady` 每 500ms 用 `workspace/symbol` 请求 ping 探活，并按 server 类型（TS/Go/Rust）差异化预热——先打开 `tsconfig.json`/`go.mod` 等关键文件。**不信任标准握手，靠探测+预热**。
- 崩溃自愈：watcher goroutine 包 `RecoverPanic`，崩溃回调 `restartLSPClient` 重建。
- 值得注意：此版本**没有**严格的"扩展名→单一 server"路由——工具把文件广播式打开到所有 client，由各 server 自行忽略不支持的语言。简单，但对多语言项目略显粗放。

**对 AI 的核心价值——"改完即诊断"闭环：**

- `diagnostics` 工具的关键机制 `waitForLspDiagnostics`：打开文件前快照当前诊断 → 临时重注册 publishDiagnostics handler → `select` 等待（新诊断到达 / 5 秒超时 / ctx 取消）。**这是把异步推送转成同步工具调用的典型做法**，任何"等异步结果"的场景都可套用。
- `edit/write/patch/view` 工具都复用了这个机制：改完文件自动等诊断、把结果按 `<file_diagnostics>`/`<project_diagnostics>` 标签（行号+列号+来源+错误码，Error 优先，每组截断 10 条，末尾附统计摘要）拼进工具输出底部。**AI 每次改代码立刻"看见"编译器/类型检查器的反馈**——这是提升 agent 一次成功率的关键设计。

### MCP（Model Context Protocol）

- 基于 `mark3labs/mcp-go`：启动时连接配置里的每个 MCP server（stdio / SSE 两种 transport，SSE 支持自定义 Headers），`Initialize` → `ListTools` 后把每个远端工具包装成内部 `BaseTool`（适配器模式）：`InputSchema.Properties/Required` 直接映射为 `ToolInfo.Parameters/Required`。
- 命名约定 `服务器名_工具名` 防多 server 冲突；MCP 工具与内置工具享受完全相同的待遇：进同一工具列表、走同一权限审批。
- 已知取舍：每次调用都新建连接（`runTool` 里 `Initialize` → `CallTool` → `defer c.Close()`），简单但低效；工具列表包级缓存（`var mcpTools`）不失效。

---

## 六、TUI 架构：Bubble Tea 的工程化实践

TUI 是项目代码量最大的部分（约 40 个文件），其组织方式本身就是一个可复用的"中大型终端应用"模板。

### 顶层 Model：页面注册表 + 对话框标志位

根 Model `appModel` 的结构极简：

```
pages map[PageID]tea.Model   // 懒加载：切到时才 Init + SetSize
showQuit / showHelp / showSession / showCommands / showModels /
showPermissions / ... bool   // 约十个对话框，各一个可见性标志
```

- `Init()` 用 `tea.Batch` 聚合所有子组件的初始命令；`Update()` 是一个大型 `tea.Msg` 类型 switch；`View()` = 当前页 + 状态栏垂直拼接，再按**代码顺序**用 `PlaceOverlay` 把打开的对话框居中叠加——模态层级即绘制顺序。
- 后端事件直接以 `pubsub.Event[T]` 的身份成为 `tea.Msg`，`Update` 里 `case pubsub.Event[message.Message]:` 即完成路由。**后端对 TUI 零感知**。

### layout 包：装饰器式布局三件套

- 三个小接口按**能力探测**组合：`Focusable`、`Sizeable`（尺寸自顶向下传播，扣除边框/padding 后递归给内容）、`Bindings`（聚合快捷键供帮助页展示）。
- `Container` = `tea.Model + Sizeable + Bindings` 的装饰器，函数式选项（`WithPadding`/`WithRoundedBorder`…）配置；`SplitPaneLayout` 按比例分配左/右/下面板，支持**运行时动态挂卸**（聊天页 sidebar 按需挂载）。
- `PlaceOverlay` 在字符串层面合成前景到背景指定坐标，还能用 `░` 画阴影。

### 流式渲染的性能设计（最值得抄的部分）

- 消息列表监听 `pubsub.Event[message.Message]` 增量更新；关键是 `cachedContent map[string]cacheItem`——**按"消息 ID + 终端宽度"缓存已渲染字符串，流式更新只失效并重渲最后一条，历史消息全部命中缓存**。这是长会话流畅的关键。
- Markdown 用 glamour 渲染、chroma 语法高亮，渲染器的 StyleConfig 每次从 `theme.CurrentTheme()` 动态生成。
- 工具调用有专门渲染：未完成显示动作占位，完成后 Edit 显示彩色 diff、View/Write 包成围栏代码块，子 agent 任务递归渲染为 `└` 嵌套。
- spinner 文案（Thinking / Building tool call / Waiting for tool response）**由消息里的工具调用状态推导**，而不是独立维护一份状态。

### 对话框系统：集中式可见性 + 模态不打断事件流

- 打开 = 顶层置标志并注入数据；关闭 = 组件发 `CloseXxxMsg` 由顶层清标志。组件自身保持"纯展示"。
- 键盘路由分层：参数对话框 → 全局键 → 按打开状态转发给各对话框；关键策略是**"模态只拦截 KeyMsg，pubsub 事件继续下流"**——弹框打开期间后台流式更新不断。
- 权限对话框是与思想 7 的完整闭环：后端 `Publish(PermissionRequest)` → 弹框 → 用户按键 → `Grant/GrantPersistant/Deny` → 后端阻塞解除。组件内还带 diff/markdown 缓存避免重复渲染。
- 命令面板支持内置命令 + 用户自定义命令（`.md` 文件，`$参数` 占位符触发多参数输入对话框）。

### 主题系统：语义色槽 + 自注册

- `Theme` 接口定义约 50 个**语义色槽**（状态/文本/边框/diff/markdown/语法…），全部用 `lipgloss.AdaptiveColor`（明暗双值自动适配）。
- 9 个主题各自在 `init()` 里 `RegisterTheme` **自注册**到全局单例管理器；切换时写回配置文件并广播 `ThemeChangedMsg`，消息列表清缓存重渲。
- 铁律：**颜色从不固化在组件状态里**，所有渲染函数即时调 `theme.CurrentTheme()`。

### TUI 层可学模式小结

1. 组件间不调方法，全靠消息；`CmdHandler` 把任意消息包成 Cmd（"向自己/父级发消息"的惯用法）。
2. 全局状态（模态可见性、当前页）收敛在顶层，子组件无状态化。
3. 小接口 + 类型断言做能力组合，优于大而全的接口。
4. 渲染缓存按"内容键 + 布局键"失效；状态尽量推导而非维护。

---

## 七、配置、日志与优雅降级

### 配置系统（internal/config/）

- **viper 统一多来源**：全局配置文件（`$HOME/.opencode.json`、`$XDG_CONFIG_HOME/opencode/` 等多路径查找）+ `OPENCODE_` 前缀环境变量；再用一个独立 viper 实例读**项目级** `.opencode.json` 并 `MergeConfigMap` 覆盖——**项目配置优先于全局**，这是开发者工具的标准答案。
- **环境变量驱动的默认模型选择**：`setProviderDefaults` 扫描各家 API key 环境变量（`ANTHROPIC_API_KEY`、`GITHUB_TOKEN`…），按内置的厂商优先级为 4 个 agent 角色挑选默认模型；还会从 `github-copilot/hosts.json` 等位置自动发现 Copilot 凭证。**零配置也能跑**的关键。
- **校验即降级**：`Validate` 发现 agent 配置的模型/厂商非法时不报错退出，而是静默回退默认值——面向终端用户的健壮性选择。
- **JSON Schema 也是产品的一部分**：`cmd/schema/main.go` 手工构建 draft-07 schema，并把模型枚举从 `models.SupportedModels` **动态注入**，产出 `opencode-schema.json`——用户写 `.opencode.json` 时编辑器有完整智能提示。
- 细节：`ContextPaths` 兼容 `CLAUDE.md`、`.cursorrules` 等别家工具的上下文文件约定；`init.go` 用数据目录下的标志文件控制"首次引导对话框"只弹一次。

### 日志系统（internal/logging/）：把日志变成可订阅的数据

一个很精巧的小设计：自定义 `io.Writer` 作为 slog 的输出目标，写入时**把每行 logfmt 反向解析回结构化 `LogMessage`**，存入内存并 `Publish` 到 pubsub——于是 TUI 的日志页（Ctrl+L）能把日志实时渲染成可交互的表格/详情面板，而不需要任何"双写"逻辑。**日志既是文件，也是事件流**。

另有：带 `Persist` 后缀的日志方法用特殊键标记"状态栏常驻显示"的错误；`RecoverPanic` 统一兜底并落盘崩溃日志；`OPENCODE_DEV_DEBUG=true` 时把每次 LLM 请求/响应原文落盘，供调试回放。

### 优雅降级是全项目的口头禅

| 场景 | 降级链 |
|---|---|
| 文件补全（`@`） | `rg \| fzf` 管道 → 只有 `rg` 用纯 Go 模糊匹配兜底 → 只有 `fzf` 用 doublestar 喂它 → 都没有就纯 Go glob + fuzzy |
| 权限 | 会话自动批准 → 会话级记忆授权 → 弹框询问 |
| 配置校验 | 非法值 → 回退默认而非报错 |
| LSP 文件版本号 | 解析失败 → 退化为时间戳版本号 |

**思想**：外部依赖（rg、fzf、language server、MCP server）全部"能用则用、不能用有兜底、再不行跳过"，任何单一外部工具缺失都不会让应用不可用。

---

## 八、启动流程全景（cmd/root.go）

```
main() → cmd.Execute() → cobra RunE:
 1. config.Load(cwd)            # 多路径查找 .opencode.json + 默认值合并
 2. db.Connect()                # 开库 + pragma + goose 迁移
 3. app.New(ctx, conn)          # 装配：Sessions/Messages/History/Permissions
                                #       + 后台初始化 LSP + 创建 CoderAgent
 4. go 预热 MCP 工具列表（30s 超时）
 5. 分叉：
    - 有 -p 参数 → RunNonInteractive：建会话 + AutoApproveSession
                   + Agent.Run + 等结果 + 按 text/json 格式化输出
    - 否则       → tea.NewProgram(tui.New(app)) + setupSubscriptions
                   + 消息泵 goroutine → program.Run() → 依序清理
```

**值得注意**：交互与非交互共享 100% 的 App 层，权限系统用 `AutoApproveSession` 适配脚本场景——"同一引擎，两种驾驶模式"。

---

## 九、可学习要点清单（TL;DR）

1. **泛型事件总线 + 服务内嵌**：130 行实现全系统解耦，订阅生命周期绑 context。
2. **统一"部件式"消息模型**：流式、工具、多模态、结束状态全部归一为 `[]ContentPart`。
3. **Agent 循环极简内核**：`流式响应 → 有工具调用就执行并续写历史 → 循环`；并发/取消/权限/成本都挂在循环的安全点上。
4. **统一事件流抹平 LLM 差异**：厂商适配 = 双向消息转换 + 流翻译，泛型模板消重，OpenAI 兼容端点最大化复用。
5. **模型注册表数据化**：价格、上下文窗口、能力位都是数据，逻辑靠数据驱动。
6. **能力即工具集**：多角色 agent 只是同一机制换配置换工具。
7. **子 agent 即工具**：递归委托不需要特殊控制流。
8. **同步阻塞式人在回路**：权限请求对工具作者是一行同步调用，UI 只是事件的消费者。
9. **摘要即检查点**：上下文压缩不删历史，只移动窗口起点。
10. **sqlc + goose + 嵌入迁移**：本地工具持久层的黄金组合。
11. **工具描述是运行时提示词**：把 SOP、禁忌、示例写进 description。
12. **改完即诊断**：LSP 诊断附在编辑工具输出里，形成自我纠错闭环。
13. **TUI 工程化**：泛型事件直接当 `tea.Msg`、模态只拦按键不断事件流、按"消息 ID+宽度"的失效式渲染缓存、主题语义色槽 + 自注册。
14. **手写协议客户端的边界**：传输层/并发模型自己写（~100 行），协议类型用成熟代码生成器；异步推送用"快照+临时 handler+超时"转同步。
15. **不变式下沉数据库**：`updated_at`、计数字段交给触发器，应用层少写一类"记得同步"的代码。
16. **日志即事件流**：slog 输出到自定义 `io.Writer` → 反向解析 logfmt → pubsub 广播 → TUI 交互式日志面板，零双写。
17. **优雅降级链**：外部依赖"能用则用、不能用有兜底、再不行跳过"（补全/权限/配置/LSP 处处如此），单一工具缺失不影响可用性。
18. **确定性派生 ID**：子会话用 `"title-"+父ID`、工具调用 ID 等确定性 ID，天然幂等。

---

## 十、局限与取舍（批判性视角）

学习时也要看到它的代价与不成熟之处（项目自述"early development"）：

| 取舍 | 现状 | 潜在问题 |
|---|---|---|
| 每个流事件都写 DB | 崩溃安全、UI 简单 | 写放大严重；`processEvent` 里有一段被注释的节流代码（1 秒一次）说明作者也意识到了 |
| 工具串行执行 | 循环逐个跑工具调用 | 独立工具调用本可并行（提示词反而鼓励模型并行发起） |
| MCP 每次调用重连 | 实现简单 | 高频调用开销大；工具列表缓存不失效 |
| Broker 丢事件策略 | 非阻塞发布 | 极端情况下 UI 可能漏状态（靠全量刷新兜底） |
| `ProviderMock` panic("not implemented") | 测试基建缺失 | 仓库里几乎没有测试，`_test` 仅 4 个 |
| 权限会话列表无锁追加 | `sessionPermissions` 切片并发写 | 潜在数据竞争（对比之下 pendingRequests 用了 sync.Map） |
| 成本统计覆盖式赋值 | `sess.PromptTokens = ...` 而非累加 | 多轮对话的 token 统计会失真 |
| **图片部件反序列化丢失**（已确认的 bug） | `unmarshallParts` 的 `imageURLType` 分支解析后漏写 `parts = append(parts, part)`（message.go:245） | 含图片的消息从 DB 重新加载后图片静默丢失——**手写标签联合分发时最典型的错误**，可用"注册表 + 泛型"代替大 switch 规避 |
| 拼写错误进入公共 API | 订阅者接口名拼成 `Suscriber` | 发现时已无法不破坏兼容地修复——接口命名要趁早较真 |

这些恰是"如果我来做 v2 该改什么"的现成清单——据作者后来透露，重写版（即今天的 opencode/Crush）确实转向了 TypeScript/服务端架构，印证了 Go 单体 TUI 版在扩展性上的天花板。

---

## 十一、阅读路线建议（按此顺序读源码收益最高）

1. `internal/pubsub/broker.go`（10 分钟）——先懂事件骨架
2. `internal/llm/agent/agent.go` 的 `processGeneration` + `streamAndHandleEvents`（30 分钟）——看懂主循环
3. `internal/message/content.go`（15 分钟）——看懂数据模型
4. `internal/llm/provider/provider.go` + `openai.go`（20 分钟）——看懂适配层
5. `internal/permission/permission.go` + `internal/llm/tools/bash.go`（20 分钟）——看懂安全边界
6. `internal/llm/prompt/coder.go` 与各工具 `Description`（20 分钟）——看懂"提示词工程"如何落地为代码
7. `cmd/root.go` + `internal/app/app.go`（15 分钟）——回头看装配与生命周期
8. 兴趣延伸：`internal/lsp/`（协议客户端）、`internal/tui/`（Bubble Tea 组件化）、`internal/db/`（sqlc 实践）

---

## 附：技术栈速查

| 领域 | 选型 |
|---|---|
| 语言 | Go 1.24 |
| CLI | spf13/cobra |
| TUI | charmbracelet/bubbletea + lipgloss + glamour（markdown 渲染）+ bubbles |
| 数据库 | ncruces/go-sqlite3（WASM 版 SQLite，CGO-free）+ pressly/goose + sqlc |
| LLM SDK | anthropic-sdk-go、openai-go、google.golang.org/genai、aws-sdk-go-v2（Bedrock） |
| MCP | mark3labs/mcp-go |
| 文件监听 | fsnotify；模糊搜索 | lithammer/fuzzysearch |
| Diff | aymanbagabas/go-udiff、sergi/go-diff |
| CI/发布 | GitHub Actions + goreleaser（build.yml 仅做快照构建，发布走 .goreleaser.yml） |
