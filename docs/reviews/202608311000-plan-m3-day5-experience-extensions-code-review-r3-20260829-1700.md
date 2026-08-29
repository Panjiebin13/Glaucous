# 代码评审报告：Glaucous M3 Day5 体验与扩展（第 r3 轮 · 分支合并复审）

> 评审日期：2026-08-29 17:00
> 评审对象：spec docs/designs/202608311000-plan-m3-day5-experience-extensions.md（m3-day5 功能基准）+ docs/designs/202608311000-design-m3-day5-cli-console-migration.md（M3-UI UI 基准）
> 模式：聚焦复审（合并提交 f6acdea = merge M3-UI[d85c5ed] into feature/m3-day5[b42de12]）
> 结论：**不通过**（阻塞 2 项，建议 5 项）

## 〇、评审范围与方法

- **合并改动清单**（git diff --ignore-cr-at-eol 排除行尾噪音后的真实变更）：
  - 融合核心：src/glaucous/cli.py（798 行深度融合版）、src/glaucous/theme.py（M3-UI 新增）、TODO.md、pyproject.toml、requirements.txt
  - m3-day5 保留：commands.py、llm/registry.py、extensions/skills.py、extensions/init_draft.py、tools/skill_tool.py、ui/renderer.py、ui/theme.py
  - M3-UI 带入的行为增量：agent/loop.py（+20 行 compressed 事件发射）+ tests/test_compression_event.py
- **波及面核对**：commands.py 全部 ctx.renderer.* 调用点（grep 逐点）、cli 反向引用（rebuild_loop/resume_history/sanitize_input）、build_system_prompt skills 参数、MemoryStore.entries/remove、Tool.risk 默认值
- **方法**：静态逐条对照两份 spec + 概设 §9/§10；只读运行验证（pytest / 导入冒烟 / 管道端到端 / rich markup 实测），未修改任何源码
- **既定合并决策**（不判问题）：UI 采用 M3-UI 实现（ui/theme.py 与 cli 主题形态不一致属预期）；依赖声明以 M3-UI 侧为准（TODO.md 已声明）；功能保留 m3-day5

## 一、阻塞问题

### B1. prompt_toolkit 顶层导入，m3-day5 spec §4.3 降级条件「导入失败不拒启动」失效
- **维度**：Spec 符合性（m3-day5 plan §4.3 降级路径，标注「（必须）」）
- **代码位置**：
  - src/glaucous/cli.py:63-66：from prompt_toolkit import PromptSession / WordCompleter / HTML / FileHistory（模块顶层，无保护）
  - src/glaucous/theme.py:19：from prompt_toolkit.styles import Style（主题单一出口模块同样顶层依赖）
  - src/glaucous/cli.py:634-647：make_prompt_session 内部 from prompt_toolkit import PromptSession as _PS 的 try/except 仅能捕获构造失败
- **spec 位置**：m3-day5 plan §4.3「**降级路径（必须）**：以下任一成立回退 input()（sanitize_input 净化，行为与 Day3 一致）——① stdin 非 TTY（测试/管道）；② **prompt_toolkit 导入失败（依赖损坏不拒启动）**；③ GLAUCOUS_INPUT=plain」
- **冲突/缺陷说明**：合并采用 M3-UI 的顶层导入形态后，prompt_toolkit 缺失/损坏时 import glaucous.cli 在第 63 行即 ImportError，CLI 直接拒启动——降级条件②整体失效；而 make_prompt_session 的 docstring（cli.py:626-629）仍声称「③ prompt_toolkit 导入/构造失败（依赖损坏不拒启动）」，名实不符。对照 m3-day5 原实现（b42de12:cli.py）为函数内延迟导入 + except ImportError: return None，条件②真实可用；合并把该行为丢掉了。
- **提请作者确认**：M3-UI 基线（d85c5ed）本身即顶层导入（其设计文档仅要求非 TTY 降级闸门），若「UI 采用 M3-UI」的合并决策视为对此条豁免，应同步修订 m3-day5 spec §4.3 并登记；否则按下述方向修复。
- **修复方向**：theme.py 将 PT_STYLE 构造包入 try（prompt_toolkit 缺失时置 PT_STYLE = None）；cli.py 移除顶层 prompt_toolkit 导入（HTML 等一并移入函数内），make_prompt_session 内延迟导入并 except ImportError: return None——约 10 行改动，M3-UI 渲染形态不受影响。

