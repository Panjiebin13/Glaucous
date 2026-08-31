# Glaucous 系统详细设计与实现方案

- 版本：v1.1 终版（2026-08-31）；覆盖 v1.0 原型到 v1.1 全部里程碑的最终形态
- 定位：**深入了解细节**用的设计文档——每个子系统讲清「数据结构 / 关键算法与协议 / 实现路径 / 设计决策」
- 配套阅读：[Glaucous实现详解（v1.1）.md](Glaucous实现详解（v1.1）.md)（三主线叙事版）、[编程智能体概要设计说明书v1.1.md](编程智能体概要设计说明书v1.1.md)（架构决策版）
- 代码规模：约 1.2 万行（src + tests）；38 个测试文件、336 个测试；零 agent 框架

---

## 目录

0. 设计哲学与演进
1. 总体架构与代码布局
2. 启动流程与 REPL 外壳
3. LLM 通道（llm/）
4. 主循环引擎（agent/loop.py）
5. 对话历史与持久化（context/history.py）
6. 上下文工程（budget / compactor / output_limit）
7. 工具系统（tools/）
8. 权限系统（permission/）
9. 模式体系与状态机（agent/state.py）
10. 多 Agent（agent/subagent.py）
11. Checkpoint（checkpoint/）
12. Spec 子系统（spec/）
13. 会话管理（sessions/）
14. 体验扩展（extensions/）
15. 命令系统（commands.py）
16. 交互与渲染（theme / renderer / ThinkingView）
17. 配置系统（config.py）
18. 错误处理全局观
19. 测试体系

---

## 0. 设计哲学与演进

**宗旨：高自主、长任务运行、尽可能减少对人类的干扰。**

三条主线贯穿全部设计：**①长任务跑得动**（Spec 计划 + 任务分解 + 子 agent 隔离 + 上下文工程）；**②需求先说清**（澄清访谈 + 书面契约 + fork 讨论）；**③安全有兜底**（checkpoint + 按「有无兜底」划分的权限矩阵 + 协议合法性保障）。

演进脉络：
- **v1.0（M0~M3）**：原型闭环 → 权限与双模式 → 上下文工程与记忆/规则/技能 → CLI 体验。默认 Plan 模式，强调「可控」；
- **v1.1（M1~M6）**：默认翻转为 Build + auto-approve（高自动化），新增多 Agent、会话管理、Checkpoint、Spec 子系统。主题从「可控」升级为「**既自主又可信**」——自主靠模式翻转与少打扰设计，可信靠兜底设施而非审批数量。

---

## 1. 总体架构与代码布局

```
src/glaucous/
├── cli.py                # REPL 主入口：启动装配、任务轮壳、事件渲染、补全
├── commands.py           # 23 个斜杠命令 + ReplContext 聚合
├── config.py             # 环境变量配置（frozen dataclass）
├── theme.py / ui/        # 主题、卡片渲染、system prompt 构建
├── agent/
│   ├── loop.py           # 主循环：守卫 → 请求 → 派发 → 回喂 → 终止
│   ├── state.py          # 模式状态机（Build/Plan × auto-approve/per-action）
│   └── subagent.py       # 子 AgentLoop 构造与生命周期（M2）
├── llm/
│   ├── client.py         # 流式请求、tool-call 拼装、重试退避、用量透传
│   └── registry.py       # models.toml 模型注册表与连通性校验
├── context/
│   ├── history.py        # JSONL 持久化、视图变换、锚截断
│   ├── budget.py         # token 估算与三档水位
│   └── compactor.py      # L1 本地裁剪 + L2 模型压缩
├── tools/                # 14 个工具 + base（注册/派发/熔断）
├── permission/           # risk / workspace 沙箱 / classifier 分类器 / approval 管线
├── checkpoint/           # Git 快照 + 保留策略 + 回退编排（M4）
├── spec/                 # Spec 状态机 + 模板 + 全流程编排（M5）
├── sessions/             # 用户级会话存储 + 侧边索引 + 统计（M3）
├── extensions/           # 规则 / 事实记忆 / 技能 / init 草稿
└── safety/output_limit.py# L0 输出截断落盘
```

**依赖方向**（严格单向，无环）：`cli → commands → agent/spec → tools → permission → context → llm`；`checkpoint` 只依赖 `permission.approval.AuditLog` 与 git 子进程；`sessions` 独立。循环引用点（如 `spec.pipeline → cli.run_managed_turn`）一律**函数内延迟导入**。

**两个核心不变量**（全局生效）：
1. **父上下文隔离**：子 agent 的中间过程永不进主历史，回传只有 ≤1000 字报告（M2）；
2. **协议合法性优先**：任何异常路径都不允许留下悬空 `tool_call_id`（否则整个会话被 API 400 永久报废）。

---

## 2. 启动流程与 REPL 外壳

### 2.1 启动序列（`cli.repl()`）

