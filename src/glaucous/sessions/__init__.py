"""会话管理子系统（v1.1-M3，FR-44~51）。

模块划分（spec §十）：
- paths.py：project-hash/用户级目录/旧会话迁移/统一新建入口；
- index.py：侧边 JSON 索引（SessionEntry/SessionIndex，损坏重建，FR-45/46）；
- stats.py：统计聚合纯函数（FR-49）。
"""

from .paths import (  # noqa: F401
    create_session_history,
    index_path,
    migrate_legacy_sessions,
    project_dir,
    project_hash,
    sessions_root,
)
from .index import SessionEntry, SessionIndex, derive_name  # noqa: F401
