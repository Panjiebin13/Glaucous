# Glaucous M3 Day5 体验与扩展 - 技术设计方案

> 创建日期：2026-08-31
> 关联规格：[编程智能体需求文档.md](../编程智能体需求文档.md)（FR-21/23/26/27/28/30/31/33/34）、[Glaucous开发计划表.md](../Glaucous开发计划表.md)（Day 5/M3 任务 3.1~3.7）、[编程智能体概要设计说明书.md](../编程智能体概要设计说明书.md)（§3 技术选型、§6 LLM 网关与模型注册、§7.2/§7.3 记忆与 skill、§8 CLI 交互与视觉设计、§9 配置与安全、§10 工程结构）、[Glaucous天青夏日主题设计.md](../Glaucous天青夏日主题设计.md)（色板与界面元素基准）
> 关联前案：[Day 4 Plan](202608301000-plan-m2-day4-memory-context.md)（已实施，为本次改造基线）
> 状态：已批准（经 2 轮评审：r1 阻塞 1 项已修复，r2 有条件通过阻塞归零；S6~S8 表述同步项已当场闭环）

## 0. 本轮范围裁剪声明

沿用 Day3/Day4 约束，本轮**只进行代码开发**：

- **不产出新增测试**：M3 新增模块的单测（概设 §10 已预留 `test_model_registry.py`、`test_skill_lazyload.py` 等）全部登记 §9 测试债务，M4 偿还；
  **既有用例回归属基线保护，允许并要求执行**（`python -m pytest tests/ -q` 全绿）——本轮重构 `cli.py` 的渲染与输入路径，回归是防止 Day1~Day4 基线被静默破坏的唯一验证手段；
- **不做端到端验证**：M3 验收「界面达到概设 §8 视觉规范；不同模型接力完成同一任务」留待用户在 WSL 环境自行验证；
- **裁剪项（本轮不做，显式登记）**：
  1. tool calling 能力验证与文本协议降级（概设 §6.2 的 P1 兜底、§12 裁剪顺序第 3 位）——当前候选档案均支持 tool calling，未排期；
  2. `/theme` 暗/亮模式切换（主题文档 §3「后续可加」，未排期）；
  3. 会话结束「自动提炼记忆候选」（连续三轮未排期，维持现状）；
  4. `/model add` 向导（概设 §6.1「手工编辑，或向导」——本轮只支持手工编辑 models.toml，向导未排期）；连带「注册时连通性校验」偏移为「切换时校验」（概设 §6.1 原文为注册时校验）——手工编辑无注册时刻，切换校验已覆盖「立即可用」语义，债务登记 §9 与 TODO.md（评审 S1）；
  5. 「☁ 后台运行中」意象文案：BashTool 尚无后台执行能力（FR-03 扩展面，当前代码未实现），文案无落点，本轮裁剪，随 bash 后台能力一并排期（概设 §8.4 意象表）；
  6. 工程位置偏离概设字面规定两处（评审 S2，均为有意决策）：内置示例 skill 放包内 `src/glaucous/assets/skills/`（概设 §10 字面为仓库根 `skills/`）——包内资产随 pip 分发、内置技能开箱可用，仓库根目录不参与运行时扫描；`models.toml` 放 `~/.glaucous/`（概设 §6.1/§9 字面为工作区级入 gitignore）——理由见 §5 D3；
- 编码策略统一为 Code-First（跳过新增 Test 产出的裁剪变体）。

## 1. 总体架构

Day 5 在 Day 4 基线上新建 **ui/theme.py**、**ui/renderer.py**、**llm/registry.py**、**extensions/skills.py**、**extensions/init_draft.py**、**commands.py**、**tools/skill_tool.py** 与包内资产 `assets/skills/`（概设 §10），并重构 cli 层：

```
src/glaucous/
├── cli.py ※                     # REPL 重构：ReplContext 聚合 + prompt_toolkit 输入层
│                                #   + 斜杠命令分派接线 + 回调改用 renderer 卡片 + on_retry 注入
├── commands.py ※（新）          # 3.3 斜杠命令处理器：/help /plan /build /compact /clear
│                                #   /resume /model /memory /rules /skills /init /stop /exit /quit（14 个）
├── config.py ※                  # 默认档案解析委托 registry（环境变量档案兜底语义不变）
├── llm/
│   ├── registry.py ※（新）      # 3.4 ~/.glaucous/models.toml 注册表 + ping 连通性校验
│   └── client.py ※              # switch_profile() + on_retry 重试通知钩子（§4.2）；ping 在 registry 层
├── extensions/
│   ├── skills.py ※（新）        # 3.5 skill 扫描（内置<全局<项目）+ 索引文本 + 惰性加载
│   ├── memory.py ※              # /memory 管理面：entries() / remove()
│   └── init_draft.py ※（新）    # 3.6 /init 工作区扫描 + glaucous.md 草稿模板
├── tools/
│   └── skill_tool.py ※（新）    # 3.5 load_skill 工具（惰性加载通道）
├── assets/
│   └── skills/ ※（新，包数据）  # 内置示例 skill：code-review / release-checklist
└── ui/
    ├── theme.py ※（新）         # 3.1/3.7 色板常量 + rich Theme + Console 工厂（降级单一出口）
    ├── renderer.py ※（新）      # 3.2 工具行/四类卡片/状态栏/Banner/意象文案（rich 渲染）
    └── prompts.py ※             # skill 索引注入段（新增 skills 参数）
```

