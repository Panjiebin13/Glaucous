"""Glaucous CLI 主题：色板单一出口（M3 渲染规范的地基，概设 §8）。

色值取自 docs/Glaucous天青夏日主题设计.md §1 取色表（雨过天青的晴空 + 海鸥 +
夏日海风）。所有 UI 输出统一引用本模块的语义样式名（[glaucous.title]…[/]），
渲染层禁止散落 hex 硬编码；将来 /theme 切换暗/亮模式只改本模块的 Theme 一处。
卡片表格由 make_card() 统一构造（M3 3.2：ROUND 圆角框、框内标题栏、键值两列）；
rich Markdown 渲染样式（markdown.*）也在此定义，模型输出/方案正文与 CLI 同色板。
prompt_toolkit 输入层（M3 3.3）同样引用本模块色板：PT_STYLE 类名与语义名一一对应。

Console 自带：tty 自动检测（管道/重定向无色且不泄漏 markup 标签）、NO_COLOR
尊重、Windows conhost VT 自动启用。highlight=False 关掉 rich 对数字/路径的
默认黄色高亮，避免抢主题色。
"""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table
from rich.theme import Theme

from .context.budget import CRITICAL_RATIO, WARN_RATIO



# —— 色板（天青夏日主题设计 §1 取色表）——
TEAL = "#3AA6B9"         # 天青主色：雨过天青 → 标题/强调
GULL_WHITE = "#EAF4F4"   # 海鸥白：海鸥腹羽 → 正文
DEEP_BLUE = "#1B2A4A"    # 深海蓝：背景基底（预留暗/亮模式，暂未启用）
SALT_TEAL = "#9BD1D9"    # 海盐青：近岸浅海 → 次级/斜体
SKY_GRAY = "#5A7A8C"     # 晴空灰：晴空薄云 → 弱化/提示
BRIGHT_TEAL = "#6BB7C9"  # 亮青 → 链接/工具名
SEA_GRASS = "#7FB685"    # 海草绿：成功
SUNSET = "#F4A261"       # 晚霞橙：警告
CLAY = "#E07A5F"         # 陶土红：错误

# 语义样式名 → 样式。样式名即接口：渲染层只认名字不认色值，换色只改这一处。
THEME = Theme({
    "glaucous.title": f"bold {TEAL}",
    "glaucous.sub": f"italic {SALT_TEAL}",
    "glaucous.muted": SKY_GRAY,
    "glaucous.text": GULL_WHITE,
    "glaucous.tool": BRIGHT_TEAL,
    "glaucous.ok": SEA_GRASS,
    "glaucous.warn": SUNSET,
    "glaucous.error": CLAY,
    # M3 3.2 卡片（rich Table）：边框/标题/键名
    "glaucous.card.border": SALT_TEAL,
    "glaucous.card.title": f"bold {TEAL}",
    "glaucous.card.key": TEAL,
    # rich Markdown（模型输出/方案正文走同一色板）：标题天青、正文海鸥白、
    # 行内代码/引用海盐青、链接亮青、分隔线/弱化晴空灰
    "markdown.h1": f"bold {TEAL}",
    "markdown.h2": f"bold {TEAL}",
    "markdown.h3": f"bold {TEAL}",
    "markdown.h4": f"bold {TEAL}",
    "markdown.h5": f"bold {TEAL}",
    "markdown.h6": f"bold {TEAL}",
    "markdown.paragraph": GULL_WHITE,
    "markdown.strong": f"bold {GULL_WHITE}",
    "markdown.em": f"italic {GULL_WHITE}",
    "markdown.s": SKY_GRAY,
    "markdown.code": SALT_TEAL,
    "markdown.code_block": SALT_TEAL,
    "markdown.block_quote": f"italic {SALT_TEAL}",
    "markdown.hr": SKY_GRAY,
    "markdown.item": GULL_WHITE,
    "markdown.item.bullet": TEAL,
    "markdown.link": BRIGHT_TEAL,
    "markdown.link_url": SKY_GRAY,
    "markdown.kbd": SKY_GRAY,
})

# 全局唯一 Console，供 CLI 渲染层（cli.py 及 M3 的状态栏/卡片）共用
console = Console(theme=THEME, highlight=False)


# —— prompt_toolkit 输入层样式（M3 3.3）——
# 与 rich 共用同一色板：类名即 THEME 的语义名（class:glaucous.text），换色只改
# 上面色板常量，rich/pt 两侧同时生效。card.*/markdown.* 为 rich 专属，不在此重复。
# prompt_toolkit 缺失/损坏时置 None 不拒启动（m3-day5 plan §4.3 降级②），
# rich 渲染（THEME/console/卡片）完全不依赖 prompt_toolkit。
try:
    from prompt_toolkit.styles import Style
    PT_STYLE: "Style | None" = Style.from_dict({
        "glaucous.title": f"bold {TEAL}",
        "glaucous.sub": f"italic {SALT_TEAL}",
        "glaucous.muted": SKY_GRAY,
        "glaucous.text": GULL_WHITE,
        "glaucous.tool": BRIGHT_TEAL,
        "glaucous.ok": SEA_GRASS,
        "glaucous.warn": SUNSET,
        "glaucous.error": CLAY,
    })
