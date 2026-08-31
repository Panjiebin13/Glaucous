# Spec 一致性评审报告：V1.1-M4 Checkpoint Spec（Git 快照 / 保留策略 / /rollback / 拒绝联动回退）

> 评审日期：2026-08-31 10:20
> 评审对象：`docs/designs/202608310900-plan-v11-m4-checkpoint.md`（状态：草稿）
> 对照文档：`docs/编程智能体需求文档v1.1.md`、`docs/编程智能体概要设计说明书v1.1.md`、`docs/Glaucous开发计划表v1.1.md`
> 模式：**聚焦复审（r2，非全量重评）**（改动范围：B1/B2/B3 修复章节 + S1~S9 修复章节及其波及面）
> 上一轮报告：`docs/reviews/202608310900-spec-review-v11-m4-checkpoint.md`（不通过：阻塞 B1/B2/B3 + 建议 S1~S9）
> 结论：**不通过**（阻塞 2 项，建议 2 项）

## 一、评审范围（聚焦复审）

- 本轮仅复查声明修复的章节及其波及面：B1（§3.1 A 项来源说明、§六 2 同步表述）、B2（决策 2/4、§一 接线图与「checkpoint 外泄路径」、分层表 AgentLoop on_checkpoint 行、§四 代码块重写、§3.5 生命周期）、B3（§二 anchor_digest 字段、§3.3 双参 + ContextAnchorMismatch、§3.4/§五/§七 同步）、S1~S9 对应改动。
- 波及面重查（改动触及概设核心机制，按规则无论章节是否改动均复查）：FR-40~43 达成路径、概设 §5.1「新增文件列入将移除清单」与「配对校验」、概设 §5.2 拒绝联动与非 Git 语义、需求 §5 约束 6（git 子进程、零新依赖）、**§3.4 与 §3.5 对 GitError/ContextAnchorMismatch 的处理口径一致性**（任务指定重点）、B2 外泄链与 B3 锚校验的内部一致性（任务指定重点）。
- 未改动章节（任务 4.1~4.5 逐条落实、模块落位、config 扩展可行性、交互协议先例核对、范围裁剪声明、头部要素与链接可达性之外的部分）**继承上一轮结论**。
- 链接可达性复查：三份上游文档相对链接（`../编程智能体需求文档v1.1.md` 等）均存在可达。

## 〇、上轮阻塞项处置

| 编号 | 处置 | 说明 |
|---|---|---|
| B1 | **已修复** | §3.1 增「A 项来源」段：`git diff --name-status <ref>` 只覆盖已跟踪文件 M/D，A 项另行计算 = `git ls-files` ∪ `git ls-files --others --exclude-standard` − `git ls-tree -r --name-only <ref>`，与「A = 工作树有、快照树无」（§六 2）语义一致；`ls-files`（含 index 中已 add 的新文件）∪ 非 ignored untracked 恰好覆盖「checkpoint 之后新建文件」全集，M/D 仍由 diff 产出。§七 用例 1（B 被移除）、计划表验收「回退后 git status 干净」由此可达成。机制自洽。 |
| B2 | **已修复** | 外泄链五环节全部落地且跨章节一致：①create 成功 → `store.last_created` 更新（§3.2/§四）；②loop 调 `on_checkpoint(cp)`（§四 代码块 L164-165）；③主 loop 接线 `lambda cp: setattr(ctx, "turn_checkpoint_seq", cp.seq)`（§一/决策 2/§四），loop 不持有 ctx 职责分离声明明确；④读取方 = 审批卡提供条件 `ctx.turn_checkpoint_seq is not None`（§3.5）；⑤生命周期「轮开始置 None → run 入口写入 → 轮末 finally 清理」（§一/§3.5/分层表 cli.py 行三处一致）。创建失败/非 Git/子 agent 的退化路径（不提供第四选项）亦闭环。 |
| B3 | **已修复** | 锚机制成立：anchor = 创建时刻 `messages[-1]`（即 `messages[count-1]`）的 sha256[:12]，L2 压缩对 `messages[:split]` 的任何原位替换都会使命中失败 → `truncate_to(count, anchor_digest)` 双参校验 + `ContextAnchorMismatch` 显式拒绝，消除静默 no-op/错位截断；「单一机制覆盖任意深度」的声明在显式拒绝路径下成立。§二/§3.3/§3.4/§七 用例 7 表述贯通。落实概设 §5.1「配对校验」要求。**但数据载体存在断链，见 B5。** |