核心交互流（Day 4 链路上叠加，概设 §8）：

```
glaucous 启动
  → theme.make_console()：终端能力探测（truecolor/256/16/无色，3.7 单一出口）
  → load_model_registry()：models.toml 解析 → 缺省档案（环境变量兜底）→ LLMClient
  → renderer.banner(model, mode)：启动 Banner（一次）
  → REPL 循环（prompt_toolkit 输入，非交互降级 input()）：
      斜杠输入 → commands.handle(line, ctx) 本地处理，不进 LLM
      普通输入 → loop.run(task)（事件经 renderer.render 着色渲染）
  → 状态栏：等待输入期间经 bottom_toolbar 常驻（模式徽标+模型+占用），
    任务执行期间由 budget 事件打印状态行（与流式输出不冲突，§5 D1）
```

## 2. 分层影响分析

| 层级 | 受影响模块 | 变更说明 |
|------|-----------|---------|
| CLI 交互层 | cli.py（重构）、commands.py（新） | ReplContext 聚合可重建组件；prompt_toolkit 输入 + 斜杠分派；三处交互回调（方案确认/审批/提问）改经 renderer 卡片 |
| 视觉层（新） | ui/theme.py、ui/renderer.py | 色板单一出口 + rich Theme；事件渲染、四类卡片、状态栏、Banner、意象文案 |
| LLM 网关 | llm/registry.py（新）、client.py、config.py | models.toml 多档案 + ping + 运行时切换；环境变量档案兜底语义保留 |
| 扩展子系统 | extensions/skills.py（新）、init_draft.py（新）、memory.py | skill 扫描/索引/加载；/init 草稿；记忆管理面（查看/删除的数据接口） |
| 工具系统 | tools/skill_tool.py（新） | load_skill（两模式、SAFE） |
| 上下文 | 不涉及 history/budget/compactor 逻辑 | /compact 仅新增「手动触发」调用点，压缩管线本身零改动 |
| 依赖与打包 | pyproject.toml、requirements.txt | +rich、+prompt_toolkit；包数据声明（assets/skills） |
| 不涉及 | agent/loop.py、permission/、safety/、context/ | 无变更（事件契约不变，仅消费端换渲染器） |

## 3. 数据模型