except ImportError:
    PT_STYLE = None


def make_card(title: str | None = None, *, key_value: bool = False) -> Table:
    """主题化卡片表格（M3 3.2）：ROUND 圆角框、边框海盐青、标题加粗天青。

    卡片视觉单一出口：cli.py 只加行列数据，/theme 暗亮切换只改本模块。
    - 单列正文卡：title 渲染为框内首行标题栏（header + 分隔线），贴合主题设计
      §2.3~2.5 mockup 的「┌─ 标题 ─┐」形态；
    - key_value=True：键值两列（键列右对齐天青），用于审批卡「命令/风险」行。
    """
    table = Table(
        box=box.ROUNDED,
        border_style="glaucous.card.border",
        show_header=bool(title) and not key_value,
        show_edge=True,
        show_lines=False,
        pad_edge=False,
    )
    if key_value:
        table.add_column(justify="right", style="glaucous.card.key")
        table.add_column()
    else:
        table.add_column(header=title, header_style="glaucous.card.title")
    return table


def render_markdown_doc(title: str, text: str) -> None:
    """markdown 文档卡片渲染：make_card 卡片容器 + rich Markdown 正文。

    打开 markdown 文件时的展示形态，与方案卡（cli.prompt_plan_decision）同源：
    圆角卡片 + rich 渲染（标题 / 表格 / 列表 / 行内代码 / 引用，markdown.* 色板）。
    长度守卫在调用侧（cli.MD_RENDER_MAX_LINES）；本函数只负责渲染。
    """
    if not text.strip():
        console.print(f"[glaucous.muted]  （空文档）[/]")
        return
    table = make_card(escape(title))
    table.add_row(Markdown(text))
    console.print(table)


def render_code_doc(title: str, path: Path, text: str) -> None:
    """代码语法高亮渲染：rich Syntax（底层 pygments，按扩展名自动识别语言）。

    不进卡片容器：代码需要全宽 + 不折行，卡片会压窄；标题行单独打印。
    主题先用内置（monokai），与天青色板对齐留待后续（方案风险项）。
    """
    if not text.strip():
        console.print(f"[glaucous.muted]  （空文件）[/]")
        return
    console.print(f"[glaucous.card.title]{escape(title)}[/]")
    console.print(Syntax(text, lexer=_lexer_for(path), theme="monokai", line_numbers=True))


def render_text_doc(title: str, text: str) -> None:
    """纯文本渲染：make_card 卡片 + 原文（可带行号）。"""
    if not text.strip():
        console.print(f"[glaucous.muted]  （空文件）[/]")
        return
    table = make_card(escape(title))
    table.add_row(escape(text))  # 文件原文含 [x] 形态极常见（数组/日志标签），必须防 markup 吞字
    console.print(table)


def render_csv_doc(title: str, text: str) -> None:
    """CSV/TSV 表格渲染：csv.reader 解析 → make_card + 分列对齐（首行作表头）。

    CSV 含引号/多行字段时正确性有限——解析失败回退原文渲染（方案风险项）。
    """
    import csv
    import io

    if not text.strip():
        console.print(f"[glaucous.muted]  （空文件）[/]")
        return
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except (csv.Error, StopIteration):
        render_text_doc(title, text)
        return
    if not rows:
        render_text_doc(title, text)
        return
    table = make_card(escape(title), key_value=False)
    for row in rows:
        table.add_row(*[(escape(c) if c != "" else " ") for c in row])
    console.print(table)


def render_answer_card(text: str) -> None:
    """最终回答 Markdown 卡片（v1.1 打磨 R7）：流式结束后追加渲染完整回答。

    与方案卡、/view markdown 卡同源：make_card + rich Markdown（markdown.* 色板）。
    空文本/纯空白不渲染；触发条件由调用方判定（TTY 且回答非空）。
    偏离登记（S5）：概设 §8.4「正文不套面板」，此卡片为用户明确决策的产品化呈现。
    """
    if not text or not text.strip():
        return
    table = make_card("🕊 回答")
    table.add_row(Markdown(text))
    console.print(table)


def _lexer_for(path: Path) -> str:
    """按扩展名选 pygments lexer：Syntax.from_path 失败时回退 text。"""
    try:
        return Syntax.from_path(str(path)).lexer.name
    except Exception:  # noqa: BLE001 —— 未知扩展名回退纯文本
        return "text"


def ctx_ring(ratio: float) -> tuple[str, str]:
    """ctx 占用圆环字符与语义样式名（概设 §8.4 占用指示，M3 3.3 输入区头部）。

    ○/◔/◑/◕/● 按占用四分位取形；≤70% 海草绿 / >70% 晚霞橙 / >85% 陶土红
    ——阈值从 budget 模块导入（「用户看到的与系统执行的一致」，概设 §4.2）。
    theme.py 单一出口：输入区头部与 M3 状态栏复用同一映射。
    """
    ratio = min(max(ratio, 0.0), 1.0)
    glyph = ("○", "◔", "◑", "◕", "●")[min(round(ratio * 4), 4)]
    style = (
        "glaucous.error" if ratio > CRITICAL_RATIO
        else "glaucous.warn" if ratio > WARN_RATIO
        else "glaucous.ok"
    )
    return glyph, style