## 二、阻塞问题

### B4. §四 参考实现在首轮空历史下必然失败：会话首个任务永远无 checkpoint，FR-40「每轮入口」出现系统性缺口

- **维度**：需求一致性（FR-40 达成缺口）＋ 结构与可执行性（参考实现与自身测试计划内部矛盾）
- **spec 位置**：
  - §四 代码块（本轮 B2 修复重写）：
    ```python
    cp = self._checkpoint_store.create(
        task,
        message_count=len(self._history.messages),   # push_user 之前（决策 3）
        anchor_digest=history_digest(self._history.messages[-1]),
    )
    except Exception:   # noqa: BLE001 —— 兜底设施失败不阻断任务轮
        cp = None
    ```
  - §七 用例 9：「FakeLLM 单步任务，断言 store.last_created 更新且 on_checkpoint 收到 seq、message_count 为 push_user 前长度」
  - 决策 3：「checkpoint 索引记录 `message_count`（创建时刻 `len(history.messages)`，即本轮入口）」
- **上游位置**：需求 §2.2 FR-40：「**每轮**用户任务开始前自动创建 checkpoint（基于 Git 快照……）」；计划表 M4 验收：「场景 H 可复现；回退后 `git status` 干净」
- **冲突说明**：新会话（及 /clear 后）的首个任务轮，`history.messages == []`，§四 代码在**参数求值阶段**即于 `messages[-1]` 抛 IndexError——异常发生在 `store.create` 被调用之前，被 `except Exception` 吞掉 → `cp = None`。后果链：①**每会话第一个任务恰好没有 checkpoint**（该时刻工作区最接近原始状态、恰是最需要回退面的时刻），FR-40「每轮……自动创建」出现按轮次规律复现的缺口，且 warning + 审计噪音每次新会话首轮必现；②首轮任务中触发审批时 `ctx.turn_checkpoint_seq` 恒为 None → §3.5 第四选项永不出现 → FR-43 在该场景失效；③§七 用例 9 若按最小 fixture（空 History + FakeLLM 单步）执行，「断言 store.last_created 更新」**必然失败**——spec 的参考实现与自身测试计划矛盾，实现者无法判断应修代码还是改测试前提；④对比之下 §3.1 对「空仓库 head=None」分支（read-tree 空树、commit-tree 省 -p）都做了显式设计，唯独空历史首轮未覆盖，属设计留白而非有意裁剪（§八声明「无裁剪」）。
- **修复方向**：定义空历史首轮语义（二选一，不可留白）：a) 首轮 anchor 取哨兵值（空串/None）照常创建 checkpoint 并落库，§3.3 补 count=0 的锚校验分支（空历史 ↔ 空锚为合法配对，truncate 到 0 = 清空对话）；b) 显式声明首轮豁免为有意设计，并说明 FR-40「每轮」口径收窄的正当性，同步修订 §七 用例 9 的 fixture 前提（预置历史）与 §八 范围声明。方向 a 与 FR-40 原义一致，优先建议。

### B5. `Checkpoint` 数据类缺 `anchor_digest` 字段，§3.4 调用 `cp.anchor_digest` 悬空（B3 修复的内部一致性缺口）

- **维度**：结构与可执行性（规范性接口定义不完整 → 章节间内部矛盾）
- **spec 位置**：
  - §3.2 数据类（规范性接口定义）：`class Checkpoint: seq: int; ref: str; commit: str; created_at: str; task: str; message_count: int`（**无 anchor_digest**）
  - §3.4 第 5 步：「是 → `history.truncate_to(cp.message_count, cp.anchor_digest)`」
  - 佐证贯通面：§二 索引行含 `"anchor_digest": "a1b2c3d4e5f6"`；§3.2 create 签名 `create(self, task, message_count, anchor_digest)`——锚的**入库**与**校验时使用**两环节均已定义，唯独内存载体缺失
