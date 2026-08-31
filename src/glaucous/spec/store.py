"""Spec 文档读写 / frontmatter / 状态机（v1.1-M5 任务 5.1，FR-54；spec §3.1）。

落盘位置 `.glaucous/specs/<id>.md`（FR-54）；frontmatter 为 `---` 围栏的
`key: value` 行（解析容错：损坏行跳过取默认）。状态机迁移强校验
（概设 §7.1，spec §2.2）；原子写（tmp + replace，同 sessions 索引口径）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 状态机合法迁移表（spec §2.2；修订回环不迁移状态，仅 round 自增）
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("reviewing", "archived"),
    "reviewing": ("approved", "draft", "archived"),
    "approved": ("executing", "archived"),
    "executing": ("code_review", "archived"),
    "code_review": ("verified", "archived"),
    "verified": (),
    "archived": (),
}

ALL_STATUSES = tuple(TRANSITIONS)
TERMINAL_STATUSES = ("verified", "archived")

# checkbox 行：- [ ] / - [x]（允许前导空白与 * 记号变体不在支持面，概设 §7.2 字面 - [ ]）
_CHECKBOX = re.compile(r"^(\s*)- \[( |x)\] (.*)$")
# 验收标准行：## 验收标准 节内的 "- " 列表行
_SECTION = re.compile(r"^## ")


class SpecStateError(RuntimeError):
    """非法状态迁移 / Spec 文档缺失 / 任务序号越界。"""


@dataclass
class SpecDoc:
    """单个 Spec 文档的内存形态（frontmatter + 正文）。"""

    meta: dict[str, Any]
    body: str
    path: Path
    warnings: list[str] = field(default_factory=list)  # load 期容错留痕

    @property
    def spec_id(self) -> str:
        return str(self.meta.get("id") or self.path.stem)

    @property
    def status(self) -> str:
        status = str(self.meta.get("status") or "draft")  # 缺失容错 → draft（spec §五）
        return status if status in ALL_STATUSES else "draft"

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.spec_id)

    def tasks(self) -> list[tuple[int, bool, str]]:
        """任务清单解析：(1 起序号, 已完成, 任务文本)。"""
        out: list[tuple[int, bool, str]] = []
        for line in self.body.splitlines():
            m = _CHECKBOX.match(line)
            if m:
                out.append((len(out) + 1, m.group(2) == "x", m.group(3).strip()))
        return out

    def acceptance(self) -> list[str]:
        """验收标准行原文（## 验收标准 节内的列表行）。"""
        lines = self.body.splitlines()
        out: list[str] = []
        in_section = False
        for line in lines:
            if line.strip() == "## 验收标准":
                in_section = True
                continue
            if in_section and _SECTION.match(line):
                break
            if in_section and line.strip().startswith("- "):
                out.append(line.strip()[2:].strip())
        return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_frontmatter(text: str, warnings: list[str]) -> tuple[dict[str, Any], str]:
    """`---` 围栏 frontmatter 解析（容错：损坏行跳过）；无围栏 → 空 meta 全文 body。"""
    meta: dict[str, Any] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        warnings.append("frontmatter 围栏未闭合，按无 frontmatter 处理")
        return meta, text
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            if line.strip():
                warnings.append(f"frontmatter 损坏行已跳过：{line.strip()[:40]}")
            continue
        meta[key.strip()] = value.strip()
    # 类型归一化（spec §2.1）
    try:
        meta["round"] = int(meta.get("round") or 0)
    except (TypeError, ValueError):
        meta["round"] = 0
    raw_cp = meta.get("entry_checkpoint")
    if raw_cp in (None, "", "null"):
        meta["entry_checkpoint"] = None
    else:
        try:
            meta["entry_checkpoint"] = int(raw_cp)
        except (TypeError, ValueError):
            meta["entry_checkpoint"] = None
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _render_frontmatter(meta: dict[str, Any]) -> str:
    keys = ("id", "name", "status", "created_at", "updated_at", "approved_at",
            "round", "mode", "entry_checkpoint", "acceptance")
    lines = ["---"]
    for key in keys:
        value = meta.get(key)
        if value is None:
            value = "null" if key == "entry_checkpoint" else ""
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


