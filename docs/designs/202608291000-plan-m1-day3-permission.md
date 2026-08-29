# Glaucous M1 Day3 权限成型 - 技术设计方案

> 创建日期：2026-08-29
> 关联规格：[编程智能体需求文档.md](../../编程智能体需求文档.md)（FR-06/08/09/10/11/12/13/14/16/34）、[Glaucous开发计划表.md](../../Glaucous开发计划表.md)（Day 3/M1 任务 1.1~1.7）、[编程智能体概要设计说明书.md](../../编程智能体概要设计说明书.md)（§5 会话模式与权限体系、§10 工程结构）
> 关联前案：[Day 1 Plan](202608270900-plan-m0-day1-prototype.md)、[Day 2 Plan](202608281000-plan-m0-day2-prototype.md)（均已实施，为本次改造基线）
> 状态：已批准（经 2 轮 Plan Review，末轮通过）

## 0. 本轮范围裁剪声明（用户明确约束）

按用户指示，本轮**只进行代码开发**：

- 不做环境配置、不安装依赖
- **不运行任何测试与端到端验证**（含任务 1.7 单测与 M1 验收「场景 A/C 复现」——执行留待用户环境就绪后自行进行）
- 编码策略统一为 Code-First（跳过 Test 产出的裁剪变体），测试债务登记 §9，M4 偿还

任务 1.7 单测按约束不产出，其覆盖范围（沙箱逃逸、分类器正反例、审批流、auto-approve 守卫）全部登记为测试债务。

## 1. 总体架构

Day 3 在 Day 2 基线上新建 **permission 包**（概设 §10），并把状态从 agent 层迁入（概设 §10 明确 state 归属 permission/modes.py）：

```
src/glaucous/
├── cli.py ※                    # 审批三选项 UI、审计初始化、沙箱注入
├── agent/
│   ├── loop.py ※               # dispatch 前过执行层权限管线（沙箱→分类→审批）
│   ├── state.py → 迁移为薄壳    # 保留 re-export 兼容（内容迁往 permission/modes.py）
├── permission/ ※（新包）
│   ├── __init__.py
│   ├── workspace.py ※          # 1.1 沙箱：realpath 规范化 + 前缀校验 + 符号链接解析 + 只读白名单
│   ├── classifier.py ※         # 1.2 bash 危险命令分类器：首词白名单 + 参数模式表 + 保守升级
│   ├── approval.py ※           # 1.3/1.4 审批管线：三选项决策 + 同类型放行 + auto-approve 守卫
│   └── modes.py ※              # 1.1 关联：SessionState 迁入（mode + approval_policy + audit 挂点）
├── tools/
│   ├── base.py ※               # Tool.risk 属性（safe/write/dangerous）+ 权限回调注入点
│   ├── files.py ※              # read/write/edit 统一走 workspace.resolve + 权限管线
│   └── shell.py ※              # BashTool 分类器接入（Plan 白名单 / Build 全量）
└── config.py ※                 # 只读白名单（环境变量 GLAUCOUS_READONLY_EXTRA，冒号/分号分隔）
```

核心数据流（Day 2 的 dispatch 前新增执行层权限管线，概设 §2.3）：

```
loop.dispatch(call, mode)
  → 工具模式可见性校验（Day 2 已有）
  → approval.gate(action)             # 统一审批管线（1.3/1.4）
       # action.risk 由工具风险声明 / 沙箱 classify_path / 分类器 classify 共同得出：
       #   file_read/file_write → workspace.classify_path(path)（区外→WRITE）
       #   bash_command → classify(command, workspace)（SAFE/WRITE/DANGEROUS）
       → 放行 → 工具执行
       → 拦截 → 三选项决策回喂（同意/同意同类型/拒绝+理由，DANGEROUS/区外不被批量豁免）
  → audit.log 记录每次权限决策（1.6）

权限管线挂接在 ToolRegistry.dispatch 内（base.py），工具模式可见性校验之后；
审批拦截返回 ok=False ToolResult（不计入解析失败熔断计数）。
```

## 2. 分层影响分析