```python
# llm/registry.py（3.4）
@dataclass(frozen=True)
class ModelEntry:                      # toml 原始档案（密钥只存环境变量名，FR-33）
    name: str                          # [models.<name>] 段名
    base_url: str
    model: str
    api_key_env: str
    temperature: float = 0.2

class RegistryError(RuntimeError): ...  # toml 非法/密钥入库等配置错误

def models_toml_path() -> Path          # ~/.glaucous/models.toml（用户级，天然不入库）

def load_registry(env: dict | None = None) -> tuple[dict[str, ModelEntry], str]
    # 返回（档案表, 默认档案名）：
    # - 文件缺失/无 [models] → 由环境变量生成单档案 {"env": ModelEntry(...)}，
    #   默认名 "env"（GLAUCOUS_* 语义与 config.load_profile 完全一致，兜底不回归）
    # - toml 解析失败/字段非法/出现 api_key 明文段 → RegistryError（启动即失败，绝不带病运行）
    # - 默认档案名：GLAUCOUS_DEFAULT_MODEL（须为已注册段名，否则 RegistryError）> 首个段

def resolve_profile(entry: ModelEntry, env: dict | None = None) -> LLMProfile
    # api_key_env → 环境变量取值；缺失/空 → RegistryError（错误信息指明变量名）

async def ping(entry: ModelEntry, env: dict | None = None) -> tuple[bool, str]
    # 最小连通性请求：chat.completions.create(stream=False, max_tokens=1,
    #   messages=[{"role":"user","content":"ping"}], timeout=15s)；
    # 返回 (成功?, 失败原因摘要)；密钥缺失直接 (False, "环境变量 X 未设置")

class LLMClient:                     # llm/client.py 变更点（评审 S7：与 §4.2/§4.4 同步）
    def __init__(self, profile, on_retry: Callable[[int, float], None] | None = None)
        # on_retry：退避入睡前通知（第 N 次, 预计等待秒）；默认 None 兼容既有构造（§4.2）
    def switch_profile(self, profile: LLMProfile) -> None
        # 替换 _profile 并重建 AsyncOpenAI 客户端；历史消息为 OpenAI 通用结构，
        # 切换只改后续请求路由（概设 §6.2，FR-27「历史无缝延续」）

# extensions/skills.py（3.5）
@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    source: str            # "builtin" | "global" | "project"
    path: Path             # SKILL.md 路径

class SkillRegistry:
    def __init__(self, workspace: Path)
    def scan(self) -> None
        # 三层扫描：包内资产（内置）→ ~/.glaucous/skills/ → <workspace>/.glaucous/skills/
        # 目录即注册：子目录含 SKILL.md 才有效；同名覆盖（项目 > 全局 > 内置）；
        # frontmatter（--- 分隔，key: value 行）取 name（缺省回退目录名）与
        # description（缺省空串）；解析失败的目录跳过并记录到 warnings，不崩溃
    def index_text(self) -> str      # "- name: description" 逐行；无技能返回 ""
    def load(self, name: str) -> str | None   # 正文（frontmatter 之后全文）；未注册返回 None
    def infos(self) -> list[SkillInfo]
    def loaded_names(self) -> set[str]        # 本会话已加载（/skills 状态展示）
    warnings: list[str]                       # 扫描告警（格式非法被跳过的目录）

# extensions/init_draft.py（3.6）
def scan_workspace(workspace: Path) -> tuple[list[str], list[str]]
    # 返回（相对路径条目 ≤50, 识别出的项目特征描述列表）；
    # 遍历深度 ≤2，跳过隐藏目录/.git/__pycache__/node_modules/.venv/venv；
    # 特征识别：pyproject.toml/requirements.txt→Python；package.json→Node.js；
    # pom.xml/build.gradle→Java；go.mod→Go；Cargo.toml→Rust；README* → 首行标题
def render_draft(features: list[str]) -> str   # glaucous.md 草稿模板（§4.6）

# extensions/memory.py（扩展）
class MemoryStore:
    def entries(self, scope: str) -> list[dict]   # 副本（/memory 展示用，防外改）
    def remove(self, scope: str, index: int) -> bool
        # 0-based 序号删除 + 落盘；越界返回 False（FR-21「可删除」的管理面）

# commands.py（3.3）
@dataclass
class ReplContext:                    # REPL 可变聚合：斜杠命令与重建循环的唯一通道
    workspace: Path
    config: Config
    registry_entries: dict[str, ModelEntry]
    current_model: str                # 当前档案名
    llm: LLMClient
    memory_store: MemoryStore
    skills: SkillRegistry
    state: SessionState
    history: History
    system_prompt: str
    loop: AgentLoop
    audit: AuditLog
    renderer: Renderer
    pipeline: ApprovalPipeline
    outputs_dir: Path
    plans_dir: Path
    last_budget: dict | None          # 最近一次 budget 事件 payload（状态栏数据源）

async def handle_command(line: str, ctx: ReplContext) -> bool | str
    # line 以 "/" 开头才受理；返回 True=已处理继续 REPL，"exit"=退出；
    # 未识别命令打印 /help 指引，返回 True（不误发给 LLM）
```

## 4. 接口设计（模块间契约）

### 4.1 色板与主题（ui/theme.py，任务 3.1/3.7，FR-30）

- **色板常量单一出口**：`PALETTE` 字典收录主题文档 §1 的 9 个色值（天青 `#3AA6B9`、海鸥白 `#EAF4F4`、深海蓝 `#1B2A4A`、海盐青 `#9BD1D9`、晚霞橙 `#F4A261`、晴空灰 `#5A7A8C`、海草绿 `#7FB685`、陶土红 `#E07A5F`、亮青 `#6BB7C9`——晚霞橙同时充当品牌色与「审批/确认」语义色，不重复计数）；全仓禁止再出现字面色值（renderer/commands 一律引用 PALETTE）。命名统一用「海鸥白」（不并用「海泡沫白」）。
- **rich Theme 命名风格**：`glaucous.brand`（天青加粗）、`glaucous.text`（海鸥白）、`glaucous.dim`（海盐青）、`glaucous.muted`（晴空灰）、`glaucous.warn`（落日橙）、`glaucous.error`（陶土红）、`glaucous.success`（海草绿）、`glaucous.accent`（亮青）、`glaucous.card`（海盐青边框用）；`build_theme() -> Theme` 为唯一构造点。
- **终端降级（3.7）——`make_console() -> Console` 工厂**：
  - 非 TTY（重定向/管道）或环境变量 `NO_COLOR` 非空 → `Console(no_color=True, highlight=False)`（纯文本，日志干净）；
  - `GLAUCOUS_COLOR` 可显式指定 `truecolor|256|standard|mono`（覆盖探测，调试/答辩演示用）；
  - 其余交由 rich 自动探测（truecolor→24bit 原色；256 色→自动映射最近调色板；16 色→映射 ANSI 基色）——三档降级由 rich 的 color_system 机制承担，本层只做探测与开关，不重复造映射表（概设 §8.5、主题文档 §3）。
- **cp936 兜底保留**：cli.main 中 stdout/stderr `errors="replace"` 重配置不变（块字符/意象符在 GBK 终端降级可读不崩溃，FR-34）。

### 4.2 渲染规范（ui/renderer.py，任务 3.2，FR-30/31，概设 §8.3/§8.4）

`Renderer` 类（持 Console + 最近 budget 缓存），承接现 `cli.render_event` 全部事件并 rich 化；事件契约（loop 侧）零改动：

