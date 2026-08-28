"""L0 工具输出截断（任务 2.5，FR-24，概设 §4.2）。

设计要点（Day4 Plan §4.5）：
- 触发阈值：单结果 >300 行或 >50KB（概设 §4.2 L0）；
- 截断不丢数据：完整输出落盘 .glaucous/outputs/<call_id>.log，入史正文替换为
  「头 200 行 + 省略标记 + 尾 50 行 + read_output 回取提示」；
- call_id 净化：仅保留 [A-Za-z0-9_-]（防路径注入），落盘路径由系统派生；
- 落盘失败降级为仅截断、附「保存失败」说明（尽力而为，不阻断主流程）；
- 边界：行数不足头尾之和（超长单行触发字节阈值）时保留头 200 行、其余整体省略。
"""

from __future__ import annotations

import re
from pathlib import Path

L0_MAX_LINES = 300
L0_MAX_BYTES = 50 * 1024
KEEP_HEAD = 200
KEEP_TAIL = 50

_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_call_id(call_id: str) -> str:
    """call_id 白名单净化：非法字符替换为下划线（空值兜底 unknown）。"""
    return _SAFE_ID.sub("_", call_id) or "unknown"


def truncate_output(content: str, call_id: str, outputs_dir: Path) -> tuple[str, bool]:
    """按阈值截断工具输出。

    :returns: (入史正文, 是否截断)；未超限原样返回 (content, False)
    """
    lines = content.splitlines()
    if len(lines) <= L0_MAX_LINES and len(content.encode("utf-8")) <= L0_MAX_BYTES:
        return content, False

    safe_id = sanitize_call_id(call_id)
    saved = False
    try:
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / f"{safe_id}.log").write_text(content, encoding="utf-8", newline="\n")
        saved = True
    except OSError:
        saved = False

    if len(lines) > KEEP_HEAD + KEEP_TAIL:
        head, tail = lines[:KEEP_HEAD], lines[-KEEP_TAIL:]
        omitted = len(lines) - KEEP_HEAD - KEEP_TAIL
    else:
        # 行数不多但字节超限（超长单行）：保留头 200 行，其余整体省略
        head, tail = lines[:KEEP_HEAD], []
        omitted = max(0, len(lines) - KEEP_HEAD)

    parts = head
    parts.append(f"…（中间 {omitted} 行已截断）…")
    parts.extend(tail)
    body = "\n".join(parts)
    saved_note = (
        f"（完整输出已保存至 .glaucous/outputs/{safe_id}.log，"
        "可调用 read_output(call_id, offset, limit) 分段查看）"
        if saved
        else "（完整输出落盘失败，仅保留以上头尾部分）"
    )
    # 字节级兑底：行数裁剪后仍可能超限（压缩 JSON/minified JS 等超长行、
    # 或行数未超但字节超 50KB 时头尾合计仍超阈值），对正文硬截断到阈值内，
    # 保证入史正文永不超 L0 上限（errors=ignore 丢弃末尾不完整多字节序列）
    if len(body.encode("utf-8")) > L0_MAX_BYTES:
        body = body.encode("utf-8")[:L0_MAX_BYTES].decode("utf-8", errors="ignore")
    return body + "\n" + saved_note, True