| 层级 | 受影响模块 | 变更说明 |
|------|-----------|---------|
| CLI 交互层 | cli.py | 审批三选项交互（升级自 y/n）；审计初始化；工作区只读白名单注入；违规警示文案 |
| Agent 编排层 | loop.py、agent/state.py→permission/modes.py | 执行层权限管线挂接；状态迁移 |
| 权限体系（新） | permission/workspace.py、classifier.py、approval.py、modes.py | 全新四模块（概设 §10） |
| 工具系统 | tools/base.py（Tool.risk）、files.py（沙箱接入）、shell.py（分类器接入） | 危险级声明 + 权限检查点 |
| 配置 | config.py | 只读白名单（环境变量）；full 权限模型在 M3 config 统一 |
| 不涉及 | llm/、context/（M2）、extensions/（M2）、ui/theme.py（M3） | 无变更 |

## 3. 数据模型

```python
# permission/risk.py（新增：统一风险枚举，单一出口，消除两套字面量）
class Risk(Enum):
    SAFE = "safe"        # 白名单只读语义
    WRITE = "write"      # 修改区内状态
    DANGEROUS = "dangerous"  # 模式表命中 / 区外写

# permission/workspace.py
class Workspace:
    root: Path
    read_only_extra: list[Path]
    def resolve(path) -> Path            # 相对→root 拼接，统一 resolve()
    def check(path) -> Path              # 区内→规范化 Path；区外→抛 WorkspaceEscape
    def classify_path(path) -> Risk      # 区内=SAFE；白名单只读=SAFE；区外=WRITE（读）/DANGEROUS（写由调用方定）
    def is_within(path) -> bool
    def is_read_only(path) -> bool

# permission/classifier.py
def classify(command: str, workspace: Workspace) -> tuple[Risk, str]
    # 返回 (风险级, 匹配说明)；命令内路径参数经 workspace 判定区外
    # 归一化：内部统一用 Risk 枚举（不再有 CommandClass 第二套字面量）

# permission/approval.py
class ApprovalAction:
    kind: Literal["file_read", "file_write", "bash_command"]
    target: str          # 文件路径 或 命令全文
    detail: str          # diff / 分类说明
    risk: Risk           # 统一枚举

class ApprovalDecision:  choice("approve"|"approve_type"|"reject"), reason: str|None
class ApprovalVerdict:   allowed: bool, decision: ApprovalDecision, message: str

# permission/modes.py（迁入 agent/state.py 原内容 + 审计挂点）
class SessionState:
    mode, approval_policy, approved_types: set[str]
```

**单一风险枚举（B1 修复）**：废除 CommandClass 大小写两套字面量，全部统一为 `Risk` 枚举（SAFE/WRITE/DANGEROUS）。`classify` 直接返回 `Risk`，`gate` 直接比较枚举——杜绝 auto-approve 守卫因字面量不一致而失效。

## 4. 接口设计（模块间契约）

### 4.1 工作区沙箱（permission/workspace.py，任务 1.1）

```python
class Workspace:
    def __init__(self, root: Path, read_only_extra: list[Path] = ())

    def resolve(self, path: str) -> Path
        # 相对路径 → root 拼接；绝对路径原样；统一 resolve(strict=False) 规范化
    def check(self, path: str) -> Path
        # 逃逸校验：realpath + 前缀校验 + 符号链接解析（§5.4）
        # 区内 → 返回规范化 Path；区外 → 抛 WorkspaceEscape
    def classify_path(self, path: str) -> Risk
        # 区内=SAFE；只读白名单=SAFE（环境探测免审批，概设 §5.4）；区外=WRITE
        # （读区外仍需审批，风险记为 WRITE 而非 DANGEROUS——由审批决定放行与否）
    def is_within(self, path: Path) -> bool
    def is_read_only(self, path: Path) -> bool   # 区内 or 只读白名单
```

- **realpath + 前缀校验**：`Path.resolve()` 规范化后，`path.is_relative_to(workspace_root)` 判定；防止 `../` 穿越。
- **符号链接解析**：`resolve()` 已展开符号链接；对 `path` 的每个中间组件也校验（`Path.resolve()` 默认全展开）。
- **区外访问 = 触发审批（B2 修复）**：区外访问不直接拒绝，而是**标记**进入审批管线（`classify_path` 返回 WRITE）——用户可逐次审批放行（FR-13「读取工作区外配置 → 仍需单独同意」、场景 C）。`WorkspaceEscape` 仅保留给**路径本身非法**（如无法解析）的硬拦截，与「需审批」语义分离。
- **只读白名单**：`read_only_extra` 允许显式追加区外只读路径（JDK 目录、`~/.m2`），`classify_path` 对白名单路径返回 SAFE——环境探测免审批（概设 §5.4）。白名单**先于**区外判定检查，避免被 check 前置拦截（S7 修复）。