```
main(argv) → 解析参数（--workspace/--resume）→ 加载配置（环境变量）
→ repl(workspace, resume_id)：
  1. 配置与模型：Config.from_env → LLMProfile（缺 key 启动即报错）
  2. 扩展装配：load_rules → MemoryStore → SkillRegistry.scan（三层：内置→全局→项目）
  3. system prompt：build_system_prompt（基础提示 + 规则 + 记忆注入 + 技能索引）
  4. 会话索引装配：SessionIndex（写入失败经 on_error 告警）→ 旧会话迁移（幂等）
     → load 判损则重建（扫描全部 project-hash 目录派生条目）
  5. 会话创建/恢复：--resume 走 resume_history（前缀模糊匹配）；
     否则 create_session_history（用户级唯一入口，失败降级回工作区旧路径）
  6. checkpoint 存储：CheckpointStore（惰性探测 Git，非 Git 降级为不可用）
  7. ReplContext 聚合装配 → rebuild_loop（见 §16.3）→ 渲染 banner
  8. prompt_toolkit PromptSession（降级三条件：GLAUCOUS_INPUT=plain / 非 TTY / 构造失败）
  9. 进入输入循环
```

### 2.2 任务轮壳（受管轮，`repl` 主循环 + `run_managed_turn`）

每个用户任务轮的标准时序（Spec 流程内的轮次经 `run_managed_turn` 复刻同一时序）：

```
begin_turn(ctx)                      # 清 turn_usage、正文段缓冲（不动会话缓冲）
thinking.start_turn(); thinking.start()  # 思考区计数清零、动态区激活
ctx.stream_state["printed"] = False
ctx.turn_active = True               # 切换保护（/sessions 等命令被挡）
ctx.turn_checkpoint_seq = None       # 本轮入口哨兵清零
answer = await ctx.loop.run(task)    # 主循环（§4）
finally:
  turn_active 复位、哨兵清理
  session_usage += turn_usage        # 会话级 token 累计
  session_index.touch(...)           # 索引刷新（名称/条数/token，尽力而为）
  thinking.close(turn_usage)         # 收缩为一行「💭 思考过程（N 步）— /expand 查看」
  终答缓冲 → 🕊 Markdown 卡片        # 正文段不落账（只留摘要行），/expand 可回看
  用量行打印
```

**异常分支**：`KeyboardInterrupt/CancelledError` → 中断本轮继续会话；其他异常 → 顶层兜底打印 ✘，单轮失败不退出会话。轮末收缩与用量行在 `finally` 中，异常路径同样执行。

---

## 3. LLM 通道（llm/）

### 3.1 LLMClient（client.py）

**职责边界**：只做「传输 + 拼装」，不做任何业务决策。`openai.AsyncOpenAI` 仅作 HTTP 客户端（允许项），重试/拼装/容错全部自研。

**请求流程**（`chat()`）：
```
for attempt in range(MAX_RETRIES + 1):        # 最多 4 次重试
    try: return await _chat_once(...)
    except: 4xx → 直接抛（鉴权/参数错重试无意义）
            429/5xx/网络 → 指数退避 1/2/4/8s + 0~1s 随机抖动（防请求风暴）
                          → on_retry 钩子通知界面「↻ 重试中」
```

**流式处理**（`_chat_once()`）：
- SSE delta 逐片消费：`delta.content` 直出 `on_text`（流式正文）；
- **tool_calls 分桶拼装**：`delta.tool_calls` 按 `piece.index` 分桶累积 `id/name/arguments` 字符串碎片，流结束后逐桶 `json.loads(arguments)` 反序列化为 `ToolCall`——SDK 只给碎片，拼装与解析容错是自研关键逻辑；
- **网关兼容降级**：`stream_options`（usage 统计）不被网关支持时，去参原样重试一次；
- **用量透传**：尾部 usage chunk 经 `on_usage` 归一化发射（prompt/completion/cache 命中），不参与重试决策。

### 3.2 模型注册表（registry.py）

`~/.glaucous/models.toml` 声明多个模型档案（name/base_url/model/temperature…）；`/model` 切换执行**三步校验**：解析档案 → `ping`（连通性 + 鉴权 + 模型存在）→ 通过才热替换（`LLMClient.set_profile` 只改后续请求路由，会话历史无缝延续）。仓库内仅存 `assets/models.toml.example` 模板（凭据不入库）。

---

## 4. 主循环引擎（agent/loop.py）

### 4.1 循环骨架

```
run(task):
  registry.reset_parse_counter()            # 熔断计数限定单任务内
  [M4] checkpoint：每轮入口快照（push_user 前取锚与计数，失败不阻断，
        首次失败经 note 事件一次性告警；成功经 on_checkpoint 外泄 seq）
  history.push_user(task)
  while True:
    ① steps >= max_steps(50) → 终止「步数上限」
    ② _enforce_budget() → 预算管线（§6），可返回终止诊断
    mode_snapshot = state.mode              # 模式快照：声明层与执行层同轮一致
    msg = llm.chat(history.view(), tools=registry.tool_schemas(mode_snapshot))
    if not msg.tool_calls:                  # ③ 自然终止：终答入史返回
        push_assistant(msg); return msg.text
    push_assistant(msg)                     # 先入史（tool 消息配对前提，协议硬约束）
    for call in msg.tool_calls:             # 逐个派发
        result = registry.dispatch(call, mode_snapshot)
        result = L0 截断（§6.4）
        push_tool(call, result)
    if state.mode != mode_snapshot: emit mode_changed
```

