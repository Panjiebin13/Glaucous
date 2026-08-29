"""工作区沙箱：realpath 规范化 + 前缀校验 + 符号链接解析 + 只读白名单（任务 1.1）。

设计要点（概设 §5.4，Day3 Plan §4.1）：
- resolve()：相对路径相对工作区根拼接，绝对路径原样，统一 resolve(strict=False) 规范化；
- check()：逃逸硬校验——realpath + 前缀校验（防 `../` 穿越与符号链接逃逸），
  区内返回规范化 Path，**路径本身非法**抛 WorkspaceEscape（硬拦截）；
- classify_path()：区内=SAFE；只读白名单=SAFE（环境探测免审批，概设 §5.4）；
  区外=WRITE（读区外仍需审批，不直接拒绝——FR-13「读取工作区外配置仍需单独同意」）；
- `.glaucous/` 纳入写排除：write/edit/bash 对运行期目录（会话/审计/方案）一律拒绝，
  agent 不可篡改审计与会话（Day3 Plan §4.6 S4 修复）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .risk import Risk


class WorkspaceEscape(RuntimeError):
    """路径逃逸硬拦截：仅用于路径本身非法（无法规范化）的防御场景。

    区外访问不抛此异常——那属于「需审批」而非「不可用」，由 classify_path 标记 WRITE 走审批管线。
    """


class Workspace:
    """工作区边界：沙箱校验 + 只读白名单 + 运行期目录写排除。"""

    def __init__(self, root: Path, read_only_extra: Iterable[Path] = (), protected_dir: str = ".glaucous"):
        self._root = root.resolve()
        self._read_only_extra = tuple(p.resolve() for p in read_only_extra)
        self._protected = self._root / protected_dir

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str) -> Path:
        """相对路径 → root 拼接；绝对路径原样；统一 resolve(strict=False) 规范化。"""
        p = Path(path)
        candidate = p if p.is_absolute() else self._root / p
        return candidate.resolve()

    def check(self, path: str) -> Path:
        """逃逸硬校验：返回区内规范化 Path；路径非法抛 WorkspaceEscape。"""
        try:
            resolved = self.resolve(path)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceEscape(f"路径无法解析: {path}（{exc}）") from exc
        if not self.is_within(resolved):
            # 此处只处理「路径非法」语义外的区外情况——区外是审批场景，
            # 但 check 被调用方用于「必须区内」的硬路径时仍应拦截。
            # 读/写工具的区外处理应优先走 classify_path（可审批），
            # 仅当调用方明确要求区内（如写排除检查）时才依赖此异常。
            raise WorkspaceEscape(f"路径越界: {path}（工作区外）")
        return resolved

    def classify_path(self, path: str) -> Risk:
        """判定路径风险：区内/白名单=SAFE；区外=WRITE（读区外仍需审批）。

        白名单先于区外判定（概设 §5.4 环境探测免审批；Day3 Plan §4.1 S7 修复）。
        """
        try:
            resolved = self.resolve(path)
        except (OSError, RuntimeError):
            return Risk.DANGEROUS  # 无法解析的路径按最坏情况对待
        if self.is_read_only(resolved):
            return Risk.SAFE
        if self.is_within(resolved):
            return Risk.SAFE
        # 区外：可审批（FR-13），记 WRITE——写区外的 DANGEROUS 判定由调用方
        # （文件写工具 risk=WRITE + 是否区外）与分类器共同完成
        return Risk.WRITE

    def is_within(self, path: Path) -> bool:
        """判断规范化路径是否在工作区内（前缀校验）。"""
        try:
            return path.is_relative_to(self._root)
        except ValueError:
            return False

    def is_read_only(self, path: Path) -> bool:
        """区内 or 只读白名单（环境探测免审批）。"""
        if self.is_within(path):
            return True
        return any(path.is_relative_to(extra) for extra in self._read_only_extra)

    def is_protected(self, path: Path) -> bool:
        """是否命中运行期受保护目录（.glaucous/，agent 不可写，防篡改审计/会话）。"""
        try:
            return path.is_relative_to(self._protected)
        except ValueError:
            return False

    def is_outside(self, path: Path) -> bool:
        """是否在工作区外（含非白名单）。"""
        return not self.is_read_only(path)