### 4.2 危险命令分类器（permission/classifier.py，任务 1.2）

```python
def classify(command: str, workspace: Workspace) -> tuple[Risk, str]
    # 返回 (风险级, 匹配说明)；命令内路径参数经 workspace.classify_path 判定区外
```

- **首词白名单（SAFE）**：`ls`、`cat`、`grep`、`git status`、`git diff`、`git log`、`pytest`、`python -m pytest`、`echo`、`head`、`tail`、`wc`、`find`、`pwd`、`which`、`python3 -m pytest` 等只读探测。
- **参数模式表（DANGEROUS）**：正则/前缀匹配——
  - `rm -rf /`、`rm -rf ~`、`rm -rf <工作区外路径>`（经 classify_path 判定区外）
  - `git push --force`、`git reset --hard`、`git clean -f`、`git checkout -- <文件>`
  - `sudo`、`su`、`curl <url> | sh`、`wget <url> | sh`
  - `>` 重定向到工作区外、`mv` 移出工作区（经 classify_path 判定目标区外）
- **工作区上下文注入（B4 修复）**：`classify` 接收 `workspace` 参数，命令内出现的路径参数（如 `rm`、`mv`、`>` 目标、`cat` 目标）经 `workspace.classify_path` 判定——命令本身只读但指向区外时，该命令视为需审批的 WRITE（读）或 DANGEROUS（写/删除区外），对齐概设 §5.4「shell 命令中的路径参数同样规范化检查」。
- **保守升级**：无法判定（未命中白名单、未命中危险表）→ **WRITE** 走审批——宁多问不漏放（§5.5）。
- **区分只读/写**：对工作区**内**的写命令（`git commit`、`write_file` 等）定级 WRITE；对工作区**外**的写命令 DANGEROUS。
- `git status` 等明确只读；`git push`（非 force）→ WRITE；`git push --force` → DANGEROUS。

### 4.3 审批管线（permission/approval.py，任务 1.3/1.4）

```python
class ApprovalPipeline:
    def __init__(self, state: SessionState, audit: AuditLog)
    def gate(self, action: ApprovalAction) -> ApprovalVerdict
        # ApprovalAction: kind("file_read"|"file_write"|"bash_command"), target, detail, risk
```

- **kind 扩展（B2 修复）**：新增 `"file_read"`——区外读（含命令内读区外文件）通过该 kind 触发审批，与 FR-13「读取工作区外配置仍需单独同意」对齐。file_write/bash_command 覆盖写操作。
- **per-action 策略**：每次写操作/写命令/区外读都走用户三选项（概设 §5.3）。
- **同意同类型**：选择"同意同类型"后，`state.approved_types` 记录该类型（按工具/操作粒度：`write_file`/`edit_file`/`bash`/`file_read`），本会话内同类型不再询问（FR-11）。
- **守卫优先级（S1 修复）**：**DANGEROUS 与区外写不可被"同意同类型"批量豁免**——即使已同意同类型，DANGEROUS/区外写仍逐条确认（安全底线，守卫优先级高于同类型豁免）。
- **auto-approve 守卫（设计底线，FR-10，B1 修复）**：即使 `approval_policy == auto-approve`，以下两类仍**单独醒目确认**（用统一 Risk 枚举比较，杜绝字面量失效）——
  - `action.risk == Risk.DANGEROUS`（破坏性命令，含工作区外写）
  - `action.risk == Risk.WRITE` 且 `action.kind == "file_read"`（区外读，信任边界不可被批量授权跨越）
  - 其余写操作自动放行（记录审计）。