### B2. /view 渲染链路动态内容未 escape——文件名与文件内容 markup 吞字、显示失真
- **维度**：Spec 符合性（M3-UI 设计文档红线「动态内容统一 escape、防 markup 注入（含模型可控文本）」；任务重点检查项 5）
- **代码位置**：
  - src/glaucous/theme.py:133-135（render_markdown_doc）：make_card(title) -> add_column(header=title)，title 未 escape
  - src/glaucous/theme.py:156-158（render_text_doc）：title 未 escape + table.add_row(text)——文件全文未 escape
  - src/glaucous/theme.py:180-182（render_csv_doc）：title 未 escape + table.add_row(*[c ...]) 单元格未 escape
  - src/glaucous/cli.py:425：title = f":book: {rel}"（rel 为用户/模型可控的文件路径）
- **spec 位置**：M3-UI 设计文档「红线核查——安全：动态内容统一 escape、流式正文关闭 markup」；该文档将同类问题 B-02（审批卡 action.target 未 escape）定为**阻塞事项**并已修复——本次遗漏点与之同性质
- **冲突/缺陷说明**（只读实测证据，rich 对未闭合标签不报错而是**吞字**）：
  - render_text_doc(':book: a[b].txt', 'plain') 渲染为「📖 a.txt」——[b] 被吞，文件名失真；
  - render_text_doc('t', 'line1 [b] line2') 渲染为「line1  line2」——正文被吞改；
  - render_csv_doc('t', 'a,[b],c') 单元格 [b] 被吞为空。
  触发路径：/view 打开 txt/csv/md 文件（txt/csv 内容含 [x] 形态极常见：代码数组、日志标签、Markdown 链接）与 agent 路径 read_file 打开 .md（cli.py:361 _render_md_tool_end -> render_markdown_doc(f":book: {rel}", ...)）。rich Table 对 str 单元格与 header 默认解析 markup，「查看文件」功能会静默展示与原文不符的内容。对比同文件 render_code_doc（theme.py:147 escape(title)）与 cli.render_event（各事件均已 escape）处理正确，属遗漏而非统一策略。
- **修复方向**：三处 title 与 render_text_doc 正文、render_csv_doc 单元格统一走 rich.markup.escape()（或以 Text(x, markup=False) 传入）；Markdown 正文经 Markdown() 渲染天然安全，无需处理。

## 二、建议问题

### S1. /help 与 PT 补全未收录 /view
- **代码位置**：src/glaucous/commands.py:43-57（HELP_LINES 命令目录）、src/glaucous/cli.py:104-107（SLASH_COMMANDS 14 个）
- **spec 位置**：M3-UI 设计文档跟进六「/view 文件渲染……REPL 输入循环与 /exit /quit 并列分发」（已落地功能）
- **说明**：/view 已实现（管道实测可用），但 /help、未知命令兜底列表与 WordCompleter 均不含，用户无从发现。建议三处同步收录。
- **修复方向**：HELP_LINES / SLASH_COMMANDS 增补 /view 条目。

