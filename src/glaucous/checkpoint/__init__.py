"""Checkpoint 模块（v1.1-M4，FR-40~43）。

- git_snapshots.py：git 子进程封装（临时索引快照五步/diff/restore/ref 管理）；
- store.py：checkpoints.json 索引、保留淘汰、回退编排。
"""

from .git_snapshots import GitError  # noqa: F401
from .store import Checkpoint, CheckpointStore  # noqa: F401