- **决策回喂**：拒绝附理由时返回 `用户已拒绝：{reason}`，模型改道；同意同类型回喂"已放行本类操作"。
- **Day 2 approve 回调收敛（B3 修复）**：写工具内嵌的 `_request_approval`（返回 bool 的 y/n 回调）**移除**——审批统一收敛到 dispatch 前的 `approval.gate` 管线（三选项决策），避免双重审批。`ApproveCallback` 类型与 CLI 的 `make_approve_callback` 一并废弃；工具只负责执行，审批完全由权限管线持有。

### 4.4 会话状态迁移（permission/modes.py，任务 1.1 关联）

```python
class SessionState:   # 从 agent/state.py 迁入，新增 approved_types
    mode, approval_policy, approved_types: set[str]
    enter_build(policy), return_to_plan()   # return_to_plan 清空 approved_types
```

- agent/state.py 改为薄壳 re-export（`from ..permission.modes import SessionState, POLICY_*`），保持 loop/CLI 引用不变，避免大规模改动。
- 新增 `approved_types` 记录"同意同类型"作用域；`return_to_plan` 时清空（策略作用域=本次构建，§5.2）。

### 4.5 工具危险级声明与接入（tools/base.py、files.py、shell.py、search.py）

- **Tool.risk**：新增类属性 `risk: Risk = Risk.SAFE`；`write_file`/`edit_file` 声明 `risk=Risk.WRITE`；`bash` 动态定级（分类器）。
- **files.py**：ReadFileTool/WriteFileTool/EditFileTool 的 `_resolve` 改为注入 `workspace: Workspace`，用 `workspace.classify_path()` 判定风险并生成 `ApprovalAction(kind=file_read/file_write, risk)` 交审批管线；**移除内嵌 `_request_approval` y/n 回调**（B3 修复，审批收敛到 gate）。
- **shell.py**：BashTool 注入 `workspace` 与 `classify`；execute 前 `classify(command, workspace)`：
  - Plan 模式：仅 `Risk.SAFE` 放行；WRITE/DANGEROUS 拦截回喂「当前处于 Plan 模式，该命令会修改状态」（概设 §5.1，任务 1.5）。
  - Build 模式：SAFE 放行；WRITE/DANGEROUS 生成 `ApprovalAction(kind=bash_command, risk)` 交审批管线（1.3/1.4）。
- **search.py**（S9 修复）：GrepTool 同样注入 workspace，路径经 `classify_path` 判定——补上 Plan 结构树遗漏的搜索工具沙箱接入。
- **权限管线挂接点（S5 修复）**：管线挂在 `ToolRegistry.dispatch` 内（base.py），**在工具模式可见性校验之后、参数解析/执行之前**。loop 层调用点不变（`registry.dispatch(call, mode)`）。审批拦截（用户拒绝）返回 ok=False 的 ToolResult，**不计入** base.py 的 ParseCircuitBroken 连续失败计数（用户拒绝是控制信号而非解析错误，避免误熔断）。

### 4.6 审计日志（permission/approval.py 内嵌 AuditLog，任务 1.6）

```python
class AuditLog:
    def __init__(self, path: Path)   # <workspace>/.glaucous/audit.log
    def record(self, event: dict)    # 追加写 JSON 行
```

- 记录：时间、模式、策略、操作类型、目标/命令、风险级、用户决策（同意/同意同类型/拒绝+理由/auto-approve 守卫）、是否放行。
- **审计作用域（S3 修复）**：除用户三选项决策外，**auto-approve 自动放行、Plan 模式 bash 拦截、沙箱逃逸硬拦截（Escape）**也一并记录——权限拦截事件是安全审计关键，FR-16「所有审批决策留痕」应覆盖全部权限决策路径。
- **审计防篡改（S4 修复）**：`.glaucous/` 目录本身纳入**沙箱写排除**——write_file/edit_file/bash 对 `.glaucous/` 内路径一律拒绝（agent 不可篡改审计与会话）。审计路径不对外暴露写能力。
- 概设 §5.3「每次审批决策（时间/工具/参数/用户选择）追加写 `.glaucous/audit.log`」。
- 审计写入失败不阻断主流程（尽力而为，如 JSONL 落盘）。

### 4.7 CLI（cli.py）