class SpecStore:
    """Spec 文档目录的读写与状态机（.glaucous/specs/）。"""

    def __init__(self, workspace: Path):
        self._dir = Path(workspace) / ".glaucous" / "specs"

    @property
    def specs_dir(self) -> Path:
        return self._dir

    # -- 读写 -------------------------------------------------------------

    def _new_id(self) -> str:
        base = f"spec-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        candidate = base
        n = 2
        while (self._dir / f"{candidate}.md").exists():  # 同秒冲突追加序号（spec §二）
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def create(self, name: str, body: str) -> SpecDoc:
        """起草落盘（status=draft，FR-54）。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        spec_id = self._new_id()
        meta = {
            "id": spec_id,
            "name": name[:40] if name else spec_id,
            "status": "draft",
            "created_at": _now(),
            "updated_at": _now(),
            "approved_at": "",
            "round": 0,
            "mode": "",
            "entry_checkpoint": None,
        }
        doc = SpecDoc(meta=meta, body=body, path=self._dir / f"{spec_id}.md")
        self._write(doc)
        return doc

    def load(self, spec_id: str) -> SpecDoc:
        path = self._dir / f"{spec_id}.md"
        if not path.is_file():
            raise SpecStateError(f"Spec 文档不存在：{spec_id}")
        warnings: list[str] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SpecStateError(f"Spec 文档读取失败：{exc}") from exc
        meta, body = _parse_frontmatter(text, warnings)
        if not meta.get("id"):
            meta["id"] = spec_id
        return SpecDoc(meta=meta, body=body, path=path, warnings=warnings)

    def _write(self, doc: SpecDoc) -> None:
        doc.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = doc.path.with_suffix(".md.tmp")
        tmp.write_text(_render_frontmatter(doc.meta) + "\n" + doc.body, encoding="utf-8", newline="\n")
        os.replace(tmp, doc.path)

    def save_body(self, doc: SpecDoc, body: str) -> None:
        """修订回环写回正文（frontmatter 不变，updated_at 刷新）。"""
        doc.body = body
        doc.meta["updated_at"] = _now()
        self._write(doc)

    # -- 状态机 ------------------------------------------------------------

    def transition(self, doc: SpecDoc, to: str, **meta_fields: Any) -> SpecDoc:
        """强校验迁移（spec §2.2）；meta_fields 顺带落 frontmatter。"""
        current = doc.status
        if to not in TRANSITIONS.get(current, ()):
            raise SpecStateError(f"非法状态迁移：{current} → {to}")
        doc.meta["status"] = to
        doc.meta["updated_at"] = _now()
        for key, value in meta_fields.items():
            doc.meta[key] = value
        self._write(doc)
        return doc

    # -- 进度写回（决策 4：勾选 = 唯一进度载体） -----------------------------

    def check_task(self, doc: SpecDoc, task_no: int) -> None:
        """第 task_no 个 checkbox 勾选写回（1 起序号；重复勾选幂等）。"""
        lines = doc.body.splitlines()
        seen = 0
        for i, line in enumerate(lines):
            m = _CHECKBOX.match(line)
            if not m:
                continue
            seen += 1
            if seen == task_no:
                lines[i] = f"{m.group(1)}- [x] {m.group(3)}"
                doc.body = "\n".join(lines) + ("\n" if doc.body.endswith("\n") else "")
                doc.meta["updated_at"] = _now()
                self._write(doc)
                return
        raise SpecStateError(f"任务序号越界：{task_no}（共 {seen} 项）")

    def append_note(self, doc: SpecDoc, note: str) -> None:
        """向 `## 风险与回退` 节尾追加注记（跳过项/验收报告/取消备注）。"""
        lines = doc.body.splitlines()
        start = None
        end = len(lines)
        for i, line in enumerate(lines):
            if line.strip() == "## 风险与回退":
                start = i + 1
            elif start is not None and _SECTION.match(line):
                end = i
                break
        stamp = datetime.now().strftime("%m-%d %H:%M")
        insert = [f"- [{stamp}] {note}"]
        if start is None:
            lines += ["", "## 风险与回退"] + insert
        else:
            lines[start:end] = lines[start:end] + insert
        doc.body = "\n".join(lines) + ("\n" if doc.body.endswith("\n") else "")
        self._write(doc)

    # -- 列表 / 活跃查询 -----------------------------------------------------

    def list_all(self) -> list[SpecDoc]:
        """全量列表（created_at 倒序）；损坏文件跳过并记入 self.warnings。"""
        self.warnings: list[str] = []
        if not self._dir.is_dir():
            return []
        docs: list[SpecDoc] = []
        for path in sorted(self._dir.glob("*.md")):
            try:
                loaded = self.load(path.stem)
                if not loaded.meta.get("status") and not loaded.body.strip():
                    raise SpecStateError("无 frontmatter 且正文为空（非 Spec 文档）")
                docs.append(loaded)
            except SpecStateError as exc:
                self.warnings.append(f"{path.name}：{exc}")
        docs.sort(
            key=lambda d: (str(d.meta.get("created_at") or ""), d.path.stem), reverse=True
        )  # 同秒以 stem 次序（冲突后缀 -2 居后建，倒序居前）
        return docs

    def active(self) -> SpecDoc | None:
        """最新非终态 Spec（供 /spec 无参与 read_spec 缺省）。"""
        for doc in self.list_all():
            if doc.status not in TERMINAL_STATUSES:
                return doc
        return None
