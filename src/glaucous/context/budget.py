"""token 记账与占用档位（任务 2.4，FR-25/31，概设 §4.2）。

设计要点（Day4 Plan §4.4）：
- 估算：ASCII 字符 /4 + CJK 字符 /1.5（概设 §4.2「字符数/4，中文按/1.5」），
  按消息粒度对 view() 全序列求和（D9：全量重算，O(n) 开销可忽略）；
- 三档阈值常量单一出口：L1（>70%）与 L2（>85%）压缩策略从本模块导入
  同一常量——「用户看到的与系统执行的一致」（概设 §4.2）；
- 估算偏差已接受：tools 声明与输出预留不计入（Day4 Plan §4.4），
  阈值可经 GLAUCOUS_CONTEXT_LIMIT 调低补偿；
- estimate_tokens 为唯一估算出口，M4 可无损替换为精确 tokenizer 实现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# 分级阈值（L1 裁剪 / L2 压缩 / 占用条三档共用，概设 §4.2）
WARN_RATIO = 0.70
CRITICAL_RATIO = 0.85

# CJK 字符范围：CJK 统一表意文字 + 中文标点 + 全角形式
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),  # Fullwidth Forms
)


@dataclass(frozen=True)
class BudgetReport:
    """一次预算评估结果：占用条与压缩管线共用的数据源。"""

    used: int
    limit: int
    percent: float
    level: str  # "low" | "warn" | "critical"


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """单条文本 token 估算：ASCII/4 + CJK/1.5（概设 §4.2；精确 tokenizer 接入点）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    ascii_count = len(text) - cjk
    return int(ascii_count / 4 + cjk / 1.5)


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    """消息序列 token 估算：逐条 JSON 序列化后求和（含键名开销，保守偏高可接受）。"""
    total = 0
    for entry in messages:
        try:
            total += estimate_tokens(json.dumps(entry, ensure_ascii=False))
        except (TypeError, ValueError):
            total += estimate_tokens(str(entry))
    return total


def level_of(percent: float) -> str:
    """占用比例 → 档位：low（≤70%）/ warn（70–85%）/ critical（>85%）。"""
    if percent > CRITICAL_RATIO:
        return "critical"
    if percent > WARN_RATIO:
        return "warn"
    return "low"


def build_report(messages: list[dict[str, Any]], limit: int) -> BudgetReport:
    """对消息序列做一次预算评估（system + 全部历史，limit 来自 GLAUCOUS_CONTEXT_LIMIT）。"""
    used = estimate_messages(messages)
    percent = used / limit if limit > 0 else 1.0
    return BudgetReport(used=used, limit=limit, percent=percent, level=level_of(percent))