**四类终止条件**：①自然终止（无 tool_calls）②步数上限 50 ③解析熔断（连续 3 次参数解析失败）④预算耗尽（压缩后仍 ≥100%）。非自然终止统一经 `_terminate()`：`diagnostic` 事件必达交付诊断文本（多步轮中途有流式输出时，不能依赖返回值推断诊断是否已呈现）。

**守卫前置**：终止条件评估固定在**每次请求 LLM 之前**——无论循环从哪条路径回来都会重新评估，无漏网路径。

### 4.2 悬空调用善后（协议合法性核心）

OpenAI 协议要求 `assistant.tool_calls` 中每个 call 必须有配对 tool 消息。一旦悬空，共享历史的后续每轮请求都被 API 400 拒绝，会话**静默报废**。实现：

- 派发循环维护 `dispatched` 集合；`ParseCircuitBroken` 捕获后为悬空 call 补推 `ok=False` 的 ToolMessage 再终止；
- **任何** `BaseException`（含 KeyboardInterrupt）路径都先 `_salvage_dangling_calls` 善后再上抛。

---

## 5. 对话历史与持久化（context/history.py）

### 5.1 存储结构

JSONL 逐条追加：首行 `session_meta`（type/session_id/created_at/workspace），之后每条消息一行。进程崩溃最多丢最后一条；`--resume` 与 `/resume` 从文件重建（损坏行跳过并告警）。

消息内部带 `_` 前缀内部键（`_meta` 记账五字段 / `_trimmed` L1 幂等标记 / `_anchor` 方案锚标记）——仅供上下文管理组件原位读写。

### 5.2 视图变换（view()）

发给 API 的序列 = system + 全部历史，经**纯函数变换**（不改内部状态、幂等）：
1. 剥除 `_` 前缀内部键（有内部键的 entry 浅拷贝后过滤）；
2. **方案锚替换**：`submit_plan` 调用的 `arguments.plan` 全文确定性替换为锚文本「方案全文已存档至 .glaucous/plans/，可调用 read_plan 回读」——方案全文不常驻上下文但随时可回读（JSONL 保留原文）。

### 5.3 上下文回退（truncate_to，M4）

`/rollback`「同时回退上下文」时截断到前 count 条：**锚配对校验**——`messages[count-1]` 的 sha256[:12] 摘要必须与 checkpoint 记录的 `anchor_digest` 一致（L2 压缩会在轮间原位替换历史，截断点可能已漂移），不符抛 `ContextAnchorMismatch` → 调用方降级为「仅回退文件」，绝不静默错位截断。空历史首轮用空串哨兵（截断到 0 条 = 清空对话，真实截断而非 no-op）。先写文件后改内存，失败两态一致。

---

## 6. 上下文工程（四级预算）

长任务的命脉。四级防线，成本与侵入性递增：

| 级 | 触发 | 机制 | 成本 | 信息保全 |
|---|---|---|---|---|
| **L0 输出截断** | 单条工具输出 >300 行或 50KB | 截断入史 + 完整落盘 `.glaucous/outputs/<call_id>.log`；`read_output` 工具按段回取 | 零 | 完整（落盘） |
| **L1 本地裁剪** | 占用 >70%（warn） | 保留最近 2 轮，更早的 tool 结果正文**原位替换**为派生摘要（由 `_meta` 记账拼「首行+行数」）；`_trimmed` 标记幂等；`_anchor` 行保留原文 | 零模型开销 | 摘要级 |
| **L2 模型压缩** | >85%（critical）且 L1 不足 | 调模型把早期轮次压缩为「【会话阶段摘要·系统压缩生成】」 | 一次模型调用 | 语义级 |
| **预算终止** | 压缩后仍 ≥100% | 优雅终止：诊断「可 /exit 后 --resume 继续」 | — | 会话文件保全 |

**实现要点**：
- **预算评估**（budget.py）：token 估算 = ASCII/4 + CJK/1.5（三档范围：统一表意文字/中文标点/全角），逐条 JSON 序列化求和（含键名开销，保守偏高可接受）；阈值常量 `WARN_RATIO=0.70 / CRITICAL_RATIO=0.85` 单一出口——「用户看到的与系统执行的一致」；`estimate_tokens` 是唯一估算出口，可无损替换为精确 tokenizer；
- **防压缩循环**：L2 连续失败 2 次（`MAX_L2_FAILURES`）且仍 critical → 终止（否则每轮空转压缩调用）；失败降级为「加深 L1」（保留轮数递减）；占用回落即清零失败计数；
- **执行时记账、裁剪时派生**：工具执行时 `_meta` 记五字段（行数/状态等），L1 裁剪不调模型，直接由 `_meta` 派生摘要行。

---

## 7. 工具系统（tools/）

### 7.1 Tool 协议（base.py）

每个工具是一个类，**声明即代码**：

