"""会话统计聚合（v1.1-M3 任务 3.5，FR-49；spec §六）。

纯函数：命令层拼卡，本模块只做数据聚合。
- audit.log 双格式口径（spec 决策 7/r2-S11）：只统计含 `decision` 字段的行
  （审批管线格式 time/decision/agent/...）；at/event 命令审计行跳过；
  无 agent 字段的行（如 record_denial 的 plan_mode_blocked）归「未标注」桶。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterable

# agent 维度小计的缺省桶（无 agent 字段的审批行）
UNKNOWN_AGENT = "未标注"


def role_distribution(messages: list[dict]) -> dict[str, int]:
    """消息数按角色分布（读 History.messages 内存权威）。"""
    counter: Counter[str] = Counter()
    for msg in messages:
        role = str(msg.get("role") or "unknown")
        counter[role] += 1
    return dict(counter)


def approval_distribution(audit_paths: Iterable[Path]) -> dict[str, dict[str, int]]:
    """审批决策分布（decision → agent → 计数）。

    - 仅统计含 decision 字段的行；at/event 命令审计行跳过（spec 决策 7）；
    - agent 缺省归「未标注」桶（r2-S11）；损坏行跳过；文件缺失静默。
    """
    result: dict[str, dict[str, int]] = {}
    for path in audit_paths:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or "decision" not in event:
                continue
            decision = str(event["decision"])
            agent = str(event.get("agent") or UNKNOWN_AGENT)
            result.setdefault(decision, {})
            result[decision][agent] = result[decision].get(agent, 0) + 1
    return result


def global_totals(index: dict) -> dict[str, int]:
    """全局聚合（r2-S13 口径）：全部 projects 的会话数/消息数/token 总和。"""
    totals = {"sessions": 0, "messages": 0, "tokens": 0}
    for project in (index.get("projects") or {}).values():
        for session in project.get("sessions", []):
            totals["sessions"] += 1
            totals["messages"] += int(session.get("message_count", 0) or 0)
            totals["tokens"] += int(session.get("token_used", 0) or 0)
    return totals
