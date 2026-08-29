"""上下文压缩：L1 历史裁剪 + L2 摘要压缩（任务 2.6/2.7，FR-24，概设 §4.2）。

分级策略（概设 §4.2，由 loop 守卫点按预算档位调度）：
- L1 裁剪（>70%）：从旧到新淘汰「最近 K 轮之外」的已完成轮次 tool 结果正文，
  替换为 _meta 本地派生的一行摘要——不调模型、零 token、确定性、无幻觉；
  assistant 文本一律保留；_anchor 条目（submit_plan 决策回喂）以锚行原文充当
  摘要（方案轻量锚的 L1 层保留）；
- L2 压缩（>85%）：早期历史（除最近 K 轮外）经模型压成一条合成摘要消息，
  显式拼接方案锚段——「压缩后任务目标与当前方案不丢失」（FR-24）；
  失败降级 L1 加深（keep_recent 递减），不阻断主流程（D4）。

轮的定义（Day4 Plan S-13）：一个完整轮 = 一条 assistant(tool_calls) 消息及其后
全部配对的 role=tool 消息；无 tool_calls 的 assistant（终答）单独成轮。
轮边界由 history 顺序结构唯一确定。裁剪/压缩只改内存不重写 JSONL（D1：
会话文件保留全量原文，resume 后按预算重新裁剪）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

# L1 保留的最近轮数 / L2 连续失败上限（后者防压缩调用循环，Day4 Plan D12）
L1_KEEP_RECENT_ROUNDS = 2
MAX_L2_FAILURES = 2

SUMMARY_PREFIX = "【会话阶段摘要·系统压缩生成】"
_SUMMARY_MAX_CHARS = 1000  # 约 500 字（概设 §4.2 L2「≤500 字任务摘要」的字符上限）

# 压缩模型调用的最小协议（LLMClient 满足；便于测试注入替身）
class _CompressLLM(Protocol):
    async def chat(self, messages: list[dict[str, Any]], tools=None, on_text=None): ...


# ---------------------------------------------------------------------------
# L1 历史裁剪
# ---------------------------------------------------------------------------


def _meta_summary(entry: dict[str, Any]) -> str:
    """从 _meta 派生一行摘要（概设 §4.2 格式）；无记账时降级为「⎿ 名 · 结果」。"""
    meta = entry.get("_meta") or {}
    name = entry.get("name") or meta.get("tool") or "tool"
    ok = meta.get("ok", True)
    if not meta:
        return f"⎿ {name} · {'成功' if ok else '失败'}"
    parts = [f"⎿ {name}"]
    brief = meta.get("args_brief")
    if brief:
        parts.append(str(brief))
    parts.append("成功" if ok else "失败")
    duration = meta.get("duration_ms")
    if duration:
        parts.append(f"{duration}ms")
    lines = meta.get("lines")
    if lines:
        parts.append(f"{lines} 行")
    return " · ".join(parts)


def trim_history(messages: list[dict[str, Any]], keep_recent: int = L1_KEEP_RECENT_ROUNDS) -> int:
    """L1：淘汰最近 keep_recent 轮之外的 tool 结果正文为派生摘要（原位改写）。

    :returns: 本次实际裁剪的条数（已裁剪条目经 _trimmed 幂等跳过）
    """
    # 轮结构：assistant 消息开启一轮；其后连续 tool 消息归属该轮
    rounds: list[list[int]] = []
    current: list[int] | None = None
    for idx, entry in enumerate(messages):
        role = entry.get("role")
        if role == "assistant":
            current = []
            rounds.append(current)
        elif role == "tool" and current is not None:
            current.append(idx)

    # 只裁剪「含 tool 结果」的轮，最近 keep_recent 轮豁免
    trimmable = [idxs for idxs in rounds if idxs]
    trimmable = trimmable[:-keep_recent] if keep_recent > 0 else trimmable

    trimmed = 0
    for idxs in trimmable:
        for idx in idxs:
            entry = messages[idx]
            if entry.get("_trimmed"):
                continue
            if entry.get("_anchor"):
                # 方案锚行原文即摘要（决策回喂仅 1–3 行），保留原文防重写
                entry["_trimmed"] = True
                continue
            entry["content"] = _meta_summary(entry)
            entry["_trimmed"] = True
            trimmed += 1
    return trimmed


# ---------------------------------------------------------------------------
# L2 摘要压缩
# ---------------------------------------------------------------------------


def _extract_goal(plan_text: str) -> str:
    """提取方案目标行：首个以「目标」开头的行（容忍 ## / ** 标记前缀），≤80 字符。

    与 planning.py 的锚行提取同规则（Day4 Plan S-15）。
    """
    for line in plan_text.splitlines():
        stripped = line.strip().lstrip("#*").strip()
        if stripped.startswith("目标"):
            goal = stripped.lstrip("目标：: ").strip()
            return goal[:80]
    return ""


def _plan_anchor(plans_dir: Path | None) -> str:
    """构造方案锚段（S-05/S-11：compactor 内部构造，loop 只注入目录）。

    最新方案按文件名（含时间戳）字典序取最大；解析失败仅附路径。
    """
    if plans_dir is None:
        return ""
    try:
        files = sorted(plans_dir.glob("*.md"))
    except OSError:
        return ""
    if not files:
        return ""
    latest = files[-1]
    try:
        text = latest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"方案锚：.glaucous/plans/{latest.name}（内容读取失败，可用 read_plan 回读）"
    goal = _extract_goal(text)
    anchor = f"方案锚：.glaucous/plans/{latest.name}（可用 read_plan 回读全文）"
    if goal:
        anchor += f"\n方案目标：{goal}"
    return anchor


def _build_compress_prompt(early: list[dict[str, Any]], plans_dir: Path | None) -> str:
    """把早期历史序列化为压缩请求文本（带角色标注，防止角色混淆）。"""
    lines = ["请把以下此前的对话历史压缩为一份任务摘要（不超过 500 字），"
             "保留：任务目标、已确认的关键决策、已完成/进行中的工作、重要环境事实与当前方案。"
             "直接输出摘要正文，不要评论。"]
    for entry in early:
        role = entry.get("role", "?")
        if role == "tool":
            content = entry.get("content", "")
        else:
            content = entry.get("content") or ""
            calls = entry.get("tool_calls")
            if calls:
                call_brief = "；".join(
                    f"{c.get('function', {}).get('name', '?')}({c.get('function', {}).get('arguments', '')[:120]})"
                    for c in calls
                )
                content = f"{content or ''}[调用工具: {call_brief}]".strip()
        if not content:
            continue
        if len(content) > 600:
            content = content[:600] + "…"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def compact_history(
    messages: list[dict[str, Any]],
    llm: _CompressLLM,
    plans_dir: Path | None = None,
    keep_recent: int = L1_KEEP_RECENT_ROUNDS,
) -> bool:
    """L2：早期历史压成一条合成摘要消息（原位替换）。

    :returns: True=压缩成功（或无早期内容可压缩）；False=压缩失败（调用方降级
        L1 加深并计数，连续失败达上限走预算耗尽终止，Day4 Plan D12/S-01）
    """
    assistant_idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(assistant_idx) <= keep_recent:
        return True  # 早期段为空：无需压缩，视为成功
    split = assistant_idx[-keep_recent] if keep_recent > 0 else len(messages)
    early = messages[:split]
    if not early:
        return True

    try:
        reply = await llm.chat([{"role": "user", "content": _build_compress_prompt(early, plans_dir)}])
    except Exception:  # noqa: BLE001 —— 压缩失败降级而非阻断（概设 §4.4）
        return False
    summary = (getattr(reply, "text", "") or "").strip()
    if not summary:
        return False
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS]

    synthetic: dict[str, Any] = {
        "role": "user",
        "content": f"{SUMMARY_PREFIX}\n{summary}",
    }
    anchor = _plan_anchor(plans_dir)
    if anchor:
        synthetic["content"] += f"\n\n{anchor}"
    messages[:split] = [synthetic]
    return True