- **上游位置**：概设 §5.1 第 4 步：「（JSONL 同步截断，**配对校验复用 load 的修复逻辑**）」——校验所需锚必须随 checkpoint 对象持久化并可回读到调用点
- **冲突说明**：锚机制三环节中「创建时入库（§二 ✓）→ 校验时使用（§3.3/§3.4 ✓）」已闭环，但 `store.create` 收下 `anchor_digest` 后在 `Checkpoint` 对象上**无处安放**（数据类无此字段），从索引行构造 Checkpoint 时字段丢失；§3.4 按 spec 落码即在调用处 AttributeError。规范性数据类（§三 接口定义章节）与规范性调用方之间的字段断链，属于影响实现判断的内部矛盾（与上轮 B2「ctx 字段无写入路径」同类）。本轮对 `anchor_digest` 全文 12 处出现点逐一核对，仅数据类定义一处缺漏。
- **修复方向**：§3.2 数据类补 `anchor_digest: str` 字段（一行改动），并在 §3.2 注明 create 成功构造 Checkpoint 时载入该字段（创建时来自入参、重建时来自索引行）；顺带核对 §七 用例 7 锚断言的取值来源与 §3.4 调用一致。

## 三、建议问题

### S10. §五 与 §3.5 对非 Git 工作区「拒绝并回退」的降级口径仍差半步（上轮 S2 残余子项）

- **维度**：结构与可执行性（口径统一）
- **位置与摘录**：§五「非 Git 工作区 | store.available=False（缓存）；**/rollback 与「拒绝并回退」降级为不可用提示**……」；§3.5「提供条件：……（**非 Git/创建失败/子 agent 均不提供，退化为三选项现状**）」
- **建议**：非 Git 下审批卡根本没有第四选项、不存在提示时机，「降级为不可用提示」易被实现为在卡上附加提示行。建议统一为「不提供该选项；不可用提示仅出现在 /rollback（`unavailable_reason()`）与首次创建失败 warning 两处」。上轮 S2 的主诉求（提示时机声明）本轮已修复，此为残余表述问题。

### S11. 「索引损坏/缺失 → 空索引重新起步」仍无对应用例（上轮 S6 部分残余）

- **维度**：结构与可执行性
- **位置与摘录**：§五「索引损坏/缺失 | 空索引重新起步（ref 残留无害；`update-ref -d` 幂等）」；§七 用例 1~9 无对应项
- **建议**：上轮 S6 明确请求的两类错误用例中，「回退 git 失败」已补（用例 6 restore GitError、用例 8 降级普通拒绝），「索引损坏重建」缺失。建议补一条：损坏 JSON → 空索引起步 → create/list 正常、Git ref 不受影响。成本极低，可随 r3 顺带处理。

