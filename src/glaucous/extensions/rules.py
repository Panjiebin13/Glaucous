"""glaucous.md 规则记忆：双层读取与注入文本生成（任务 2.1，FR-20）。

设计要点（概设 §7.2）：
- 双层：全局 ~/.glaucous/glaucous.md（跨项目）+ 项目 <workspace>/glaucous.md；
- **全量注入永不裁剪**——规则被裁剪等于没规则；单文件超长时只附提示建议
  用户精简，不截断正文；
- 缺失容错：文件不存在/读取失败/解码失败一律返回空段（首用用户无规则文件
  是常态，不报错）；本轮不做 /init 草稿生成（归 M3 任务 3.6）。
"""

from __future__ import annotations

from pathlib import Path

# 单文件超长提示阈值（字符）：超过则在段尾附精简建议（概设 §7.2「超长时提示用户精简」）
RULE_CHAR_WARN_LIMIT = 4000

GLOBAL_HEADER = "【全局规则】"
PROJECT_HEADER = "【项目规则】"
TOO_LONG_NOTE = "（规则过长，建议精简——规则被裁剪等于没规则）"


def global_rules_path() -> Path:
    """全局规则文件路径：~/.glaucous/glaucous.md。"""
    return Path.home() / ".glaucous" / "glaucous.md"


def project_rules_path(workspace: Path) -> Path:
    """项目规则文件路径：<workspace>/glaucous.md。"""
    return workspace / "glaucous.md"


def _read_rule_file(path: Path) -> str:
    """读取单个规则文件：成功返回正文（超长附提示），缺失/损坏返回空串。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if not text.strip():
        return ""
    note = TOO_LONG_NOTE if len(text) > RULE_CHAR_WARN_LIMIT else ""
    return f"{text.rstrip()}{note}"


def load_rules(workspace: Path) -> str:
    """读取双层规则并拼接为注入文本：全局段在前、项目段在后（项目覆盖语义更具体）。

    空段省略；两文件均缺失时返回空串（由 prompts 层决定是否省略注入段）。
    """
    sections: list[str] = []
    global_text = _read_rule_file(global_rules_path())
    if global_text:
        sections.append(f"{GLOBAL_HEADER}\n{global_text}")
    project_text = _read_rule_file(project_rules_path(workspace))
    if project_text:
        sections.append(f"{PROJECT_HEADER}\n{project_text}")
    return "\n\n".join(sections)