- **工具调用行（无框，学 Claude Code 密度）**：
  - `tool_start`：`⏺ <工具名> <参数摘要 ≤80 字符> ❄`——⏺ 用 `glaucous.brand`，工具名 `glaucous.accent`，摘要 `glaucous.text`，行尾 `❄`（海盐青）标示进行中（概设 §8.4 示例同款静态形态；不引入 rich Live 动画，与 D1 同理——事件是两点式快照，`⎿` 结果行到达即视为完成）；
  - `tool_end`：`⎿ <摘要>`——成功海盐青（`glaucous.dim`），保留现行「≤3 行拼接 / 超 3 行尾部摘要+总行数」渐进披露；失败前缀 `✘` 用 `glaucous.error`；
  - 耗时展示：`tool_end` 事件无 duration 字段（不扩事件契约），耗时摘要沿用 `_meta` 之外的现有形态——本轮不新增耗时列（登记建议项，M4 视情况补）。
- **四类卡片（仅人介入时刻升格为 Panel，概设 §8.3「无框优先」）**：
  1. **方案确认卡**（落日橙边）：`◆ 方案已就绪` 标题 + 方案全文 + ①②③ 选项行（替代现 `prompt_plan_decision` 的 print 拼装）；
  2. **审批卡**（常规落日橙边；`Risk.DANGEROUS` 换陶土红边 + `⚠` 前缀）：`需要您的确认` 标题 + 操作/目标/风险说明 + detail（≤60 行）+ 选项行；
  3. **提问卡**（海盐青边）：`🕊 请教你` 标题 + 问题 + 候选编号行（替代现 ask 回调 print）；
  4. **警示卡**（陶土红边）：REPL 顶层本轮失败与 budget critical 预警的整段呈现（替代裸 print 到 stderr）。
- **Banner**（启动一次）：`☁ Glaucous · coding agent`（天青加粗）+ 副标语（海鸥白）+ 一行提示（晴空灰）；去除现行「（M2 记忆与上下文）」版本字样，改为当前模型名与模式占位。
- **状态栏**（FR-31 常驻）：
  - 等待输入期间经 prompt_toolkit `bottom_toolbar` 常驻（§4.3）：`[◆ plan | <模型名> | ctx 34% ███████░░░ 43k/128k]`（占用条 10 格，示例按 34% 显示填充数）——Plan 徽标 `◆` 天青、Build 徽标 `⬥` 亮青并附策略（`⬥ build·每次审批`）；占用条三档变色（与压缩阈值同一档位判定：低海草绿 / >70% 落日橙附「建议 /compact」/ >85% 陶土红附「🌊 即将自动压缩」）；无 budget 数据时占用段显示 `ctx --`；
  - 任务执行期间（流式输出与进度占前台）：`budget` 事件打印同款单行状态行——「常驻」语义由两个通道接力实现，不引入 rich Live（§5 D1）。
- **流式正文**：`text` 事件经 `console.print(chunk, end="", markup=False, highlight=False)` 原样输出（不解析 Markdown——内联渲染登记为建议项，避免代码内容被 markup 误解析）；段末换行逻辑沿用现 repl 的 `stream_state` 判定。
- **意象文案映射**（主题文档 §4，图标保留意象、文字朴素）：上下文压缩事件 `🌊`、会话恢复 `🌅 已恢复上次会话`、退出 `🌅 再见。`、/stop `☁ 会话已保存。`；`diagnostic` 事件维持单行 `⎿` 形态（安静，不升格卡片）。
- **重试提示（「↻ 重试中」，概设 §8.4 意象表 + §4.4）**：`LLMClient.__init__` 新增可选钩子 `on_retry: Callable[[int, float], None] | None = None`（参数为第 N 次重试与预计等待秒数；默认 None 保持既有构造兼容，测试不受影响）——退避入睡前调用；CLI 注入实现经 renderer 打印单行 `↻ 重试中（第 N 次，约 Xs）`（`glaucous.muted`）。客户端保持纯传输职责：钩子只做通知，不参与重试决策。

### 4.3 输入层与斜杠命令框架（cli.py + commands.py，任务 3.3，FR-31）

- **prompt_toolkit PromptSession**：`FileHistory(~/.glaucous/repl_history)` 跨会话输入历史；`WordCompleter` 对 14 个斜杠命令（含 /exit、/quit）补全；`bottom_toolbar` 回调读 `ctx` 现值（模式/模型/占用）。
- **降级路径（必须）**：以下任一成立回退 `input()`（sanitize_input 净化，行为与 Day3 一致）——① stdin 非 TTY（测试/管道）；② prompt_toolkit 导入失败（依赖损坏不拒启动）；③ `GLAUCOUS_INPUT=plain`。两路径经同一 `read_line()` 协程出口，REPL 主循环不感知差异。
- **分派协议**：输入以 `/` 开头 → `commands.handle_command`（本地处理，绝不发给 LLM）；否则进 `loop.run`。`/exit`、`/quit`、`/stop` 返回退出；Ctrl+C/EOF 在输入处维持现语义（退出会话）。
- **命令全集与语义**（未知命令 → 打印可用列表）：

