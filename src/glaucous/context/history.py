"""对话历史：OpenAI 角色语义的消息模型 + view() API 格式转换 + JSONL 持久化。

消息序列硬约束（OpenAI 协议）：role=tool 消息必须紧跟包含对应
tool_call_id 的 assistant(tool_calls) 消息之后——因此主循环在
dispatch 之前先 push_assistant，push_tool 携带 call_id 配对入史。

持久化（Day 2 任务 0.14，概设 §4.2）：
- 每个 push_* 后立即追加写一行 JSON（ensure_ascii=False）——崩溃/中断
  不丢已发生消息（FR-05）；
- load 恢复时做尾部配对校验与修复：进程在 dispatch 期间被硬杀会留下
  完整 assistant(tool_calls) 行而无配对 tool 行，恢复出的序列必被
  API 400 拒绝；修复策略与 loop 内存善后（_salvage_dangling_calls）
  语义对齐——为悬空 call_id 补推 ok=False 的 ToolMessage 并写回文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..llm.client import AssistantMessage, ToolCall
from ..tools.base import ToolResult

SESSION_META_TYPE = "session_meta"

# 视图变换（Day4 Plan D11）：方案全文不常驻上下文（概设 §5.2）——
# 发给 API 的视图中，submit_plan 的 arguments.plan 被确定性替换为锚文本；
# 内部存储与 JSONL 保留原文（D1 全量落盘，非模型上下文）
ANCHOR_TOOL_NAME = "submit_plan"
PLAN_ANCHOR_TEXT = "【方案锚】方案全文已存档至 .glaucous/plans/，可调用 read_plan 回读全文"


@dataclass
class ToolMessage:
    """入史形态的工具结果：call_id/name 与 content/ok 打包，保证 view() 可生成合法序列。"""

    call_id: str
    name: str
    content: str
    ok: bool


@dataclass
class History:
    """自管理的对话历史（可选 JSONL 持久化）。

    CLI 与 AgentLoop 共享同一实例，REPL 跨轮次累积（Day1 Plan §4.4）；
    每轮 run() 追加 user + assistant/tool 消息，多轮上下文连续。
    system_prompt 不落盘（恢复时按当前 workspace 重建）。
    """

    system_prompt: str
    session_file: Path | None = None
    session_id: str | None = None
    _messages: list[dict[str, Any]] = field(default_factory=list)

    # -- 写入（内存 + 落盘） ------------------------------------------------

    def push_user(self, text: str) -> None:
        self._append({"role": "user", "content": text})

    def push_assistant(self, msg: AssistantMessage) -> None:
        """assistant 消息入史：文本与 tool_calls 可同时存在。

        必须在 dispatch 之前调用（tool 消息的配对前提）。
        """
        entry: dict[str, Any] = {"role": "assistant"}
        # content 显式置 None：部分网关拒绝缺失 content 键的 assistant 消息
        entry["content"] = msg.text
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in msg.tool_calls
            ]
        self._append(entry)

    def push_tool(self, call: ToolCall, result: ToolResult) -> None:
        """工具结果入史：失败结果同样入史（错误即控制信号，模型据此自纠）。

        附带 _meta（base.py 执行时记账的五字段）与 _trimmed（L1 幂等标记）——
        概设 §4.2「执行时记账，裁剪时派生」：L1 裁剪不调模型，直接由 _meta
        拼接一行摘要。submit_plan 结果额外标记 _anchor（L1 保留锚行原文，
        Day4 Plan §4.6「压缩时显式保留方案轻量锚」的 L1 层落实）。
        """
        entry = self._tool_entry(call.id, call.name, result.content)
        entry["_meta"] = dict(result.metadata) if result.metadata else {}
        entry["_trimmed"] = False
        if call.name == ANCHOR_TOOL_NAME:
            entry["_anchor"] = True
        self._append(entry)

    def push_raw_tool(self, message: ToolMessage) -> None:
        """直接入史一条工具消息（熔断善后：为悬空 call_id 补推 ok=False 结果）。"""
        entry = self._tool_entry(message.call_id, message.name, message.content)
        entry["_meta"] = {}  # 善后产物无记账，L1 派生降级格式（Day4 Plan §4.6）
        entry["_trimmed"] = False
        self._append(entry)

    def _append(self, entry: dict[str, Any]) -> None:
        """入史并立即落盘（若启用持久化）——崩溃最多丢当前这一条，不产生损坏行。"""
        self._messages.append(entry)
        if self.session_file is not None:
            self._write_line(entry)

    def _write_line(self, entry: dict[str, Any]) -> None:
        """追加写一行 JSON；磁盘 IO 异常不阻断对话（持久化尽力而为）。"""
        try:
            with self.session_file.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # -- 读取与恢复 ---------------------------------------------------------

    @property
    def messages(self) -> list[dict[str, Any]]:
        """内部消息列表（含 "_" 前缀内部键）——仅供上下文管理组件
        （budget 估算 / compactor 裁剪压缩）原位读写；发给 API 一律走 view()。"""
        return self._messages

    def view(self) -> list[dict[str, Any]]:
        """生成发给 API 的完整消息序列（system + 全部历史）。

        视图变换（Day4 Plan §3/D11，纯函数、不改内部状态、幂等，resume 重放
        同样生效）：
        - 剥除 "_" 前缀内部键——含内部键的 entry 返回浅拷贝再过滤，无内部键的
          entry 沿用原引用（不原地删改，保住单一数据源）；
        - submit_plan 的 arguments.plan 替换为锚文本（方案全文不常驻上下文，
          概设 §5.2）；JSON 解析失败时整体替换为 {"plan": 锚文本}，不抛错。
        """
        return [
            {"role": "system", "content": self.system_prompt},
            *(self._public_entry(entry) for entry in self._messages),
        ]

    def _public_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """单条消息的视图变换：剥内部键 + 方案锚替换（详见 view()）。"""
        public: dict[str, Any] | None = None
        if any(key.startswith("_") for key in entry):
            public = {k: v for k, v in entry.items() if not k.startswith("_")}
        source = public if public is not None else entry
        calls = source.get("tool_calls")
        if source.get("role") == "assistant" and calls:
            if any(c.get("function", {}).get("name") == ANCHOR_TOOL_NAME for c in calls):
                if public is None:
                    public = dict(entry)
                public["tool_calls"] = [self._anchor_call(c) for c in calls]
        return public if public is not None else entry

    @staticmethod
    def _anchor_call(call: dict[str, Any]) -> dict[str, Any]:
        """submit_plan 调用的 arguments 锚替换；其他调用原样返回。"""
        fn = call.get("function", {})
        if fn.get("name") != ANCHOR_TOOL_NAME:
            return call
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        args["plan"] = PLAN_ANCHOR_TEXT
        return {**call, "function": {**fn, "arguments": json.dumps(args, ensure_ascii=False)}}

    # -- 会话文件管理 -------------------------------------------------------

    @staticmethod
    def create_session_file(workspace: Path) -> Path:
        """生成新会话文件路径：.glaucous/sessions/<时间戳>-<随机后缀>.jsonl。"""
        import secrets

        sessions_dir = workspace / ".glaucous" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return sessions_dir / f"{stamp}-{secrets.token_hex(2)}.jsonl"

    @classmethod
    def create(cls, system_prompt: str, workspace: Path) -> "History":
        """创建带持久化的新会话：首行写 session_meta。"""
        session_file = cls.create_session_file(workspace)
        session_id = session_file.stem
        history = cls(system_prompt=system_prompt, session_file=session_file, session_id=session_id)
        meta = {
            "type": SESSION_META_TYPE,
            "session_id": session_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(workspace.resolve()),
        }
        try:
            with session_file.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return history

    @classmethod
    def load(cls, session_file: Path, system_prompt: str) -> "History":
        """从 JSONL 恢复会话，返回（历史实例, 工作区路径, 修复告警列表）。

        恢复语义：
        - 首行 session_meta 校验格式（缺失/非法则拒绝恢复）；
        - 逐行还原消息，损坏行（非法 JSON，如崩溃时的半行）停止读取，
          保留已还原部分——宁可少恢复也不崩；
        - 尾部配对校验：为悬空 tool_call_id（硬杀窗口产物）补推
          ok=False 的 ToolMessage，修复行写回文件防止二次恢复重复修复。
        """
        warnings: list[str] = []
        lines = session_file.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise ValueError(f"会话文件为空: {session_file}")

        meta = json.loads(lines[0])
        if meta.get("type") != SESSION_META_TYPE:
            raise ValueError(f"会话文件首行不是 session_meta: {session_file}")

        messages: list[dict[str, Any]] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # 崩溃半行：丢弃本行及之后（追加写语义下后续行不可信）
                warnings.append(f"发现损坏行，已停止读取并丢弃其后内容：{line[:50]}…")
                break
            messages.append(entry)

        history = cls(
            system_prompt=system_prompt,
            session_file=session_file,
            session_id=meta.get("session_id", session_file.stem),
        )
        history._messages = messages
        history._repair_dangling_tool_calls(warnings)
        # workspace 缺失时返回 None（CLI 侧跳过一致性比较而非误报「与 . 不一致」）
        raw_workspace = meta.get("workspace")
        return history, (Path(raw_workspace) if raw_workspace else None), warnings

    def _repair_dangling_tool_calls(self, warnings: list[str]) -> None:
        """尾部配对校验与修复（B1 关键设计）。

        合法性论证：loop 逐个顺序 push_tool 且异常路径有内存善后，
        崩溃只可能在尾部留下悬空、已落盘 tool 消息必为前缀——故
        补推行追加至尾部即满足配对约束；自尾部向前扫描，遇到首个
        完整配对的 assistant 即可终止。
        """
        repaired: list[dict[str, Any]] = []
        for entry in reversed(self._messages):
            if entry.get("role") == "assistant" and entry.get("tool_calls"):
                answered = {
                    m.get("tool_call_id")
                    for m in self._messages
                    if m.get("role") == "tool" and m.get("tool_call_id")
                }
                dangling = [c for c in entry["tool_calls"] if c.get("id") not in answered]
                if not dangling:
                    break  # 首个完整配对的 assistant：更早的消息必已配对
                for call in dangling:
                    repaired.append(
                        self._tool_entry(
                            call.get("id", ""),
                            call.get("function", {}).get("name", "unknown"),
                            "会话中断，该调用结果未落盘",
                        )
                    )
                    warnings.append(
                        f"检测到悬空工具调用 {call.get('function', {}).get('name', 'unknown')}，"
                        "已补推中断占位结果（原结果因会话中断未落盘）"
                    )
                break  # 仅尾部一轮可能悬空（见论证），处理完即终止
        if repaired:
            # 修复行按 tool_calls 原序追加与写回（reversed 只影响外层扫描，
            # 追加必须保持 dangling 顺序，否则多 call 场景 tool 消息与
            # tool_calls 顺序颠倒，严格网关按序校验会 400）：
            # 二次恢复时 answered 已含修复行 id，不会重复修复
            for entry in repaired:
                self._messages.append(entry)
                self._write_line(entry)

    @staticmethod
    def _tool_entry(call_id: str, name: str, content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": content,
        }