```python
class Tool:
    name: str                 # 模型可见名
    description: str          # 用法说明（模型决策依据）
    parameters: dict          # JSON Schema（原生 tool calling 声明）
    risk: Risk                # SAFE/WRITE/DANGEROUS 静态风险级
    modes: frozenset[str]     # 可用模式集（声明层过滤）
    def build_approval(args, mode) -> ApprovalAction | None  # 参数级动态审批（可选覆盖）
    async def execute(**args) -> ToolResult
```

### 7.2 dispatch 管线

```
dispatch(call, mode_snapshot):
  ① 模式校验：工具不在当前模式可用集 → 拒绝回喂（声明层+执行层双保险）
  ② 权限管线：tool.build_approval(args) 构造审批动作 → pipeline.gate
     （拦截 → ok=False 回喂，不计入熔断计数）
  ③ 参数解析：json.loads(arguments)；失败计数，连续 3 次 → ParseCircuitBroken
  ④ 参数校验：schema required 检查
  ⑤ execute 本地执行（异常 → ok=False 回喂，模型自行调整）
  ⑥ 记账：_meta 五字段写入 tool 消息
```

### 7.3 十四个工具一览

| 工具 | 类别 | 要点 |
|---|---|---|
| read_file / list_dir | 只读 | 区内免审；区外读 WRITE 可豁免；行号标注输出 |
| grep | 只读 | 工作区全文搜索；区外路径走审批 |
| write_file / edit_file | 写 | 携带 unified diff 审批；git 兜底区免审（§8.4）；`.glaucous/` 受保护硬拦（`.glaucous/skills/` 除外，写入后刷新技能索引即时生效）；父目录自动创建 |
| bash | 执行 | asyncio 子进程、cwd=工作区、超时钳制（默认 120 最大 600 秒，模型传超大值钳制而非拒绝）、超时 kill 并收尸取部分输出、Ctrl+C 同样 kill 不留僵尸、输出 300 行防爆；风险经分类器动态定级（§8.3） |
| ask_user | 交互 | 提问 + 候选选项（≤6），箭头选择/数字回退；挂起等待用户回答；回答入史 |
| submit_plan | 交互 | 高风险主动确认通道（二选：批准/修改意见）；方案全文落盘 `.glaucous/plans/` + 视图锚替换；PLAN 下批准 = 回 Build 的唯一工具面出口 |
| read_plan | 只读 | 方案锚的回取通道（缺省读最新） |
| memory_save / memory_load | 扩展 | 事实记忆双作用域存取 |
| load_skill | 扩展 | 技能正文惰性加载 |
| read_output | 只读 | L0 截断落盘的分段回取 |
| spawn_agent | 多 Agent | 子任务派发（仅主 agent 注册；§10） |
| read_spec | 只读 | Spec 锚回读（缺省读最新活跃；§12） |

---

## 8. 权限系统（permission/）

### 8.1 三级风险 × 两种策略

`Risk = SAFE | WRITE | DANGEROUS`；`SessionState` 持 `mode`（build/plan）× `approval_policy`（auto-approve/per-action）× `approved_types`（同类型豁免集）。

**gate 决策流**（approval.py）：
```
gate(action):
  守卫：risk == DANGEROUS → 永远单独弹卡（不可被 auto-approve/同类型豁免越过）
  auto-approve 且非守卫 → 静默放行（审计留痕）
  per-action：approved_types 命中 → 放行；否则弹卡三选（+「拒绝并回退」，§11.4）
    approve → 放行；
    approve_type → 记入豁免集（DANGEROUS 本次放行但不记豁免，防批量放行危险操作）；
    reject / reject_rollback → 拒绝，审计 + 回喂「用户拒绝：理由」
```

**审计**：所有权限决策写 `.glaucous/audit.log`（JSON Lines：time/event/decision/risk/allowed/reason/agent 归属）；写入失败不阻断主流程。

### 8.2 工作区沙箱（workspace.py）

- `resolve()`：相对路径拼工作区根，统一 `resolve(strict=False)` 规范化；
- `check()`：realpath + 前缀校验，防 `../` 穿越与符号链接逃逸；
- `classify_path()`：区内/只读白名单 = SAFE；区外 = WRITE（可审批不硬拒）；无法解析 = DANGEROUS；
- `is_protected()`：`.glaucous/` 下除 `skills/`（技能资产，开放写）外一律保护——审计/会话/记忆/方案锚不可被 agent 篡改；
- **`git_backed` 标志**（v1.1 决策）：装配时探测 `checkpoint_store.available` 注入，驱动 §8.4 放宽。

### 8.3 命令分类器（classifier.py）

bash 命令动态定级，层层递进：