| 命令 | 语义 | 关键契约 |
|---|---|---|
| `/help` | 列出全部命令一行说明 | 无副作用 |
| `/plan` | 强制回归 Plan（`state.return_to_plan()`） | 已处 Plan 提示无需切换；写审计 `{"event":"mode_switch",...}` |
| `/build` | 用户驱动进入 Build（`enter_build(PER_ACTION)`） | 异常终止滞留 Plan 之外的补位通道（modes.py 注释语义）；写审计；auto-approve 只能经 submit_plan ② 授予（防绕过方案确认拿全放行） |
| `/compact` | 手动压缩：先 `trim_history` 后 `compact_history`（复用 loop 同款函数） | 展示压缩前后 token 估算；L2 失败提示仅完成 L1；压缩后打印新状态行 |
| `/clear` | 开新会话：`History.create` 新 JSONL + `SessionState()` 重置 + 重建 AgentLoop | 旧会话文件保留，可 /resume；system prompt 现读重建（规则/记忆/技能索引刷新） |
| `/resume [id]` | 会话内恢复：复用启动 `resume_history` 逻辑（不带参取最新、前缀模糊匹配）+ 重建 loop | 恢复后 system prompt 用当前启动时构建的版本（不重建，避免注入段闪变；规则变更需 /clear 生效——与 Day4 D6 一致） |
| `/model` | 档案列表：名称 + base_url/model + 密钥状态（✓已设置/✗缺变量名）+ 当前高亮 | 不做批量 ping（列表秒回，§5 D4） |
| `/model <name>` | 切换：ping 成功 → `llm.switch_profile` + 更新 ctx.current_model + 状态栏即时反映；失败打印原因且不切换 | 未知名 → 列出可用；写审计 `{"event":"model_switch",...}` |
| `/memory` | 双作用域全量列表：`[p1] 内容 [category] (last_used)`，项目/全局分段编号 | FR-21「可查看」 |
| `/memory add <scope> <内容>` | 新增（复用 `MemoryStore.add`，同内容去重提示） | scope∈{global,project}，非法给例 |
| `/memory del <scope> <序号>` | 按列表序号删除（`MemoryStore.remove`） | 越界提示当前条数 |
| `/rules` | 展示全局/项目两文件路径与原文（缺省标注「未创建」） | 只读 |
| `/skills` | 技能表：名称 / 描述 / 来源（内置·全局·项目）/ 本会话是否已加载 | 扫描告警（格式非法被跳过）一并提示 |
| `/init` | 生成 glaucous.md 草稿（§4.6） | 确认后才写盘 |
| `/exit` / `/quit` | 退出会话（现行语义保留） | 与 /stop 同出口，文案 `🌅 再见。` |
| `/stop` | 优雅结束会话：落盘提示后退出（= /exit 的语义别名） | 输入阶段无运行中任务，轮内中断由 Ctrl+C 承担（§5 D7） |
| 未知 `/xxx` | 不发给 LLM，打印 `/help` 可用命令列表 | 分派协议兜底 |

- **规则/记忆变更生效时机**：注入段在启动（及 /clear）时读取（Day4 D6），`/memory add|del` 后提示「下次会话或 /clear 后注入生效」——不为单命令重启注入链路。
- **审计**：`/plan`、`/build`、`/model <name>` 成功切换经既有 `AuditLog.record` 追加事件（尽力而为，不阻断）。

### 4.4 模型注册与切换（llm/registry.py + client.py，任务 3.4，FR-26/27/33）

- **注册表位置**：`~/.glaucous/models.toml`（用户级主目录，不随仓库分发——天然满足「密钥不出现在任何入库文件」，FR-33）；格式与概设 §6.1 一致（`[models.<name>]`：base_url / api_key_env / model / temperature 可选）。
- **密钥零存储**：解析时若段内出现 `api_key` 键 → `RegistryError`（明示「密钥只能经环境变量提供」），把 FR-33 从约定升级为硬校验。
- **环境变量兜底**：文件缺失或无 `[models]` → 由 `GLAUCOUS_*` 生成名为 `env` 的单档案（与现行 `config.load_profile` 同默认值/同报错语义：`GLAUCOUS_API_KEY` 缺失启动即 `ConfigError`）——无 models.toml 的用户体验与 Day4 完全一致（风险预案第 4 条「环境变量单模型兜底」自动成立）。
- **默认档案**：`GLAUCOUS_DEFAULT_MODEL` 指定段名（非法段名 → 启动报错）；未指定取 toml 首个段；环境变量兜底时即 `env`。
- **连通性校验**：`/model <name>` 切换时同步 `await ping(...)`（≤15s）——成功才切换，失败打印原因（网络/鉴权/密钥缺失）并保持原档案（§5 D4）；注册入口为手工编辑，「注册时校验」由用户切换时自然覆盖（无向导，§0 裁剪 4）。
- **切换即时生效面**：后续全部请求（含 loop 内守卫压缩的 L2 调用——经同一 `LLMClient` 实例）；历史与 state 不动（FR-27 接力语义）；状态栏模型名随 `ctx.current_model` 刷新。
- **config.py 收缩**：`load_profile` 保留（测试兼容），`load_config` 内改为经 `registry.resolve_profile` 取默认档案；`LLMProfile` 结构不变。