- 审批三选项交互：per-action 时弹 `[a] 同意  [b] 同意同类型  [c] 拒绝(附理由)`；破坏性命令陶土红警示 + 命令全文（M3 才 rich 主题，此处纯文本 `⚠` 前缀）。
- 审计日志初始化：`AuditLog(workspace/.glaucous/audit.log)`。
- 工作区沙箱注入：`Workspace(workspace, read_only_extra=config.read_only_extra)` 实例传入各工具与 approval。
- **prompts 拒绝引导（S8 修复）**：ui/prompts.py 补充「写操作被拒绝时，根据拒绝理由调整方案，不要原样重试同一操作」——FR-12 的结构化保障由回喂文案 + 提示词双通道实现。

## 5. 关键设计决策

| 决策点 | 选项 A | 选项 B | 选择 | 理由 |
|--------|--------|--------|------|------|
| 状态归属 | 继续留在 agent/state.py | 迁入 permission/modes.py | B | 概设 §10 明确 modes.py 归属；M1 是权限成型阶段，状态归权限包语义更清 |
| 迁移方式 | 直接改 import 所有引用 | agent/state.py 留薄壳 re-export | B | 避免大范围改 loop/cli/planning 引用；薄壳代价小且后续 M3 可再精简 |
| 沙箱校验位置 | 各工具内自行 resolve+check | dispatch 层统一检查 | B | 单一检查点覆盖所有工具（含未来新增），避免漏检；工具只报 Escape 由 dispatch 转回喂 |
| 区外读是否放行 | 只拦写、读放行 | 区外读写都需单独审批 | B | 概设 §5.4「区外读写任何模式下都走单独审批」；FR-13 区外访问须逐次审批 |
| 分类器无法判定 | 放行 | 保守升级 WRITE | B | 概设 §5.5「无法判定时保守升级为 WRITE 走审批——宁多问不漏放」 |
| 同类型放行粒度 | 按(工具名, 操作类型) | 按(工具名, 操作类型) | B | 概设 §5.3「按工具粒度：edit_file / bash 等」；bash 细分为「只读探测/写命令/破坏性」——DANGEROUS 与区外写不受同类型豁免（§4.3 守卫优先级） |
| auto-approve 例外 | 只拦 DANGEROUS | DANGEROUS + 区外写 | B | FR-10「工作区外访问与破坏性命令仍单独醒目确认」；概设 §5.2 a/b 两条底线 |
| 审计失败处理 | 阻塞 | 尽力而为 | B | 审计是留痕不是门禁，写入失败不阻断任务（与 JSONL 落盘一致） |

## 6. 编码策略决策

按 §0 裁剪声明，本轮全部步骤为 **Code-First（跳过 Test 产出的裁剪变体）**：

| 步骤 | 任务描述 | 策略 | 决策依据 |
|------|---------|------|---------|
| Step 1 | 1.1 Workspace 沙箱（resolve/check/is_within/is_read_only） | Code-First | 路径校验本应 TDD（逃逸边界密集），按用户约束跳过，登记债务 |
| Step 2 | 1.2 CommandClassifier（首词白名单+参数模式表+保守升级） | Code-First | 分类逻辑本应 TDD（正反例矩阵），按用户约束跳过，登记债务 |
| Step 3 | 1.3/1.4 ApprovalPipeline（三选项+同类型+auto-approve 守卫） | Code-First | 审批状态流转本应 TDD，按用户约束跳过，登记债务 |
| Step 4 | 状态迁移 + 工具 risk 声明 + files/shell 沙箱接入 | Code-First | 迁移与属性声明，胶水为主 |
| Step 5 | 1.6 AuditLog + 审批决策审计 | Code-First | 日志类，无复杂逻辑 |
| Step 6 | 1.5 Plan bash 白名单 + CLI 审批三选项 UI 接线 | Code-First | 交互胶水层 |

## 7. 实施步骤

- [ ] Step 1：permission 包骨架 + risk.py 统一枚举 + Workspace 沙箱（workspace.py）（策略：Code-First）
- [ ] Step 2：CommandClassifier 危险分类器（classifier.py，注入 workspace）（策略：Code-First）
- [ ] Step 3：ApprovalPipeline 审批管线（三选项+同类型+守卫）+ SessionState 迁移（approval.py + modes.py + state.py 薄壳）（策略：Code-First）
- [ ] Step 4：工具接入——Tool.risk、files.py 沙箱+移除 y/n 回调、search.py 沙箱、shell.py 分类器、dispatch 权限管线挂接（策略：Code-First）
- [ ] Step 5：AuditLog 审计日志 + `.glaucous/` 沙箱写排除（内嵌 approval.py + workspace.py）（策略：Code-First）
- [ ] Step 6：CLI 接线——审批三选项 UI、Plan bash 白名单、审计初始化、prompts 拒绝引导（策略：Code-First）

