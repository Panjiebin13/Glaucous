"""Skill 注册系统：三层扫描 / 索引注入 / 惰性加载（任务 3.5，FR-28，概设 §7.3）。

设计要点（Day5 Plan §4.5）：
- Skill 定义 = 目录 + SKILL.md（frontmatter 声明 name/description，正文为指令）；
- 三层扫描：包内资产（内置）→ ~/.glaucous/skills/ → <workspace>/.glaucous/skills/，
  同名覆盖（项目 > 全局 > 内置，更近的作用域优先，D5）；
- 两段式惰性加载：启动只注入 name+description 索引（<30 token/个）；
  模型判断相关时经 load_skill 工具取正文（正文入史，会话内有效、跨会话自然失效）；
- frontmatter 畸形 → 跳过并记 warnings，不因一个坏技能拖垮启动。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILL_FILE = "SKILL.md"

# 扫描来源标签（/skills 展示用）
SOURCE_LABEL = {"builtin": "内置", "global": "全局", "project": "项目"}


@dataclass(frozen=True)
class SkillInfo:
    """一个已注册技能的元信息。"""

    name: str
    description: str
    source: str  # "builtin" | "global" | "project"
    path: Path   # SKILL.md 路径


def builtin_skills_root() -> Path | None:
    """包内内置技能目录（随包分发）；打包形态异常时返回 None（降级为无内置）。"""
    try:
        from importlib.resources import files

        root = Path(str(files("glaucous").joinpath("assets/skills")))
        return root if root.is_dir() else None
    except (TypeError, OSError, ModuleNotFoundError):
        return None


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md：`---` frontmatter（key: value 行）+ 正文。

    :raises ValueError: frontmatter 缺失或无闭合分隔线
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("缺少 frontmatter 起始分隔线")
    meta: dict[str, str] = {}
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            body = "\n".join(lines[idx + 1:]).strip()
            return meta, body
        key, sep, value = lines[idx].partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    raise ValueError("frontmatter 未闭合")


class SkillRegistry:
    """技能注册表：启动扫描一次，索引与正文按需读取。"""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._skills: dict[str, SkillInfo] = {}
        self._bodies: dict[str, str] = {}
        self._loaded: set[str] = set()
        self.warnings: list[str] = []

    def scan(self) -> None:
        """三层扫描：内置 → 全局 → 项目，同名后者覆盖前者。"""
        self._skills.clear()
        self._bodies.clear()
        self._loaded.clear()
        self.warnings.clear()
        sources: list[tuple[str, Path | None]] = [
            ("builtin", builtin_skills_root()),
            ("global", Path.home() / ".glaucous" / "skills"),
            ("project", self._workspace / ".glaucous" / "skills"),
        ]
        for source, root in sources:
            if root is None or not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                skill_file = child / SKILL_FILE
                if not skill_file.is_file():
                    continue
                try:
                    text = skill_file.read_text(encoding="utf-8")
                    meta, body = _parse_frontmatter(text)
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    self.warnings.append(f"技能 {child} 已跳过（{exc}）")
                    continue
                name = meta.get("name") or child.name
                self._skills[name] = SkillInfo(
                    name=name,
                    description=meta.get("description", ""),
                    source=source,
                    path=skill_file,
                )
                self._bodies[name] = body

    def index_text(self) -> str:
        """索引注入文本：`- name: description` 逐行；无技能返回空串。"""
        if not self._skills:
            return ""
        lines = [
            f"- {info.name}: {info.description}" if info.description else f"- {info.name}"
            for info in self._skills.values()
        ]
        return "\n".join(lines)

    def load(self, name: str) -> str | None:
        """取技能正文（惰性加载）；未注册返回 None。重复加载幂等。"""
        body = self._bodies.get(name)
        if body is None:
            return None
        self._loaded.add(name)
        return body

    def skill_text(self, name: str) -> str | None:
        """按名取技能正文（v1.1 反馈 F3 /skill 手动调用）：纯读取无副作用，
        不标记加载态（加载状态不对外呈现，正文入史语义由调用方决定）。"""
        return self._bodies.get(name)

    def infos(self) -> list[SkillInfo]:
        """全部已注册技能（按名称排序，/skills 展示用）。"""
        return sorted(self._skills.values(), key=lambda info: info.name)

    def loaded_names(self) -> set[str]:
        """本会话已加载的技能名集合（副本）。"""
        return set(self._loaded)