1. **拆分**：复合命令按 `;`/`&&` 拆段、管道按 `|` 拆段（均引号感知：引号内分隔符不拆），**逐段独立定级取最坏**——防 `echo a; rm -rf /` 被白名单首词遮蔽；
2. **整串管道模式**：`curl|sh`/`wget|sh` 先于逐段检测（引号内字面量不误报：先剥引号内容再匹配）；
3. **首词危险表**：`sudo`/`dd`/`mkfs`/`chmod`… 无条件 DANGEROUS；
4. **rm 细分**：危险模式表（`-rf /`、`-rf ~`、` -rf .` 等）命中 → 扫描目标路径，**全在区内且 git 兜底 → 降级 WRITE**，否则 DANGEROUS；普通区内删除 → WRITE；
5. **mv**：目标/源区外或受保护 → DANGEROUS；区内 → WRITE；
6. **git 细分**：只读子命令（status/diff/log…）SAFE；危险模式 `push --force` 恒 DANGEROUS（远端无兜底），`reset --hard`/`clean -f`/`checkout -- .` 在 git 兜底区降 WRITE（工作树可回退）；其余写操作 WRITE；
7. **白名单命令写变体**：重定向（扫描全部目标取最坏，`2>/dev/null` 丢弃输出豁免）、`sed -i`/`find -delete` → 按写定级；
8. **路径参数判定**（`_path_risk`）：剥引号后 resolve（防 `'/etc/passwd'` 拼到工作区根误判）；`~` 展开按区外；受保护目录写 → DANGEROUS、读 → SAFE；
9. **保守升级**：无法判定的命令 → WRITE 走审批（宁多问不漏放）。

### 8.4 权限矩阵（当前形态，2026-08-31 决策）

**核心判据：有无兜底**，而非操作类型吓人程度。

| 操作 | Git 工作区 | 非 Git 工作区 | 理由 |
|---|---|---|---|
| 区内读（含 .glaucous/） | 放行 | 放行 | 读无完整性风险 |
| 区内文件写 | **免审** | WRITE 审批 | checkpoint 可回退 |
| 区内危险命令（目标区内） | **WRITE 可豁免** | DANGEROUS | 可回退，保留一次确认 |
| `.glaucous/` 写 | DANGEROUS 恒拦 | 同左 | 快照排除 + 审计底线 |
| 区外读 | WRITE 可豁免 | 同左 | 环境探测真实需求，但要可见 |
| 区外写 | DANGEROUS 不可豁免 | 同左 | 无回退面 |
| `git push --force`/`sudo`/`curl\|sh` | DANGEROUS 恒拦 | 同左 | 远端/系统侧无兜底 |

**拒绝联动回退**（FR-43）：审批卡第四选项「拒绝并回退」——主 agent 且本轮入口 checkpoint 就位时可选，立即回退文件到本轮任务前 + 拒绝回喂（§11.4）。

---

## 9. 模式体系与状态机（agent/state.py）

**两个维度**：`mode`（build/plan）× `approval_policy`（auto-approve/per-action），存于 `SessionState`（切换类命令后重置为启动默认）。

- **默认 Build + auto-approve**（v1.1 翻转）：启动即干活；高自动化的安全由兜底设施承担而非审批数量；
- **Plan = 只读研究**：`/plan` 显式进入，写工具声明层不发给模型，执行层二次拦截（双保险）；产出分析建议不动手；`/build` 或批准方案回 Build；
- **submit_plan 二选协议**（FR-38）：高风险任务的主动确认通道（批准/修改意见）；PLAN 下批准 = 回 Build 的唯一工具面出口（FR-39）；BUILD 下批准不触碰状态；
- **模式快照**：每轮请求前取 `state.mode` 快照，声明层（工具集过滤）与执行层（派发校验）同轮一致——消除「轮中切换后同轮后续调用按新模式放行」的窗口期；轮末比对快照与现态，不一致发 `mode_changed` 事件；
- **先澄清后开发**（FR-37）：system prompt 软约束，需求含糊先 ask_user；
- **授权策略仅经 `/build` 显式改变**（审批卡不附带策略切换，收敛决策面）。

---

## 10. 多 Agent（agent/subagent.py，M2）

### 10.1 spawn_agent 契约（FR-60）

参数：`task`（必填）+ `context`（可选）；SAFE 风险、全模式可用、**仅主 agent 注册**。

### 10.2 隔离实现（FR-61/64）

`SubagentRunner.run(task, context)` 构造完全独立的子执行环境：
- **独立 History**：落盘 `.glaucous/agents/<时间戳>.jsonl`（不进会话索引）；
- **独立 system prompt**：角色 + 子任务 + 工作区 + 项目规则；不注入记忆/技能索引（评审员需要的是任务上下文，不是主 agent 的全部包粘）；
- **独立工具集**：`build_sub_registry` = 父注册表全集 − `spawn_agent`（**防嵌套**：声明层不可见 + 执行层「工具不存在」双保险）；工具实例共享（无 per-agent 状态，解析熔断计数在 registry 层天然隔离）；
- **SessionState 快照复制**：继承父当前模式/策略/豁免集，子内状态变化不回流父；
- **复用父 LLMClient**：usage 计入父轮统计面；
- **串行**：父派发后 await 到返回才继续；执行期经 `ctx.active_state/active_agent/active_task` 哨兵切换归属（审批卡标注「🕊 子 agent（任务：…）」，finally 恢复），归标识 `child-N`（类级自增，进程内唯一）。

### 10.3 报告回传纪律（FR-63）

- 子终答拼装为四段式报告（任务结果摘要/修改文件清单/验证结果/风险与遗留），修改文件清单由子会话事件采集（write/edit 的 tool_end）；
- **入史上限 1000 字**：超限截断 + 完整报告落盘 `outputs/spawn_agent-child-N.log`，尾注附真实 `read_output(call_id)` 回取提示（外置型，对齐 L0 机制；防模型猜 call_id 落空）；
- 父史只增 2 条（assistant 调用 + tool 报告）——父上下文零污染。