## 8. 风险与注意事项

| 风险 | 缓解 |
|------|------|
| Windows 符号链接语义差异（resolve 行为） | FR-34 只要求基本兼容；Linux 一等公民（演示在 WSL2）；路径逃逸校验以 realpath 规范化为准，跨平台语义已在测试债务登记 |
| 分类器漏判危险命令 | 无法判定保守升级 WRITE（宁多问不漏放）；正反例单测登记 M4 |
| 迁移破坏现有引用 | agent/state.py 留薄壳 re-export；迁移后立即语法检查 + 现有调用链（loop/cli/planning）编译验证 |
| auto-approve 守卫误伤正常流程 | 守卫仅针对 DANGEROUS 与区外写；区内普通写自动放行（与概设 §5.2 一致） |
| 沙箱逃逸绕过（symlink、`..`、绝对路径） | realpath 规范化 + 前缀校验 + 符号链接解析三层；M4 单测覆盖逃逸矩阵 |
| 审批交互在非 TTY 场景 | 决策由 CLI 回调注入，无 TTY 时可注入默认策略（自动化场景），工具层不感知 |
| 审计被 agent 篡改（`.glaucous/audit.log` 在区内） | `.glaucous/` 纳入沙箱写排除——write/edit/bash 对 `.glaucous/` 内一律拒绝（§4.6 S4 修复） |
| Windows cmd 命令未在白名单 | FR-34 只要求基本兼容不崩；POSIX 白名单面向 Linux 一等公民；cmd 命令按保守升级 WRITE 走审批兜底（S10） |

## 9. 测试策略

**本轮不产出、不执行任何测试**（用户约束）。测试债务清单（M4 任务 4.1/4.2 偿还，概设 §11 对应文件标注）：

| 债务项 | 应覆盖 | 对应概设测试文件 |
|--------|--------|-----------------|
| 沙箱逃逸矩阵 | `../` 穿越、绝对路径区外、符号链接逃逸、软链指向区外、只读白名单放行、区外读触发审批 | test_workspace_escape.py |
| 分类器正反例 | 白名单只读放行、危险表命中（rm -rf /、git push --force、sudo、curl\|sh）、无法判定保守升级 WRITE、区内写 WRITE、命令内路径区外判定（rm 区外/mv 移出/> 重定向区外）、cmd 命令保守升级 | test_classifier.py |
| 审批三选项 | approve/approve_type/reject 各自状态与回喂；同类型放行后不再询问；拒绝附理由回喂；DANGEROUS 不受同类型豁免仍逐条确认 | test_approval_flow.py |
| auto-approve 守卫 | auto-approve 放行区内写；仍拦 DANGEROUS；仍拦区外读/写（file_read 载体） | test_autoprivilege_guard.py |
| 模式工具暴露矩阵 | Plan 下 write/edit 不可见；bash 两模式注册但 Plan 仅 SAFE；区外读审批链（含 file_read kind） | test_mode_tool_exposure.py |
| 状态迁移 | approved_types 生命周期（enter_build 清空/return_to_plan 清空/同类型累积） | test_plan_build_switch.py |
| 审计日志 | 每次审批决策落盘、含时间/工具/参数/用户选择、自动放行与逃逸拦截入审计、`.glaucous/` 写排除、写入失败不阻断 | — |
| Plan bash 白名单 | Plan 下 SAFE 放行、WRITE/DANGEROUS 拦截回喂 | test_mode_tool_exposure.py |
| 审批拦截不计熔断 | 用户拒绝返回 ok=False 但不计入 ParseCircuitBroken 连续失败计数 | test_loop_termination.py |

M1 验收（场景 A 每次审批 / 场景 C 全放行仍拦危险命令）按用户约束**不在本轮执行**，留待环境就绪后由用户自行验证。
