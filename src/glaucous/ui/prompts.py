"""system prompt 组装。

Day 1 仅有基础段落；glaucous.md 规则注入、事实记忆、skill 索引
是 M2/M3 任务，届时在 build_system_prompt 的段落序列上扩展（概设 §4.2）。
"""

from __future__ import annotations

from pathlib import Path

BASE_PROMPT = """\
你是 Glaucous，一个运行在终端里的编程智能体。用户会在一个项目目录（工作区）中\
向你提出编程任务，你通过调用工具阅读代码、搜索内容、了解项目结构，然后回答或继续探索。

工作准则：
- 回答前先用工具查看真实文件，不要凭空猜测项目内容。
- 当前处于只读探索阶段（Day 1 原型），你可以自由使用 read_file / list_dir / grep。
- 工具输出已带行号与截断标注，引用代码时使用「文件路径:行号」格式，方便用户定位。
- 回答使用简体中文，简洁直接，先给结论再给依据。
"""


def build_system_prompt(workspace: Path) -> str:
    """组装 system prompt：基础准则 + 工作区信息。

    后续里程碑的注入顺序（概设 §4.2）：基础准则 → glaucous.md 规则 →
    事实记忆 → skill 索引，均在 system 消息内追加段落。
    """
    return f"{BASE_PROMPT}\n当前工作区：{workspace.resolve()}\n"
