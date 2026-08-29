"""/init 草稿生成：工作区扫描 + glaucous.md 模板（任务 3.6，FR-23）。

设计要点（Day5 Plan §4.6）：
- 扫描：深度 ≤2、跳隐藏/依赖目录、条目上限 50；特征识别产出描述行；
- 草稿为占位模板（「初始草稿」语义），经用户确认才写盘；
- 已存在 → 拒绝覆盖只提示（规则文件是团队资产，D6）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 扫描跳过的目录（隐藏目录另按前缀判断）
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".glaucous", ".tox", "dist", "build", ".idea", ".vscode"}

# 遍历深度上限与条目上限（概设「草稿」定位：克制，不刷屏）
MAX_DEPTH = 2
MAX_ENTRIES = 50

# 特征文件 → 项目描述（按优先级取首个命中组合，可多条）
MARKERS = (
    ("pyproject.toml", "Python 项目（pyproject.toml）"),
    ("requirements.txt", "Python 项目（requirements.txt）"),
    ("package.json", "Node.js 项目（package.json）"),
    ("pom.xml", "Java 项目（Maven）"),
    ("build.gradle", "Java/Kotlin 项目（Gradle）"),
    ("go.mod", "Go 项目"),
    ("Cargo.toml", "Rust 项目"),
)


def scan_workspace(workspace: Path) -> tuple[list[str], list[str]]:
    """浅扫工作区，返回（相对路径条目 ≤50, 项目特征描述列表）。

    遍历深度 ≤2；跳过隐藏与依赖目录；目录条目以 `/` 后缀区分。
    """
    entries: list[str] = []
    seen_markers: set[str] = set()
    features: list[str] = []

    def collect(rel: str, is_dir: bool) -> None:
        if len(entries) >= MAX_ENTRIES:
            return
        entries.append(rel + "/" if is_dir else rel)

    def walk(directory: Path, depth: int, prefix: str) -> None:
        if depth > MAX_DEPTH or len(entries) >= MAX_ENTRIES:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if len(entries) >= MAX_ENTRIES:
                return
            name = child.name
            if name.startswith(".") or (child.is_dir() and name in SKIP_DIRS):
                continue
            rel = f"{prefix}{name}"
            if child.is_dir():
                collect(rel, True)
                walk(child, depth + 1, rel + os.sep)
            else:
                collect(rel, False)
                for marker, label in MARKERS:
                    if name == marker and marker not in seen_markers:
                        seen_markers.add(marker)
                        features.append(label)

    walk(workspace, 0, "")
    # README 首行作为项目标题补充（存在且可读时）
    for readme in ("README.md", "README.txt", "README"):
        path = workspace / readme
        if path.is_file():
            try:
                first = next((ln.strip("# \t") for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()), "")
            except (OSError, UnicodeDecodeError):
                first = ""
            if first:
                features.append(f"README：{first[:60]}")
            break
    return entries, features


_DRAFT_TEMPLATE = """\
# 项目规则（glaucous.md）

> 本文件由 /init 生成草稿，请修订后使用。规则会全量注入智能体上下文，保持精炼。

## 项目概况
{features}

## 构建与测试命令
- （待填写）构建：
- （待填写）测试：

## 编码约定
- （待填写）

## 禁止操作
- （待填写）
"""


def render_draft(features: list[str]) -> str:
    """由识别特征渲染草稿正文；无特征时概况段给占位提示。"""
    feature_text = "\n".join(f"- {line}" for line in features) if features else "- （未自动识别出项目特征，请手动补充）"
    return _DRAFT_TEMPLATE.format(features=feature_text)