### 10.4 消费方（M5）

Spec 评审/代码评审/验收核验三种子任务经 `ctx.subagent_runner` 直调（不经 spawn_agent 工具）；`runner` 挂账在 `ReplContext`（rebuild_loop 写入，/clear、/resume 重建后仍有效）。评审报告机器可读契约：首行「评审结论：通过/不通过」+【阻塞级】【建议级】两节；解析失败保守判不通过。

---

## 11. Checkpoint（checkpoint/，M4）

### 11.1 快照机制（git_snapshots.py）

**为什么不用 `git stash create`**：它捕获不了 untracked 新增文件，「新增文件移除」回退验收点会落空。改用**临时索引五步法**（对概设方案的显式修正）：
```
GIT_INDEX_FILE=<临时索引> git read-tree <HEAD 或空树>
→ git add -A -- .
→ git rm -q --cached -r -- ':(glob)**/.glaucous/**'（任意层级排除，两条 glob 拆独立调用，无匹配容忍）
→ git write-tree → git commit-tree <tree> -p <HEAD>
→ git update-ref refs/glaucous/checkpoints/<seq> <commit>
```
特性：不碰用户工作树与真实索引、不污染 stash 列表；对象有 ref 引用不被 gc；全命令注入 `core.quotepath=off`（非 ASCII 文件名不被八进制转义）；子进程超时/非零码 → `GitError`。

### 11.2 存储与编排（store.py）

- **索引**：`.glaucous/checkpoints.json`（version/seq/checkpoints[]），原子写（tmp+replace）；损坏/缺失 → 空索引起步；seq 错型归一化防创建永久失败；
- **保留淘汰**：`max_keep`（默认 50，`GLAUCOUS_CHECKPOINT_MAX_KEEP`），超出删最旧 ref + 清索引行；
- **创建时机**：主循环每轮入口（loop.run 注入，失败不阻断 + 一次性告警）；Spec 执行期每任务前额外打权威任务级快照；
- **回退编排**（`rollback`）：`diff_against` 产 M/D/A 三态清单（A 项 = `ls-files` ∪ untracked − 快照树，解决 diff 产不出新增文件的问题）→ `restore_from` 还原 M/D → 逐个 unlink A 项（失败容忍并标记）→ 审计；
- **可用性**：惰性探测一次即缓存；非 Git 工作区 → `available=False` + 原因（权限放宽与快照功能同依据）。

### 11.3 /rollback（FR-42）

箭头选择历史快照 → 变更清单确认卡（M/D/A 计数 + 路径预览）→ 文件还原；随后可选「同时回退对话上下文」：`History.truncate_to` 锚校验（不匹配 → 仅回退文件并提示）。回退完成后向对话写入「[系统] 回退记录」，模型可感知已回退。

### 11.4 拒绝联动回退（FR-43）

审批卡第四选项，仅主 agent 且 `ctx.turn_checkpoint_seq` 就位时呈现；回调侧立即执行文件回退（回退失败降级为普通拒绝并提示），gate 将 `reject_rollback` 映射为拒绝分支（审计 + 回喂「用户拒绝并已回退」）。

---

## 12. Spec 子系统（spec/，M5）

### 12.1 文档与状态机（store.py）

落盘 `.glaucous/specs/<spec-时间戳>.md`：frontmatter（id/name/status/created_at/approved_at/round/mode/entry_checkpoint）+ 七节正文（需求与边界/澄清记录/约束/设计/任务清单/验收标准/风险与回退）。
```
draft → reviewing → approved → executing → code_review → verified
                        │            │            │
                        └───(修订回环 ≤3 轮，仅 round 自增)───┘ → archived（任何阶段可归档）
```
`transition` 强校验非法迁移；原子写；任务勾选 `check_task` 写回 checkbox（状态即文档，中断续跑依据）。

### 12.2 全流程编排（pipeline.py）