### S2. /exit 双路径死代码与告别文案分叉
- **代码位置**：src/glaucous/cli.py:732-734（内联 /exit、/quit 打印 :waving_hand: 短代码告别并 return）；src/glaucous/commands.py:339-341、386-387（_cmd_exit 打印 🌅 再见。——经内联拦截后不可达）
- **spec 位置**：m3-day5 plan §4.3 命令表「/exit /quit …… 文案 🌅 再见。」
- **说明**：内联路径生效后，handle_command 的 /exit 分支为不可达死代码，且两条路径的告别意象分叉（waving_hand 与 sunrise）。文案差异属「UI 采用 M3-UI」预期范畴，不判阻塞；但死分支与 m3-day5 spec 字面（sunrise 意象）并存易误导后续维护。
- **修复方向**：将 handle_command 的 /exit、/quit 死分支清理掉，或恢复 🌅 文案并同步修订 spec §4.3。

### S3. rich 依赖上限丢失（m3-day5 spec §4.7 字面 rich>=13,<14）
- **代码位置**：pyproject.toml:18、requirements.txt:10：rich>=13.7（无上限）
- **spec 位置**：m3-day5 plan §4.7「依赖新增：rich>=13,<14、prompt_toolkit>=3,<4」
- **说明**：合并采用 M3-UI 侧声明（TODO.md 已登记「依赖声明以 M3-UI 侧为准」），prompt_toolkit 约束一致，但 rich 的 <14 上限被丢弃。当前 rich 13.x 稳定，风险低；作为 spec 字面偏离登记。
- **修复方向**：恢复 rich>=13.7,<14，或修订 spec §4.7 并登记。

### S4. M3-UI 已登记修复项在融合版中现状未变（B-01/S-01/S-02）
- **代码位置**：src/glaucous/cli.py:245（拒绝理由 console.input 无 EOFError/KeyboardInterrupt 保护——EOF 落「本轮执行失败」兜底而非视为 reject，M3-UI 文档 B-01）；cli.py:284（render_event 的 state 形参未使用，S-01）；cli.py:195-197（ask 越界序号原样回喂模型，S-02）
- **spec 位置**：M3-UI 设计文档「剩余待办：B-01、B-03、S-01、S-02——已登记 TODO.md 3.2r」
- **说明**：三项均为 M3-UI 分支既有待办（非本次合并引入，TODO.md 3.2r 已登记），聚焦复审确认其在融合版中依然存在；建议随 3.2r 待办在 M4 偿还，避免融合后遗失线索。
- **修复方向**：按 TODO.md 3.2r 清单偿还（B-01 优先——影响审批语义）。

