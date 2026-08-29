"""事实记忆双作用域存储与 Top-N 注入（任务 2.2，FR-21/22，概设 §7.2）。

设计要点（Day4 Plan §4.2）：
- 双作用域：全局 ~/.glaucous/memory.json + 项目 <workspace>/.glaucous/memory.json，
  JSON 数组格式（可读可手工编辑，M3 /memory 可直接管理）；
- **存储全量、注入裁剪（Top-N）**：注入时按 (last_used, created_at) 降序取前
  N 条——「按 category 与最近使用加权裁剪」的简化实现（category 以标注保留，
  最近使用为主权重）；存储文件永不自动裁剪；
- 去重作用域 = 单一存储文件内：同 content 刷新 last_used 而非新增（跨作用域
  不去重——同一事实在全局与项目分属不同信任范围）；
- 写入原子化（临时文件 + os.replace），损坏容错为空表重建（宁丢不崩，
  与审计「尽力而为」一致）；读写失败不阻断主流程。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_CATEGORY = "general"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MemoryStore:
    """双作用域事实记忆存储（启动时加载，进程内缓存，写入即落盘）。"""

    def __init__(self, global_path: Path, project_path: Path):
        self._paths = {"global": global_path, "project": project_path}
        self._cache: dict[str, list[dict[str, Any]]] = {
            scope: self._load(path) for scope, path in self._paths.items()
        }

    # -- 读取与容错 ---------------------------------------------------------

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        """加载单个存储文件；缺失/非法 JSON/非数组一律容错为空表（宁丢不崩）。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict) and entry.get("content")]

    def _save(self, scope: str) -> None:
        """原子化写回单个作用域：临时文件 + os.replace；失败尽力而为不阻断。"""
        path = self._paths[scope]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self._cache[scope], f, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except OSError:
            pass

    # -- 写入 ---------------------------------------------------------------

    def add(self, content: str, scope: str, category: str = DEFAULT_CATEGORY) -> bool:
        """写入一条记忆（scope: "global"|"project"）。返回是否为新增。

        同作用域内 content 完全一致 → 刷新 last_used 与 category，返回 False（去重）；
        否则新增条目（created_at/last_used 记当前时间），返回 True。
        :raises ValueError: scope 非法（工具层应先行校验，此为最后防线）
        """
        if scope not in self._paths:
            raise ValueError(f"未知记忆作用域: {scope}")
        entries = self._cache[scope]
        now = _now()
        for entry in entries:
            if entry.get("content") == content:
                entry["last_used"] = now
                entry["category"] = category or DEFAULT_CATEGORY
                self._save(scope)
                return False
        entries.append(
            {
                "content": content,
                "category": category or DEFAULT_CATEGORY,
                "created_at": now,
                "last_used": now,
            }
        )
        self._save(scope)
        return True

    # -- 注入 ---------------------------------------------------------------

    def load_injection(self, top_n: int) -> str:
        """生成注入文本：双作用域合并 → 按 (last_used, created_at) 降序取 Top-N。

        被选中条目刷新 last_used（注入即使用，下次排序权重自然前移）；
        无记忆返回空串（prompts 层省略该注入段）。
        """
        merged: list[tuple[dict[str, Any], str]] = []
        for scope, label in (("project", "项目"), ("global", "全局")):
            for entry in self._cache[scope]:
                merged.append((entry, label))
        if not merged or top_n <= 0:
            return ""
        merged.sort(
            key=lambda pair: (
                pair[0].get("last_used") or pair[0].get("created_at") or "",
                pair[0].get("created_at") or "",
            ),
            reverse=True,
        )
        selected = merged[:top_n]
        now = _now()
        for entry, _ in selected:
            entry["last_used"] = now
        for scope in self._paths:
            self._save(scope)
        lines = [
            f"- [{label}][{entry.get('category') or DEFAULT_CATEGORY}] {entry.get('content', '')}"
            for entry, label in selected
        ]
        return "\n".join(lines)