命令式流水线（非模型自主驱动）：轮次上限、升级点、交互门全部确定性代码，可 mock 回放：
```
澄清访谈（≤3 轮，主 loop 轮 + ask_user，确定性门）
→ 起草（终答=正文；_clean_body 净化：裁开场白 + 剔元话语尾段；缺节补写一轮）
→ 评审循环（≤3 轮）：子评审（报告契约 + 截断回读）→ 不通过：修订（深度介入每轮收用户建议）
→ 批准：全自动且正常轮次通过（无阻塞）→ 免批准直执行；深度介入/升级路径 → 批准卡（反馈修订 ≤3 轮）
→ 执行：入口基线快照 → 逐任务（权威任务级快照 → 受管任务轮（三段式消息）→ 勾选写回；失败三选：重试/跳过/归档中止）
→ 代码评审循环（≤3 轮）：子评审（验收标准 + 自基线的 diff 摘要）→ 修复 → 复审；升级「再修复一轮」后追加复审
→ 验收核验：子核验逐条 ✓/✗（保守口径：全 ✓ 且无 ✗ 且 ✓ 数 ≥ 标准数 → verified，否则 archived 附未决）
→ 终局总结轮（主 agent 要点式汇报，受管轮壳以 md 卡片呈现）
```
**受管任务轮壳**（`cli.run_managed_turn`）：pipeline 直调 `ctx.loop.run` 会绕过 repl 轮壳，壳层复刻同款时序（思考区逐轮收缩 + 终答缓冲一次性 md 卡片 + 用量行）。
**中断与恢复**：顶层捕获后状态停驻（frontmatter 已落盘）；`/spec` 无参且存在 executing → 续跑（pending 从未勾选任务重新解析）。
**思考区间隙段**：子评审区间段经 `thinking_enter/exit` 自有生命周期（独立计数 + 段末收缩）；`close()` 后复位全部内部状态、`resume()` 未激活不重绘——防旧计数跨段累积与正文尾泄漏。
**命令面**：`/spec [需求|status|cancel]`、`/specs`；`read_spec` 工具常备（主注册一次，子 registry 派生继承）。
**BASE_PROMPT 主动建议**：大任务可建议用户以 /spec 发起（提示词层，无代码触发判定）。

---

## 13. 会话管理（sessions/，M3）

### 13.1 存储布局（FR-44）

```
~/.glaucous/
├── session_index.json          # 侧边索引（派生缓存，可重建）
└── sessions/<project-hash>/    # sha1(工作区绝对路径)[:12]
    └── <时间戳>-<随机>.jsonl    # 会话文件（真相源）
```
旧会话（工作区内 `.glaucous/sessions/`）启动时一次性迁移（幂等）；`.glaucous/agents/` 子会话不迁移不索引。

### 13.2 侧边索引（FR-45/46）

结构 `{"version":1,"projects":{<hash>:{"workspace":…,"sessions":[{id,name,created_at,updated_at,message_count,token_used,status}]}}}`。写入时机：轮末 touch/自动命名//rename//fork/迁移；原子写，失败经 `on_error` 告警（尽力而为 ≠ 静默）。**损坏/缺失 → 自动重建**：扫描全部 project-hash 目录 JSONL 派生条目（名称取首条 user 消息前 20 字；`/skill` 包装行跳过）；重建后手动命名与 token 累计归零（派生字段已知边界）。名称全局唯一化：同名冲突追加 id 尾段（写入时 `_dedupe_name`），列表名可直接精确切换。

### 13.3 命令面（FR-47~50）

- `/sessions [kw|id|a]`：列表/搜索/切换；消解链五级（id 精确 → id 前缀唯一 → 名称精确唯一 → 多命中候选 → 子串搜索仅展示）；**切换保护**（`turn_active` 置位期间拒绝）；跨项目切换需目标工作区存在；
- `/rename <name>`、`/fork [name]`（另存为语义：复制 JSONL + meta session_id 替换 + 索引双条目 + 当前 REPL 切新会话；六处 IO 兜底）、`/stats`（会话统计 + 全局审批决策分布，审计双格式过滤）。
- `session_usage` 生命周期：/clear 重置、/fork 继承、切换从索引恢复；轮末累加并回写索引。

---

## 14. 体验扩展（extensions/）

| 模块 | 机制 |
|---|---|
| **规则**（rules） | `glaucous.md` 项目规则 + `~/.glaucous/rules.md` 全局规则，启动注入 system prompt；`/rules` 查看；/init 生成项目规则草稿（扫描工作区 → 模型起草 → 确认后写入，拒覆盖） |
| **记忆**（memory） | 事实记忆双作用域（全局 `~/.glaucous/memory.json` + 项目 `<工作区>/.glaucous/memory.json`）；`memory_save/load` 工具 + `/memory add\|del`；启动按最近使用加权取 Top-N 注入；写入原子化，损坏容错为空表重建（宁丢不崩） |
| **技能**（skills） | 三层扫描（包内置 → 全局 → 项目，同名后覆盖）；启动只注入索引（`- name: description`）；两段式惰性加载：模型判断相关 → `load_skill` 取正文（正文入史，会话内有效）；`/skill` 手动调用（组装任务立即执行一轮）；`.glaucous/skills/` 写入后刷新索引即时生效；frontmatter 畸形跳过并记告警 |
| **init_draft** | 扫描工作区（语言/框架/测试/依赖）→ 模型起草规则草稿 |

---

## 15. 命令系统（commands.py）

`ReplContext` 是 REPL 可变状态的唯一聚合（dataclass）：workspace/config/llm/history/state/loop/audit/renderer/pipeline + 会话管理三字段 + checkpoint 两字段 + 子 agent 归属三哨兵 + thinking 等。命令层与回调层一律经它间接引用（**闭包不捕获旧对象**，/clear、/resume 整体替换后仍正确）。
23 个命令单一数据源 `COMMAND_META`（/help 与补全共用）；分派：`handle_command` 返回 `True`（继续）/`"exit"`；未识别命令打 /help 可用列表（不发给 LLM）。交互类命令遵守 `live_hooks` pause/resume 协议（阻塞交互前后暂停/恢复思考区）。
补全：命令段前缀匹配（meta 显示）；参数段注册表（/view 路径、/model 模型名、/build 策略、/skill 技能名、/sessions 会话名、/spec 子命令）。
非 TTY 全链路降级：箭头选择 → 数字输入；思考区 → 直打；卡片 → 纯文本。