### S5. Banner 无模型名/模式占位（r1 S7 合并后偏离扩大）
- **代码位置**：src/glaucous/cli.py:81-90（render_banner：三行固定文案卡片）
- **spec 位置**：m3-day5 plan §4.2「Banner……去除现行版本字样，**改为当前模型名与模式占位**」；M3-UI 侧 ui/renderer.py:145-153 的 banner() 反而含「当前模型：{model_name}」
- **说明**：合并采用 M3-UI cli 的 Banner 形态后，连原 m3-day5 Banner 的模型名也不再显示（r1 评审建议 S7 仅登记「缺模式占位」，合并后状态加重）。属 UI 形态预期合并结果，登记提醒 M4 视觉验收时处理。
- **修复方向**：Banner 第三行接 ctx.current_model 与模式段（数据已就绪：ctx.current_model + prompt_mode）。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | ThemeRenderer 适配器完整覆盖 commands.py 对 renderer 的全部调用面（note/info/error/console/model_name/last_budget/render_budget_report/retry 八成员，grep 逐点核对无遗漏；render_budget_report 三参位置传参签名匹配） | ✓ |
| Spec 符合性 | /view 内联分派先于 handle_command（task == "/view" 或 "/view " 前缀），不落未知命令兜底；/exit 内联先拦截，无双路径冲突 | ✓ |
| Spec 符合性 | 斜杠命令全集 14 个：handle_command 分派、HELP_LINES、WordCompleter 三处一致；未知命令打印可用列表、不误发 LLM（管道实测） | ✓ |
| Spec 符合性 | /model 切换链路：ping 上限 15s（PING_TIMEOUT=15.0）-> resolve_profile -> llm.switch_profile -> ctx.current_model 与 renderer.model_name 更新 + 审计 model_switch；未知名列可用、失败保持原档案；列表路径零网络 | ✓ |
| Spec 符合性 | registry：api_key 明文硬拒绝（RegistryError）、文件缺失环境变量兜底（env 单档案）、GLAUCOUS_DEFAULT_MODEL 非法段名报错、config.load_config 经 registry 接线且 load_profile 保留 | ✓ |
| Spec 符合性 | 技能链路：三层扫描同名覆盖（builtin -> global -> project）、frontmatter 畸形跳过 + warnings、build_system_prompt 增 skills 参数 + BASE_PROMPT load_skill 引导、load_skill risk=SAFE（实测 Risk.SAFE）、未知名回喂可用清单、空注册表回喂提示 | ✓ |
| Spec 符合性 | 会话重建：/clear（skills.scan -> system_prompt 现读 -> History.create -> SessionState() -> rebuild_loop）、/resume（复用 resume_history：latest / 前缀模糊匹配）整体替换后回调经 ctx 间接引用（confirm 读 ctx.state、on_event 读 ctx.stream_state/last_budget，闭包不捕获旧对象，D8 合规） | ✓ |
| Spec 符合性 | /compact（trim -> compact -> 前后 token 估算 -> L2 失败提示 -> render_budget_report -> last_budget 缓存）、/memory add/del（去重提示/越界报条数）、/rules、/skills、/init（已存在不覆盖）、/stop、审计事件齐全 | ✓ |
| Spec 符合性 | M3-UI UI 约束：色板单一出口（theme.py 常量 -> THEME/PT_STYLE 同源派生）、流式正文 markup/emoji 关闭、render_prompt_header escape(model_name)、ctx_ring 三档阈值自 budget 导入 | ✓ |
| Spec 符合性 | loop compressed 事件 payload（stage/ok/used/limit/percent）与 render_event compressed 分支消费匹配；test_compression_event.py 随合并保留并通过 | ✓ |
| 逻辑正确性 | 降级路径 ①GLAUCOUS_INPUT=plain、②非 TTY（stdin/stdout 任一）实测生效；console.input 与旧 input() 语义一致（M3-UI 文档实测留档）；条件③导入失败见阻塞 B1 | ✓（③除外） |
| 逻辑正确性 | escape 面：ThemeRenderer note/info/error、render_event 全事件、render_prompt_header、_cmd_view 提示行、make_decision_callback(action.target)、make_ask_callback(option) 均已 escape；Markdown 正文天然安全 | ✓ |
| 逻辑正确性 | 运行验证（PYTHONPATH=src，只读）：pytest tests/ -q 得 **67 passed, 1 skipped**；import glaucous.cli 与 glaucous.commands OK；管道无 GLAUCOUS_API_KEY 时「配置错误」+ 退出码 1；管道 /help、/badcmd 兜底、/model 列表、/view（不存在文件正确报错）、/exit -> 退出码 0 | ✓ |

## 四、范围蔓延核查

- agent/loop.py 新增 compressed 事件发射（+20 行）：M3-UI 压缩意象（波浪潮汐）的事件通道，m3-day5 spec §4.2 意象文案映射同样要求「上下文压缩事件」的意象渲染，且 M3-UI 附带 test_compression_event.py——判为「UI 采用 M3-UI」预期范围，不判蔓延。
- /view 命令：M3-UI 设计文档跟进六登记的用户主动新增功能，随「UI 采用 M3-UI」保留；m3-day5 spec 14 命令之外属 UI 层增量，不判蔓延（发现性缺口见 S1）。
- 未发现其他两份 spec 之外的实现。

## 五、复审要求

必须修复后提请复审：**B1、B2**。
（B1 若作者裁定按合并决策豁免，请同步修订 m3-day5 spec §4.3 并在此登记后视为关闭。）