### 4.5 Skill 注册系统（extensions/skills.py + tools/skill_tool.py + prompts.py，任务 3.5，FR-28）

- **SKILL.md 格式**（概设 §7.3）：首部 `---` frontmatter（`name:`、`description:` 两键，`key: value` 行，自研解析不引 yaml）+ 正文；frontmatter 缺失/畸形 → 该目录跳过并记 `warnings`（不因一个坏技能拖垮启动）。
- **三层扫描与覆盖**：包内资产 `assets/skills/`（内置，随包分发）→ `~/.glaucous/skills/` → `<workspace>/.glaucous/skills/`；同名后者覆盖前者（项目定制优先）。内置两个示例：`code-review`（代码评审清单）、`release-checklist`（发布前检查单）——正文为可执行的步骤清单（各 20 行内）。
- **两段式惰性加载**：启动只注入索引（`name: description` 每行 <30 token）；`load_skill(name)` 工具取正文回喂——正文经工具结果入史，会话内持续可见、跨会话自然失效（「仅本会话生效」无需额外机制）；重复加载同一技能允许（幂等，正文回喂即可）。
- **load_skill 契约**：参数 `name`（必填）；两模式可用、risk=SAFE（读取的是注册表资产，无沙箱面）；未知名回喂可用技能清单（与幻觉工具同款引导范式）；扫描为空时索引段省略，工具回喂「未注册任何技能」。
- **注入接线**：`build_system_prompt(workspace, rules, memory, skills)` 新增 `skills: str = ""` 参数（默认空保兼容），顺序：基础准则 → 工作区 → 规则 → 记忆 → 技能索引（概设 §4.2 注入段序的尾部延伸），空段省略；BASE_PROMPT 增补一句：任务与某技能描述相关时应先 `load_skill` 再行动。
- **打包**：`pyproject.toml` 增 `[tool.setuptools.package-data]` 声明 `assets/skills/**/SKILL.md`；扫描经 `importlib.resources` 定位（开发态 `pip install -e .` 与 wheel 安装均可达）。

### 4.6 /init 草稿生成（extensions/init_draft.py，任务 3.6，FR-23）

- **扫描**：深度 ≤2、跳隐藏/依赖目录（.git、node_modules、__pycache__、.venv、venv）、条目上限 50；特征识别产出描述行（语言栈/构建文件/README 标题）。
- **草稿模板**（占位待用户修订，体现「初始草稿」语义）：

```markdown
# 项目规则（glaucous.md）

> 本文件由 /init 生成草稿，请修订后使用。规则全量注入智能体上下文，保持精炼。

## 项目概况
<自动识别行，如：Python 项目（pyproject.toml）；README：<标题>>

## 构建与测试命令
- （待填写）构建：
- （待填写）测试：

## 编码约定
- （待填写）

## 禁止操作
- （待填写）
```

- **交互与保护**：项目 `glaucous.md` 已存在 → 打印路径提示「已存在，不覆盖」直接返回（不追加不覆盖，§5 D6）；不存在 → 展示草稿全文 → `[y] 写入 / [n] 放弃`（EOF/非法视为放弃）→ 写入后提示「下次会话或 /clear 后生效」（注入时机同 §4.3）。

### 4.7 CLI 装配重构（cli.py）

- **ReplContext 聚合**（§3）：repl() 组装全部组件注入 ctx；`build_registry(ctx)` 与回调闭包一律经 `ctx.state` / `ctx.renderer` 间接引用——`/clear`、`/resume` 替换 `ctx.history`/`ctx.state` 后 `rebuild_loop(ctx)` 重建 AgentLoop，审批/确认/提问回调自动跟随新状态（闭包不捕获旧对象，§5 D8）。
- **AgentLoop / 事件契约零改动**：`on_event` 回调内改经 `ctx.renderer.render(event, payload)`；`budget` 事件同时缓存 `ctx.last_budget`（状态栏数据源）。
- **`main(argv)` 签名与参数不变**；`sanitize_input` 保留原位置（基线测试面）；`render_event` 自由函数从 cli 移除（职责迁入 Renderer）。
- **依赖新增**：`rich>=13,<14`、`prompt_toolkit>=3,<4`（概设 §3 技术选型既定项，本轮首次落地）；pyproject `dependencies` 与 requirements.txt 同步。

## 5. 关键设计决策