---

## 16. 交互与渲染（theme / renderer / ThinkingView）

### 16.1 思考区动态折叠（ThinkingView）

自管 ANSI 擦除重绘协议（取代 rich.live.Live——后者在「自适应大窗口 + 中途 pause/resume + console 直打交叉」下重绘崩坏）：
- 每次事件后光标上移擦除旧块重绘（与 select_with_arrows 同款协议）；块高 = 「⚙ 思考中 · N 步」头 + 最近事件行（窗口随终端高度自适应，下限 8 上限 60）+ 正文增量尾部两行（流式观感）；
- **N 口径**：非 text 事件 + 交互伪事件 + 正文段落账条目（不含增量）；`note_step` 为交互计数不占行；
- `pause/resume`：阻塞交互前擦除让位（交互卡打在原位），返回后重绘；未激活不重绘（R5）；
- `close`：擦除动态区，原地留一行摘要「💭 思考过程（N 步 · ↑↓ tokens）— /expand 查看」；随后复位全部内部状态；
- 打印/擦除失败 → 置 `_paused` 降级直打，不阻断会话；
- `was_active` 是终答呈现路径判据（轮末先取判据再 close）。

### 16.2 事件契约与卡片体系（LoopEvent）

loop → CLI 事件：`text`（流式正文）/`tool_start`/`tool_end`/`diagnostic`（终止诊断必达）/`mode_changed`/`budget`/`compressed`/`note`/`sub_start`/`sub_event`/`sub_end`。交互伪事件（ask/decision/plan_decision）落会话缓冲供 /expand 回看。卡片体系：审批卡（风险分级 + 归属标注）、提问卡、方案确认卡、评审报告卡、批准卡、验收卡、会话列表卡、终答 🕊 Markdown 卡——「人介入时刻才升格卡片」，过程性输出一律紧凑行。
`/expand` 回放会话缓冲（思考过程可回看）；`/collapse` 逆操作。
主题：天青夏日色板（glaucous.* 样式族），Windows legacy conhost 降级防崩。

---

## 17. 配置系统（config.py）

frozen dataclass，只从环境变量加载（凭据不入库）：`GLAUCOUS_API_KEY`（必填）/`BASE_URL`/`MODEL`/`TEMPERATURE`/`MAX_STEPS`（50）/`CONTEXT_LIMIT`（128000，`/context` 运行时可调 128K/512K/1M）/`MEMORY_TOP_N`（50）/`CHECKPOINT_MAX_KEEP`（50）/`READ_ONLY_EXTRA`。模型配置在用户级 `models.toml`（注册表独立于代码仓，模板随包分发）。切换上下文档位以 `dataclasses.replace` 生成新 Config + rebuild_loop 生效（frozen 不可原地改）。

---

## 18. 错误处理全局观（分层哲学）

按「损害不可逆性」分层，越不可逆越严格：
1. **协议合法性**（最高优先）：悬空 tool_call 强制善后——不做则整个会话报废；
2. **会话存续**：单轮异常不退出会话（REPL 顶层兜底）；中断（Ctrl+C）杀子进程、清哨兵后继续；
3. **轮内恢复**：工具失败 → ok=False 回喂（错误即控制信号，模型自纠）；网络错误退避重试；解析失败 3 次熔断；
4. **设施尽力而为**：索引写入/审计/快照/总结轮失败 → 一次性告警不阻断主流程；
5. **降级而非拒绝**：非 TTY 降级、非 Git 降级、索引损坏重建、存储不可写降级回旧路径——能力收敛但会话总能继续。
诊断必达：终止诊断经事件通道交付；审计全量留痕（权限决策/回退/检查点）。

---

## 19. 测试体系

**38 个测试文件、336 passed**（WSL；基线守恒制：每里程碑新增用例且既有全绿）。
关键手法：
- **脚本化 mock LLM**：按队列返回 AssistantMessage（含 tool_calls），驱动全链路回放（状态机/双评审循环/四类轮次耗尽/任务执行联动）；
- **PipelineHooks 依赖注入**：Spec 编排的 run_turn/run_review/ask/checkpoint 四个接缝全部可替换，确定性验证轮次计数与升级分支；
- **真实环境单测**：checkpoint 用例在 tmp 内 git init + 真实快照/回退/淘汰（含 CJK 文件名、A 项移除失败、子目录工作区）；
- **用户级路径隔离教训**（真实事故修复）：重定向 `~/.glaucous` 的 fixture 必须 autouse 且 `index_path/sessions_root` 经**模块属性**打补丁（from-import 直接绑定会绕过 monkeypatch 污染真实用户目录）；
- **协议合法性专项**：悬空调用善后、序列合法性断言。
测试覆盖维度对照概设 §11 七个增补文件全部落实；开发方法为 spec 驱动交付（先约束文档 → 评审子代理多轮评审 → 编码 → 代码评审循环，40+ 份评审报告留痕）。

