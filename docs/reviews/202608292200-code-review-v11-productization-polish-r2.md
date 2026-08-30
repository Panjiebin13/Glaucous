# 代码评审报告：v1.1 前置产品化打磨（第 r2 轮）

> 评审日期：2026-08-29 22:00
> 评审对象：spec `docs/designs/202608291800-plan-v11-productization-polish.md`（评审通过版）；代码为提交 `f6b0f78`（r1 修复，父提交 `9142392`）
> 模式：聚焦复审（改动范围：r1 四项处置 —— r1-B1/r1-S1/r1-S2/r1-S3）
> 结论：**不通过**（阻塞 1 项，建议 3 项）

## 评审方式说明

除静态审读外，执行如下只读式运行验证（PowerShell，PYTHONPATH=src，未改任何源码）：

- `git show f6b0f78`：改动仅 4 文件（新增 r1 报告、models.toml.example 删 1 行注释、SKILL.md 删 8 行改 1 行、cli.py +11/-2），未触及其他文件；
- `python -m pytest tests/ -q` → **112 passed, 1 skipped**（基线 67+1 无回退）；`python -c "import glaucous.cli"` 通过；
- 直调 `_thinking_line('budget', ...)` 复现渲染输出（证据见阻塞 B1）。
## 一、阻塞问题

### B1. budget 渲染新分支把 payload.percent（0~1 比例）直接当百分比数字输出，展示值与实际差 100 倍

- **维度**：逻辑正确性（r1-S1 修复引入的新偏差）
- **代码位置**：
  1. cli.py:533 `f"[{level_style}]  {ring} ctx 占用 {payload.get('percent', 0)}%"`（render_event budget 分支）；
  2. cli.py:575 `f"◔ ctx 占用 {payload.get('percent', 0)}%（…tokens）"`（_thinking_line budget 分支）。
- **证据**：budget 事件 payload 的 percent 为 0~1 比例：
  - budget.py:78 `percent = used / limit if limit > 0 else 1.0`；
  - loop.py:241 发射 `"percent": round(report.percent, 4)`；
  - 复现（used=54000、limit=128000，真实占用 42.19%）：`_thinking_line('budget', payload)` 返回 `'◔ ctx 占用 0.4219%（54000/128000 tokens）'` —— 展示为 0.42%，与实际相差 100 倍，且与同行圆环字符自相矛盾（◔ 由 `ctx_ring(ratio)` 按四分位正确算出约 42%）；
  - render_event 分支（/expand 重放与折叠关/管道实时打印路径）同一缺陷；折叠开时思考区动态行同样中招。budget 事件每轮末必发（loop 发射），该错误展示必现；
  - 注：`ctx_ring(payload.get("percent", 0.0))` 传参本身正确（接收比例），仅文案拼接错。
- **与 spec 关系**：r1-S1 的诉求为「/expand 可见、可读摘要」；现摘要可见但数值必错，属修复引入的逻辑缺陷。
- **修复方向**：文案处先换算百分比数字（如 `round(percent * 100)`），render_event 与 _thinking_line 两处同改；ctx_ring 入参维持原比例。建议补一条 budget 渲染文案断言（载荷 percent 为 0~1 比例）防回退。

## 二、建议问题

### S1. _thinking_line 压缩失败分支文案被顺手改动，超出修复声明范围且与同事件另一路径文案分叉

- **维度**：改动范围管理
- **代码位置**：cli.py:573 `"🌊 潮水仍不回，继续精简对话"`（原文案为「🌊 潮水不退，继续精简对话」）。
- **说明**：r1-S1 处置声明仅为新增 budget 分支，此行却同步改为「潮水仍不回」；而 render_event 同事件分支（cli.py:527）仍为「潮水不退，继续精简对话」——同一压缩失败事件在思考区折叠行与 /expand 重放中措辞不一致。全库检索无该文案测试断言，不影响回退基线。建议回改为「潮水不退」保持两路径一致。

### S2. _thinking_line 的 budget 行圆环字符硬编码 ◔，与 render_event 的动态圆环不一致

- **代码位置**：cli.py:575 行首硬编码 `◔`。
- **说明**：theme.ctx_ring 按占用四分位取形（○/◔/◑/◕/●，theme.py:221），低占用（如 10%）应为 ○；硬编码 ◔ 使折叠行与 /expand 重放（render_event 用动态圆环）形态不一致。建议复用 `ctx_ring(percent)` 取形，两路径同口径。

### S3.（可选）SKILL.md description 要点下括注仍保留

- **代码位置**：SKILL.md:25 「（不要写成"这是一个技能"这类无信息描述）」。
- **说明**：r1-B1 代码位置曾列出该项，但 r1 复验方式仅明示删「正文用中文书写…」与「## 约束」两项；该括注系对 spec §1.2 要点 4 原文「写成明确触发场景句」的反例注脚，未新增规范性要求，判在边界内、不构成阻塞；如追求与七要点逐字严格对应可一并删除，提请作者知悉。

## 三、通过项（r1 四项处置逐一验证）

| 处置 | 验证 | 结果 |
|---|---|---|
| r1-B1：附加条目删除 | 「正文用中文书写…」一行与「## 约束」整段（3 条）已删，全文 28 行无残留（git diff 与通读双证） | ✓ |
| r1-B1：frontmatter | name: create-skill；description 与 §1.2 示例句逐字一致；不引入新工具/新代码路径（§1.3 不变量） | ✓ |
| r1-B1：正文恰好覆盖 §1.2 七要点 | ①确认用途与触发场景、信息不足用 ask_user（L10）②小写连字符命名（L11）③固定路径 .glaucous/skills/<name>/SKILL.md、绝不写工作区之外（L12）④frontmatter 模板含 name 与 description、正文为指令（L13-25）⑤写文件仅 Build 模式、Plan 须先声明进入 Build（L12）⑥复读自校验（L26）⑦下次 /clear 或重启后生效（L27）；无要点之外的规范性段落 | ✓ |
| r1-S1：budget 渲染分支 | render_event 与 _thinking_line 均已增 budget 分支，载荷字段 used/limit/percent 与发射端一致（loop.py:238-242），render_event 经 theme.ctx_ring 圆环渲染——分支存在性成立；数值文案缺陷见阻塞 B1 | ⚠ 见 B1 |
| r1-S2：模板注释对齐 | models.toml.example 第 3 行注释已删，现与 spec §4.1 字面模板逐字一致（两行注释 + 两档案段、仅 api_key_env、无明文密钥） | ✓ |
| r1-S3：提问卡箭头条件 | cli.py:254 `len(options) >= 2 and _arrow_mode()`，对齐 §6.2 通用触发条件「选项数 ≥2」；单选项回落数字输入卡，越界回喂/空回答等既有行为不受影响 | ✓ |
| 不回退、无新波及 | 112 passed 1 skipped；import glaucous.cli 通过；diff 仅 4 文件，r1 已确认的 R3 时序/R5 口径/R6 契约/R4 安全结论均未被触及 | ✓ |

## 四、复审要求

1. **B1（必须）**：修正 budget 文案中 percent 的百分比换算（如 `round(percent * 100)`，render_event 与 _thinking_line 两处），复跑 `python -m pytest tests/ -q` 保持全绿（≥112 passed，1 skipped）后复审。
2. S1（文案回改）、S2（圆环取形同源）为建议级，可随批处理；S3 仅提请作者知悉，均不阻塞。

（报告完）