| 决策点 | 选项 A | 选项 B | 选择 | 理由 |
|--------|--------|--------|------|------|
| D1 状态栏常驻实现 | rich Live 底部常驻条 | bottom_toolbar（等待输入）+ 事件状态行（执行中）接力 | B | Live 与流式 on_text 直写、审批 input() 阻塞读互相抢占，需全程接管 stdout；接力方案零冲突且两时段信息都不缺 |
| D2 输入层 | 全量替换 input() | prompt_toolkit + 非交互/失败降级 input() | B | 测试与管道场景（既有回归依赖 stdin 重定向）必须保留纯 input() 路径；降级开关显式可控 |
| D3 models.toml 位置 | 工作区 .glaucous/models.toml | ~/.glaucous/models.toml 用户级 | B | 档案是跨项目个人配置；放工作区则每个项目重复注册且易随仓库误分发（FR-33 风险） |
| D4 连通性校验时机 | /model 列表时批量 ping | 仅切换时 ping 目标档案 | B | 列表要秒回（可能含离线档案）；切换是唯一需要「立即可用」保证的时刻，失败不切换即止损 |
| D5 skill 覆盖语义 | 同名报错 | 项目 > 全局 > 内置 覆盖 | B | 概设 §7.3 双层记忆同款「更近的作用域优先」；项目定制覆盖内置是合理诉求 |
| D6 /init 对已存在文件 | 询问覆盖 | 拒绝覆盖只提示 | B | 规则文件是团队资产（应入库，概设 §9），误覆盖代价高；手写修订路径成本极低 |
| D7 /stop 语义 | 中断运行中任务 | 优雅退出会话（/exit 别名） | B | 输入阶段不存在运行中任务（REPL 串行），任务中唯一输入通道被 loop 占用；轮内中断已由 Ctrl+C 覆盖（Day2 既有善后），再造并行中断通道收益为负。**分歧标注（评审 S3）**：概设 §4.1 终止条件④语境下「停止」隐含保留现场可继续——本方案现场经会话 JSONL 全量落盘，`--resume`/`/resume` 即恢复通道，语义等价且已验证（M0 0.14/0.15），不另造内存态挂起 |
| D8 回调与重建 | 回调捕获具体 state/loop 对象 | 回调经 ctx 间接引用 | B | /clear、/resume 会整体替换 history/state/loop；捕获旧对象会产生新旧两套状态并存的越权窗口 |
| D9 流式正文渲染 | rich Markdown 内联渲染 | console.print 原样输出 | B | 代码内容含大量 markup 特殊字符（[/]、[#]），渲染风险 > 收益；建议项登记，M4 或后续专项 |
| D10 记忆删除接口 | CLI 直接改 JSON 文件 | MemoryStore 增 remove() 走原子写 | B | 写入原子化与去重语义集中在存储层，旁路写文件会破坏「单一出口」 |

## 6. 编码策略决策

按 §0 裁剪声明，本轮全部步骤为 **Code-First（跳过 Test 产出的裁剪变体）**：

| 步骤 | 任务描述 | 策略 | 决策依据 |
|------|---------|------|---------|
| Step 1 | 依赖声明（pyproject/requirements）+ theme.py 色板/主题/降级 + renderer 事件渲染与工具行 | Code-First | 纯展示层，逻辑直白 |
| Step 2 | renderer 四类卡片 + Banner + 状态行 + 重试提示行；LLMClient 增 on_retry 钩子；cli 三处回调切换卡片；移除旧 render_event | Code-First | 视觉收敛在 renderer 单点；钩子属展示面归本步 |
| Step 3 | ReplContext + rebuild_loop + prompt_toolkit 输入层（含降级）+ commands.py 框架与 /help /plan /build /clear /stop /exit；repl 装配处注入 on_retry→renderer | Code-First | REPL 重构为后续命令铺路 |
| Step 4 | /compact、/resume（会话内恢复） | Code-First | 复用 compactor/resume_history 既有函数，纯接线 |
| Step 5 | registry.py + ping + client.switch_profile + /model + config 接线 | Code-First | 本轮最复杂步骤（配置解析 + 网络校验） |
| Step 6 | memory 管理面（entries/remove）+ /memory /rules；skills.py + load_skill + 注入 + 内置示例 + /skills | Code-First | 存储扩展 + 扫描装配 |
| Step 7 | init_draft.py + /init | Code-First | 模板与文件保护，直白 |
| Step 8 | 全模块导入编译验证 + 基线保护回归（既有用例全绿）+ TODO.md 债务与建议登记 | Code-First | 收尾校验 |

## 7. 实施步骤

- [ ] Step 1：pyproject.toml/requirements.txt 新增 rich、prompt_toolkit 与包数据声明；ui/theme.py（PALETTE/build_theme/make_console 降级工厂）（任务 3.1/3.7）
- [ ] Step 2：ui/renderer.py（工具行/四类卡片/状态行/Banner/意象文案/重试提示行）+ LLMClient.on_retry 钩子 + cli 方案确认/审批/提问三回调改用卡片 + 事件渲染切换 + 移除旧 render_event（任务 3.2）
- [ ] Step 3：cli.py ReplContext 重构 + rebuild_loop + prompt_toolkit 输入层（FileHistory/14 命令补全/bottom_toolbar/降级三条件）+ commands.py 框架与 /help /plan /build /clear /stop + repl 装配注入 on_retry（任务 3.3 上半）
- [ ] Step 4：commands.py /compact（L1+L2 手动触发）与 /resume [id]（会话内恢复 + 重建）（任务 3.3 下半，M2 遗留命令偿还）
- [ ] Step 5：llm/registry.py（models.toml 解析/密钥硬校验/环境变量兜底/默认档案）+ ping + LLMClient.switch_profile + /model 列表与切换 + config.py 接线 + 审计（任务 3.4）
- [ ] Step 6：MemoryStore.entries/remove + /memory 三形态 + /rules；extensions/skills.py（三层扫描/索引/加载）+ tools/skill_tool.py（load_skill）+ prompts 注入段 + assets/skills 两个内置示例 + /skills（任务 3.5 + FR-21 管理面）
- [ ] Step 7：extensions/init_draft.py（扫描/模板/保护）+ /init 交互（任务 3.6）
- [ ] Step 8：全模块导入编译验证 + **基线保护回归**（`python -m pytest tests/ -q` 既有用例全绿，§0 声明允许并要求）+ TODO.md 登记测试债务与建议项（收尾）

## 8. 风险与注意事项

| 风险 | 缓解 |
|------|------|
| prompt_toolkit 与 asyncio/Windows 终端兼容问题 | prompt_async 原生运行于既有事件循环；异常路径（导入失败/非 TTY）一律降级 input()（§4.3），REPL 不因输入层故障拒启动 |
| 回调捕获旧对象导致 /clear、/resume 后状态错位（越权窗口） | 回调全部经 ctx 间接引用（D8）；rebuild_loop 后旧 loop 对象不再被任何入口持有 |
| rich markup 误解析代码/错误文本 | 全部动态内容 `markup=False`；样式只经预定义命名风格施加（D9） |
| models.toml 写错导致启动失败 | 解析错误信息指明段名/字段；环境变量兜底路径保证「删掉 toml 即回到 Day4 行为」 |
| ping 超时阻塞 /model | 15s 超时上限 + 失败不切换；列表路径零网络（D4） |
| 密钥意外入库 | api_key 段硬拒绝（RegistryError）+ models.toml 位置在用户主目录（D3）+ .gitignore 已有 .glaucous/ 条目（双保险） |
| SKILL.md frontmatter 畸形 | 跳过并记 warnings，/skills 展示告警；不中断启动 |
| importlib.resources 在 `pip install -e .` 下资产定位差异 | 扫描层统一经 `files("glaucous").joinpath("assets/skills")` 遍历；两种安装形态均列入手动验证项（用户环境） |
| cp936 终端下 rich 宽字符/块字符显示错位 | make_console 对非真彩终端宽度计算由 rich 承担；`errors="replace"` 兜底不变；最坏降级为可读不崩溃（FR-34） |
| 12 命令实现面大导致 cli.py 膨胀 | 命令逻辑集中在 commands.py（cli.py 只留装配与输入循环）；每命令独立函数可单测（M4） |
| /compact 与守卫点自动压缩重复 | 手动压缩走同一对函数（幂等：_trimmed 跳过 + L2 无早期内容返回 True）；压缩后守卫点按新预算评估，无冲突 |
| bottom_toolbar 在无 budget 数据时空白 | 占用段显示 `ctx --`；模式/模型段始终有值（启动即确定） |

## 9. 测试策略（本轮不产出，登记债务）

**本轮不产出新增测试、不执行新增验证（既有用例基线保护回归除外，§0 已声明）**。登记为测试债务（M4 任务 4.1 偿还，概设 §10/§11 已预留文件名）：

| 债务项 | 应覆盖 | 对应概设测试文件 |
|--------|--------|-----------------|
| 模型注册表 | toml 解析/字段校验、api_key 明文硬拒绝、文件缺失环境变量兜底（名称/默认值）、GLAUCOUS_DEFAULT_MODEL 非法段名、resolve 密钥缺失、ping 失败原因返回（mock） | test_model_registry.py |
| skill 惰性加载 | 三层扫描与同名覆盖、frontmatter 畸形跳过 + warnings、索引文本格式（<30 token/个）、load 未知名回喂清单、重复加载幂等、空注册表工具回喂 | test_skill_lazyload.py |
| 主题与渲染降级 | make_console 非 TTY/NO_COLOR→no_color、GLAUCOUS_COLOR 覆盖、renderer 工具行/状态行/占用条三档配色（纯函数断言） | test_theme_render.py（新增） |
| 命令层 | /plan、/build 状态流转与审计、/clear 新会话文件、/resume 模糊匹配、/memory add/del 边界（越界/非法 scope）、/init 拒绝覆盖、未知命令不误发 LLM | test_repl_commands.py（新增） |
| 记忆管理面 | entries 副本隔离、remove 越界/落盘原子性 | 并入 test_memory_scope.py（M2 债务项扩展） |
| 连通性校验时机偏移（评审 S1） | 「注册时校验」（概设 §6.1）偏移为「切换时校验」：若后续落地 /model add 向导，应补注册时 ping | 并入 test_model_registry.py（M4） |

M3 验收（界面达到概设 §8 视觉规范；不同模型接力完成同一任务）按用户约束**不在本轮执行**，留待 WSL 环境由用户自行验证。