## 四、通过项（仅本轮实际复查项；未列项继承上轮）

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| 上轮阻塞处置 | B1 修复：A 项 = `ls-files` ∪ `ls-files --others --exclude-standard` − `ls-tree -r <ref>`，§3.1/§六 2/§七 用例 1 三处表述一致，「将移除」清单与 unlink 循环从此有真实输入 | ✓ |
| 上轮阻塞处置 | B2 修复：外泄链（create→last_created→on_checkpoint→ctx.turn_checkpoint_seq→审批卡读→finally 清）写入主体明确、五处章节表述一致，决策 4 闭环 | ✓ |
| 上轮阻塞处置 | B3 修复：锚机制对 L2 压缩所有改写形态（含 split 跨越截断点）均能检出，ContextAnchorMismatch 显式拒绝，静默 no-op 已消除；双参贯通 §3.2/§3.3/§3.4/§七 | ✓（数据载体断链见 B5） |
| 需求一致性 | FR-42 改动面：变更清单确认卡（A 项现已可产出）+ 上下文二问默认否 + 错位场景显式提示「本次仅回退文件」 | ✓ |
| 需求一致性 | FR-43 改动面：第四选项经外泄链闭环，提供条件覆盖非 Git/创建失败/子 agent 退化 | ✓（首轮场景受 B4 影响） |
| 需求一致性 | 硬约束波及重查：需求 §5 约束 6——快照五步与回退全程 subprocess 封装、零新依赖；创建不触碰用户工作树/真实索引/stash | ✓ |
| 概设一致性 | 概设 §5.1「checkpoint 之后新增的文件 → 列入将移除清单」机制现已可达成；「配对校验」已落实（锚校验 + 双参签名） | ✓ |
| 概设一致性 | **§3.4 与 §3.5 的 GitError/ContextAnchorMismatch 处理口径**（任务指定重点）：/rollback 路径 GitError → 报错保持现状 + `git status` 提示 + 不继续二问；审批卡路径 GitError → 降级普通拒绝 + 提示——两条路径分属「确认后的显式操作」与「拒绝语义附加动作」，差异各有理由且均标注 S5；ContextAnchorMismatch 仅存在于 /rollback 路径（审批卡路径只回文件不 truncate，决策 4 明示「只回文件不动上下文」），提示文案与 §3.3 逐字一致。无矛盾 | ✓ |
| 概设一致性 | **B2 外泄链内部一致性**（任务指定重点）：决策 2/4、§一 接线图与外泄路径、分层表（AgentLoop on_checkpoint 行、cli.py 轮开始置 None/轮末 finally）、§四 代码块、§3.5 生命周期五处交叉核对，写入时机（轮开始置 None 先于 run 入口写入）、读取方、清理方均无歧义；子 agent 不注入 store 与 §3.5 提供条件自洽 | ✓ |
| 概设一致性 | S2 修复：非 Git 提示时机显式声明（启动不探测不告警，首次创建尝试失败 warning 一次，FR-40 口径落地） | ✓（残余表述见 S10） |
| 概设一致性 | S3 修复：决策 7 显式登记 auto_rollback_on_reject 与「可关」开关不在本批 + TODO 偿还指向 M6 + env 替代 config.toml 理由（M3 既有口径）；§八 同步 | ✓ |
| 概设一致性 | S7 修复：决策 1 修正声明覆盖概设 §5.2 性能行与计划表 4.1 措辞；§3.1 空仓库分支补明（head=None → read-tree 空树、commit-tree 省 -p） | ✓ |
| 结构与可执行性 | S1 修复：分层表补 `permission/approval.py` 行（Literal 扩展 + gate 分支 + 审计 decision 取 choice 原样） | ✓ |
| 结构与可执行性 | S4 修复：箭头形态统一四选项（保留「同意同类型」，对齐现网 L359 三选项 + 新增第四项），符合 r2-S3「呈现不分列」裁决；数字形态增 [d]、[b] 维持现状 | ✓ |
| 结构与可执行性 | S5 修复：决策 4/§3.4/§3.5 三处 GitError 分支齐备，§七 用例 8 覆盖降级路径 | ✓ |
| 结构与可执行性 | S6 修复：测试拆分为 `test_checkpoint_git.py` + `test_rollback_context.py`，与概设 §11 清单命名一致；五类错误面用例补齐（restore GitError、failed_remove、锚不匹配、truncate 写失败、create 异常任务照常） | ✓（残余见 S11） |
| 结构与可执行性 | S8 修复：§3.1 gitignored 语义显式设计（快照不捕获、A 项经 --exclude-standard 排除）+ §七 用例 5；与 `.glaucous/` 排除逻辑（§六 2）不冲突 | ✓ |
| 结构与可执行性 | S9 修复：全文经检索确认无 `checkpoint_id`/`turn_checkpoint_id` 残留（0 处），统一为 `turn_checkpoint_seq`，并在决策 2 声明「Checkpoint 无 id 字段，全用 seq」 | ✓ |
| 继承上轮 | 任务 4.1~4.5 逐条落实、模块落位、无范围蔓延、§3.3 JSONL 落盘格式吻合、config 扩展可行性、交互协议先例（select_with_arrows/live_hooks/rebuild_loop）、头部要素 | 继承上轮（✓） |

## 五、复审要求

- 结论为**不通过**：必须修复 **B4、B5** 后进行 r3 复审。两项均为机械性小改（B4 定义空历史首轮语义、B5 数据类补一字段），但 B4 触及 FR-40 首轮达成与 §七 用例 9 可执行性、B5 是 B3 锚机制的数据载体断链，均属阻塞级。
- S10/S11 登记待办，不作为放行前置条件，建议随 r3 顺带处理。
- r3 建议仍采用聚焦复审：仅验证 B4/B5 修复及其对 §3.3（count=0 分支）、§3.4、§四、§七（用例 9 fixture）的波及面。
