"""Spec 子系统（v1.1-M5，FR-52~59）。

模块划分（spec §一）：
- store.py：Spec 文档读写/frontmatter/状态机（.glaucous/specs/<id>.md）；
- templates.py：文档模板 + 两套评审检查清单 + 报告契约；
- pipeline.py：澄清→起草→评审→批准→执行→验收的命令式编排（决策 1）。
"""

from .store import SpecDoc, SpecStateError, SpecStore  # noqa: F401
