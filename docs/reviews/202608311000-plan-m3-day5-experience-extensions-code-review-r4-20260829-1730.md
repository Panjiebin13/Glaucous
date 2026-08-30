# 代码评审报告：Glaucous M3 Day5 体验与扩展（第 r4 轮 · 聚焦复审）

> 评审日期：2026-08-29 17:30
> 评审对象：spec docs/designs/202608311000-plan-m3-day5-experience-extensions.md（m3-day5 功能基准）+ docs/designs/202608311000-design-m3-day5-cli-console-migration.md（M3-UI UI 基准）
> 代码：src/glaucous/cli.py、src/glaucous/theme.py（修复提交 2c8dcda = feature/m3-day5 HEAD）
> 模式：聚焦复审（改动清单：cli.py 顶层 prompt_toolkit 导入移除并延迟化；theme.py PT_STYLE 降级保护 + 三处 render_*_doc escape。上一轮报告：docs/reviews/202608311000-plan-m3-day5-experience-extensions-code-review-r3-20260829-1700.md）
> 结论：**通过**（阻塞 0 项，建议 0 项新增；r3 建议 S1~S5 维持登记现状）

## 〇、评审范围与方法

- **改动核对**（git show 2c8dcda）：diff 仅含两文件且与修复声明一致，无夹带改动——
  - cli.py（+23/−9）：顶层 PromptSession/WordCompleter/FileHistory/HTML 四项导入全部移除，前三者移入 make_prompt_session（cli.py:634-636），HTML 移入 repl 的 session 非 None 分支（cli.py:724）；make_prompt_session docstring 同步（降级③「导入在此延迟，顶层不依赖 prompt_toolkit」，cli.py:626-627）。
  - theme.py（+20/−17）：移除顶层 from prompt_toolkit.styles import Style；PT_STYLE 构造包入 try/except ImportError（theme.py:88-101，缺失置 None）；render_markdown_doc/render_text_doc/render_csv_doc 三处 title + render_text_doc 正文 + render_csv_doc 单元格统一 escape()。
- **工作区状态**：HEAD == feature/m3-day5 == 2c8dcda；未跟踪文件仅 r3 评审报告（评审产物）。
- **方法**：静态逐行比对两份 spec + r3 报告 B1/B2 修复方向；只读运行验证（pytest 全量 / 正常导入冒烟 / 阻断导入冒烟 / TTY 模拟构造 / escape 渲染实测），未修改任何源码。

## 一、阻塞问题

无（r3 B1、B2 均确认关闭，证据见通过项表）。

## 二、建议问题

无新增。（r3 S1~S5 均不在本轮改动范围，维持 r3 登记现状：S1 /view 发现性、S2 /exit 死分支、S3 rich 上限、S4 TODO.md 3.2r 待办、S5 Banner 占位。）

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| B1 关闭·Spec 符合性 | cli.py 顶层零 prompt_toolkit 导入（60-66 行仅注释与 rich/theme 导入，全文 grep 无残留代码引用）；theme.py 顶层零 prompt_toolkit 导入 | ✓ |
| B1 关闭·Spec 符合性 | theme.py:88-101 PT_STYLE 构造包 try/except ImportError，缺失置 None（§4.3 降级②「依赖损坏不拒启动」）；try 块语法正确（pytest 与导入实测通过），注解 `"Style \| None"` 为字符串字面量 + 模块启用 from __future__ import annotations，ImportError 分支不求值、无 NameError 风险 | ✓ |
| B1 关闭·逻辑正确性 | 阻断导入冒烟（sys.modules["prompt_toolkit"]=None 模拟依赖损坏）：import glaucous.theme 与 glaucous.cli 均成功不拒启动、theme.PT_STYLE 为 None、make_prompt_session（TTY 模拟）返回 None | ✓ |
| B1 关闭·逻辑正确性 | 正常安装场景：theme.PT_STYLE 为 prompt_toolkit Style 实例；TTY 模拟（isatty=True + DummyOutput patch create_output）下 make_prompt_session 返回 PromptSession 且 session.style is theme.PT_STYLE——style=PT_STYLE 传参正确（cli.py:646） | ✓ |
| B1 关闭·逻辑正确性 | 降级③「导入/构造失败」名实一致：make_prompt_session 延迟导入于 try 块内（cli.py:633-636），except Exception 兜底（cli.py:649，ImportError 为其子类）；并获真实异常实证——本环境 PromptSession 构造抛 prompt_toolkit.output.win32.NoConsoleScreenBufferError，被捕获降级返回 None 而非崩溃，构造失败子路径真实可用 | ✓ |
| B1 波及面 | PT_STYLE 全仓库代码引用仅三处：theme.py 定义、cli.py:69 导入、cli.py:646 使用（ui/renderer.py:118 等均为注释）；PT_STYLE=None 时唯一消费点 style=PT_STYLE 传 None 对 prompt_toolkit 合法（默认无样式，cli.py:643 注释与行为一致）；rich 侧 THEME/console 完全不依赖 prompt_toolkit（渲染实测正常） | ✓ |
| B1 波及面 | repl 中 HTML 延迟导入（cli.py:724）位于 session 非 None 分支：session 非 None 蕴含 make_prompt_session 已成功导入 prompt_toolkit 包，formatted_text.HTML 为同包子模块必然可导入；session=None（管道/降级）路径走 console.input 分支不触达 | ✓ |
| B2 关闭·Spec 符合性 | 三处 title escape：render_markdown_doc（theme.py:138）、render_text_doc（:161）、render_csv_doc（:185）；render_text_doc 正文 escape（:162）；render_csv_doc 单元格 escape（:187）——r3 所列全部遗漏点闭合；render_code_doc 原有 escape（:152）未回退 | ✓ |
| B2 关闭·逻辑正确性 | escape 顺序正确：render_csv_doc 空单元格先判空替换 " "（空值不经 escape，" " 无 markup 风险），非空才 escape——实测 "x,,z" 空位保留、"a,[b],c" 单元格 [b] 字面显示 | ✓ |
| B2 关闭·逻辑正确性 | 渲染实测（rich 输出逐字比对）：":book: a[b].txt" → 「📖 a[b].txt」；正文「line1 [b] line2」逐字保真；csv 单元格 [b] 字面；md 卡片 title「📖 m[b].md」——r3 吞字缺陷（[b] 被 rich 解析为 markup 标签而消失）全部修复 | ✓ |
| B2 关闭·逻辑正确性 | :emoji: 短代码无副作用：escape() 仅转义 [标签] 形态（实测 escape(":book: a[b].txt") → ":book: a\[b].txt"，冒号形态不转义），title 的 ":book: " 前缀仍被 rich 渲染为 📖 emoji（三处卡片实测确认） | ✓ |
| B2 波及面 | 无双重转义：全部调用方传原始文本——cli.py:359（_render_md_tool_end）、cli.py:423 title=f":book: {rel}" → :452/:456/:458（_cmd_view）、theme.py 内部回退 :180/:183（render_csv_doc → render_text_doc 单层转义）；escape 收敛于 theme.py 单层执行 | ✓ |
| 逻辑正确性·运行验证 | pytest tests/ -q：**67 passed, 1 skipped**（与 r3 基线一致，无回归）；import glaucous.cli / glaucous.theme 正常导入冒烟通过 | ✓ |
| 逻辑正确性·夹带核对 | git show 2c8dcda 无 tests/spec/TODO/pyproject 改动；新增注释（cli.py:60-64、theme.py:86-87、cli.py:643）与代码实际行为一致，无误导性说明 | ✓ |

## 四、范围蔓延核查

- git show 2c8dcda 改动严格限于 r3 报告 B1/B2 修复方向所列内容：无新增功能、无顺带重构、无依赖或文档变更。
- theme.py 的 escape 引入属 r3 B2 修复方向原样执行；cli.py 导入移位属 r3 B1 修复方向原样执行；注释更新为修复配套说明。未发现 spec 之外的实现。

## 五、复审要求

无——r3 阻塞 B1、B2 已全部修复关闭，本轮通过。
（遗留跟踪项：r3 建议 S1~S5 与 M3-UI 登记的 TODO.md 3.2r 待办维持原状，按既有安排偿还，不构成本轮复审条件。）
