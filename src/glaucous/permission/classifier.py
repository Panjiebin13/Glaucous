"""bash 危险命令分类器：首词白名单 + 参数模式表 + 保守升级（任务 1.2）。

设计要点（概设 §5.5，Day3 Plan §4.2）：
- classify(command, workspace) 注入工作区——命令内路径参数（rm/mv/`>` 目标/cat 目标）
  经 workspace 判定区外/受保护目录，对齐概设 §5.4「shell 命令中的路径参数同样规范化检查」；
- 白名单只读命令 → SAFE（Plan/Build 均放行）；但白名单命令若含写变体
  （重定向/删文件/python 任意代码/sed -i/find -delete）→ 按写操作定级；
- 参数模式表命中（rm -rf /、git push --force、sudo、curl|sh 等）→ DANGEROUS；
- 无法判定 → 保守升级 WRITE 走审批（宁多问不漏放，概设 §5.5）；
- 返回 (Risk, 匹配说明)，说明供审批展示与审计。
"""

from __future__ import annotations

from .risk import Risk
from .workspace import Workspace

# 首词白名单：只读探测命令（POSIX 面向 Linux 一等公民；Windows cmd 命令未命中按保守升级 WRITE）。
# cd 无害（子进程 cwd 由工具控制为工作区，段内跳目录不影响后续段独立定级）
SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        "ls", "cat", "head", "tail", "wc", "find", "pwd", "which", "echo",
        "git", "rg", "grep", "sed", "awk", "cd",
    }
)

# 首词本身即危险（无条件 DANGEROUS）
DANGEROUS_COMMANDS: frozenset[str] = frozenset(
    {
        "sudo", "su", "shutdown", "reboot", "halt", "poweroff", "kill", "killall",
        "mkfs", "dd", "chmod", "chown", "passwd", "fdisk", "mkfs.*",
    }
)

# git 子命令参数模式（git status/diff/log 只读，其余写）
GIT_READONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {"status", "diff", "log", "show", "branch", "remote", "ls-files", "rev-parse", "config --list"}
)
GIT_DANGEROUS_PATTERNS: tuple[str, ...] = (
    "push --force", "push -f", "reset --hard", "clean -fd", "clean -f", "checkout -- .",
)

# 危险命令参数模式（对首词是 rm/mv/curl 等时进一步判定）
DANGEROUS_RM_PATTERNS: tuple[str, ...] = ("-rf /", "-rf ~", "-fr /", "-fr ~", "-rf $HOME", "/*", " -rf .")
# curl/wget | sh：兼容有无空格（`|sh`/`| sh`）两种写法
DANGEROUS_PIPE_PATTERNS: tuple[str, ...] = ("| sh", "|sh", "| bash", "|bash", "| zsh", "|zsh", "| python", "|python")

# 写变体标记（白名单命令出现这些则不再视为只读）
INPLACE_MARKERS: tuple[str, ...] = ("-i", "-i.bak")
DELETE_MARKERS: tuple[str, ...] = ("-delete", "-exec rm", "-exec rm -rf")


class CommandClassifier:
    """命令定级器：纯函数式，注入 workspace 判定命令内路径区外。"""

    def __init__(self, workspace: Workspace):
        self._workspace = workspace

    def classify(self, command: str) -> tuple[Risk, str]:
        """定级命令：返回 (Risk, 匹配说明)。

        先拆分复合命令（; / && / 管道 |），对每段递归定级取最坏风险——
        非首段危险命令（`echo a; rm -rf /`、`x && sudo rm -rf /`、`echo foo | rm -rf /`）
        不可因白名单首词被整体放行（B2r1/Blocker-1 修复）。
        """
        stripped = command.strip()
        if not stripped:
            return Risk.WRITE, "空命令按保守升级处理"
        segments = _split_segments(stripped)
        if len(segments) > 1:
            worst: Risk | None = None
            worst_note = ""
            for seg in segments:
                risk, note = self.classify(seg)
                if risk == Risk.DANGEROUS:
                    return Risk.DANGEROUS, f"复合命令段包含危险操作: {note}"
                if worst is None or _risk_rank(risk) > _risk_rank(worst):
                    worst, worst_note = risk, note
            return worst, worst_note

        # 管道处理（Blocker-1 修复）：单段内若含 |，按 | 切分逐段独立定级——
        # 管道右端命令独立执行，不可被左端白名单首词遮蔽。
        # 但 curl|sh / wget|sh 这类「下载并执行」需基于完整命令串检测，
        # 故先做整串管道模式检测，再逐段定级。
        pipe_parts = _split_pipes(stripped)
        if len(pipe_parts) > 1:
            # 完整命令串含 curl/wget|sh → DANGEROUS（无论首词）。
            # 引号感知（S1 修复）：引号内字面量（如 `echo 'x | sh'`）不误报——
            # 先剥离引号内容再匹配 `| sh` 模式
            unquoted = _strip_quoted(stripped)
            if any(p in unquoted for p in DANGEROUS_PIPE_PATTERNS):
                return Risk.DANGEROUS, "远程下载并通过管道执行（curl|sh）"
            worst: Risk | None = None
            worst_note = ""
            for part in pipe_parts:
                risk, note = self.classify(part)
                if risk == Risk.DANGEROUS:
                    return Risk.DANGEROUS, f"管道段包含危险操作: {note}"
                if worst is None or _risk_rank(risk) > _risk_rank(worst):
                    worst, worst_note = risk, note
            return worst, worst_note

        first_word = stripped.split()[0]

        # 1) 首词即危险（无条件 DANGEROUS）
        if first_word in DANGEROUS_COMMANDS or first_word.startswith("mkfs"):
            return Risk.DANGEROUS, f"命令首词 {first_word} 位于危险命令表"

        # 2) rm 系列：参数含危险模式或删除区外/受保护 → DANGEROUS；区内删除 → WRITE
        if first_word == "rm":
            return self._classify_rm(stripped)

        # 3) curl/wget | sh 管道执行（单段且含管道时已被上方管道分支处理）
        if first_word in ("curl", "wget") and any(p in stripped for p in DANGEROUS_PIPE_PATTERNS):
            return Risk.DANGEROUS, "远程下载并通过管道执行（curl|sh）"

        # 4) mv 移出工作区
        if first_word == "mv":
            return self._classify_mv(stripped)

        # 5) git 系列
        if first_word == "git":
            return self._classify_git(stripped)

        # 6) python/python3：仅 -m pytest 只读探测；其余（-c 任意代码/脚本）按写保守 WRITE
        if first_word in ("python", "python3"):
            if "-m pytest" in stripped or " -m pytest" in stripped:
                return Risk.SAFE, f"{first_word} -m pytest 只读测试运行"
            return Risk.WRITE, f"{first_word} 执行代码，保守升级为写操作审批"

        # 7) 白名单命令：检查是否含写变体（重定向/原地编辑/删除）
        if first_word in SAFE_COMMANDS:
            return self._classify_safe_command(first_word, stripped)

        # 8) 保守升级：无法判定的命令按 WRITE 走审批（宁多问不漏放）
        return Risk.WRITE, f"无法判定命令 {first_word}，保守升级为写操作审批"

    # -- 内部细分 ----------------------------------------------------------

    def _classify_rm(self, command: str) -> tuple[Risk, str]:
        """rm 删除：危险模式/区外/受保护 → DANGEROUS；区内删除 → WRITE。"""
        if any(p in command for p in DANGEROUS_RM_PATTERNS):
            return Risk.DANGEROUS, "rm 命中危险删除模式（根目录/家目录/工作区根）"
        # 扫描删除目标路径：区外/受保护 → DANGEROUS；区内 → WRITE（删除是写操作）
        for token in self._tokens_after(command):
            risk = self._path_risk(token, writing=True)
            if risk != Risk.SAFE:
                return risk, f"rm 删除路径指向 {token}"
        # 目标全在区内：删除是写操作，绝不低于 WRITE
        return Risk.WRITE, "rm 区内删除（写操作）"

    def _classify_safe_command(self, first: str, command: str) -> tuple[Risk, str]:
        """白名单命令细分：含重定向/原地编辑/删除等写变体 → 按写定级；纯读 → SAFE。

        入口 classify 已拆分复合命令，此处 command 必为单段（B2r1 修复）；
        重定向扫描所有目标（> / >> / 2> / 2>&1），任一区外/受保护 → DANGEROUS，
        任一区内 → WRITE（不再命中即返回，B2 复评修复）。
        """
        # 重定向风险（单段）
        risk, note = self._classify_segment(first, command)
        if risk is not None:
            return risk, note
        # 无重定向：原地编辑/删除变体
        if first in ("sed", "awk", "find", "perl"):
            for marker in INPLACE_MARKERS + DELETE_MARKERS:
                if marker in command:
                    return Risk.WRITE, f"{first} 含原地编辑/删除变体（写操作）"
        # 读文件类：路径指向区外 → WRITE（读区外需审批，FR-13）
        if first in ("cat", "head", "tail", "grep", "rg", "sed", "awk"):
            for token in self._tokens_after(command):
                if token.startswith("-") or token.startswith((">", "<", "|", "&&", "||")):
                    continue
                risk = self._path_risk(token, writing=False)
                if risk != Risk.SAFE:
                    return risk, f"{first} 读取路径指向 {token}"
            return Risk.SAFE, f"{first} 只读命令"
        if first in ("ls", "find", "echo", "pwd", "which", "wc", "cd"):
            return Risk.SAFE, f"{first} 只读/探测命令"
        return Risk.SAFE, "白名单只读命令"

    def _classify_segment(self, first: str, segment: str) -> tuple[Risk | None, str]:
        """单段命令的重定向风险：None=无重定向；Risk=最坏风险。

        扫描段内所有重定向（> / >> / 2> / 2>> / 2>&1），对每个符号取其后目标，
        逐一 _path_risk(writing=True) 取最坏——不因命中首个即返回（B2 复评修复）。
        """
        targets = self._collect_redirect_targets(segment)
        # v1.1 修订（用户实测误报）：丢弃输出的惯用法（2>/dev/null）无写语义，
        # 不再判 DANGEROUS（此前每次 2>/dev/null 都弹审批卡）
        targets = [t for t in targets if t.strip("'\"") != "/dev/null"]
        if not targets:
            return None, ""
        worst: Risk | None = None
        for target in targets:
            risk = self._path_risk(target, writing=True)
            if risk == Risk.DANGEROUS:
                return Risk.DANGEROUS, f"重定向写目标指向 {target}"
            if risk != Risk.SAFE and worst is None:
                worst = risk
        return (worst if worst is not None else Risk.WRITE), "重定向写入（写操作）"

    @staticmethod
    def _collect_redirect_targets(segment: str) -> list[str]:
        """收集段内所有重定向目标（fd 重定向如 2>&1 的目标是数字/& 会被跳过）。

        支持 `> target`、`>target`、`>> target`、`2> target`、`2>&1` 等写法；
        紧凑写法（`a>/path`）也能识别（符号夹在 token 中间）。
        """
        import re as _re

        targets: list[str] = []
        # 匹配重定向符号：可选 fd 前缀 + > 或 >> 或 >&（2>&1/>&2 由正则整体吞掉，目标不单独出现）
        for m in _re.finditer(r"\d*>>?|\d*>&\d*", segment):
            start = m.end()
            rest = segment[start:].lstrip()
            if not rest:
                continue
            token = rest.split()[0]
            # 跳过 fd 重定向目标（&1、&2 形式）；纯数字目标是文件名（`2>1` 的 1 是文件），保留
            if token.startswith("&"):
                continue
            targets.append(token)
        return targets

    def _classify_git(self, command: str) -> tuple[Risk, str]:
        """git 子命令细分：只读子命令 SAFE；push --force 等 DANGEROUS；其余写 WRITE。"""
        tokens = command.split()
        if len(tokens) < 2:
            return Risk.WRITE, "git 缺少子命令"
        sub = tokens[1]
        if sub in GIT_READONLY_SUBCOMMANDS:
            return Risk.SAFE, f"git {sub} 只读"
        if sub in ("push", "reset", "clean", "checkout"):
            for pat in GIT_DANGEROUS_PATTERNS:
                if pat in command:
                    return Risk.DANGEROUS, f"git {sub} 命中危险模式: {pat}"
        if sub in ("commit", "add", "mv", "rm", "push", "merge", "rebase", "tag"):
            return Risk.WRITE, f"git {sub} 写操作"
        return Risk.WRITE, f"git {sub} 无法判定，保守升级"

    def _classify_mv(self, command: str) -> tuple[Risk, str]:
        """mv：目标/源移出区外或受保护 → DANGEROUS；其余 mv → WRITE（区内移动）。"""
        tokens = command.split()
        if len(tokens) < 3:
            return Risk.WRITE, "mv 参数不足"
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            risk = self._path_risk(token, writing=True)
            if risk != Risk.SAFE:
                return risk, f"mv 路径指向 {token}"
        return Risk.WRITE, "mv 区内移动（写操作）"

    # -- 辅助 --------------------------------------------------------------

    def _path_risk(self, token: str, *, writing: bool) -> Risk:
        """判定命令内路径参数风险：
        - 写操作指向受保护目录（.glaucous/）→ DANGEROUS（防篡改审计）
        - 写操作指向区外 → DANGEROUS
        - 读操作指向区外 → WRITE（读区外需审批，可同类型豁免，v1.1 修订）
        - 读操作指向受保护目录 → SAFE（v1.1 用户决策：区内读一律放行）
        - 区内 → SAFE

        先剥离引号（'/"）再 resolve：引号包裹是 shell 最常见写法，若保留引号
        字符，`'/etc/passwd'` 会被当作相对路径拼到工作区根误判区内（B3r2-1 修复）。
        """
        if token.startswith((">", "<", "|", "&&", "||", "-")):
            return Risk.SAFE
        clean = token.strip("'\"")
        if not clean:
            return Risk.SAFE
        # `~/...` / `~user/...`：shell 展开到家目录，工作区外（FR-13 区外需审批）。
        # 不实际展开（跨平台 home 获取开销），直接按区外处理
        if clean.startswith("~"):
            return Risk.DANGEROUS if writing else Risk.WRITE
        try:
            resolved = self._workspace.resolve(clean)
        except (OSError, RuntimeError):
            return Risk.DANGEROUS if writing else Risk.WRITE
        if self._workspace.is_protected(resolved):
            # v1.1 修订（用户决策 2026-08-30）：保护语义收窄为写完整性——
            # 读 .glaucous/ 运行日志（审计/会话）与区内普通读同等（SAFE）
            return Risk.DANGEROUS if writing else Risk.SAFE
        if not self._workspace.is_within(resolved):
            return Risk.DANGEROUS if writing else Risk.WRITE
        return Risk.SAFE

    @staticmethod
    def _tokens_after(command: str) -> list[str]:
        """取命令中非选项 token（跳过首词与 - 开头/管道/重定向符号）。"""
        return [t for t in command.split()[1:] if not t.startswith(("-", ">", "<", "|", "&"))]


def _split_segments(command: str) -> list[str]:
    """按分号/&& 拆分复合命令段（每段独立定级）。

    - 引号感知：逐字符扫描跟踪单/双引号与转义，引号内 `;`/`&&` 不作分隔符
      （B2r2 修复：`grep ';' f.py`、`echo '; rm -rf /'`、`sed 's/;/x/g' f` 不误拆）；
    - 覆盖紧凑 `a&&b` 与带空格 `a && b`；
    - 单 &（后台符）不拆；管道（|）不拆——管道两端属同一命令读写流。
    """
    segments: list[str] = []
    current: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False

    def flush_token() -> None:
        if buf:
            current.append("".join(buf))
            buf.clear()

    def flush_segment() -> None:
        flush_token()
        if current:
            segments.append(" ".join(current))
            current.clear()

    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            # 反斜杠转义（单引号内无反斜杠转义）
            escaped = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in " \t":
            flush_token()
            i += 1
            continue
        # 引号外分隔符
        if ch == ";":
            flush_segment()
            i += 1
            continue
        if ch == "&":
            # fd 重定向 `2>&1`/`>&2`：& 前是 `>`（可选数字 fd 前缀），不是命令分隔符
            joined = "".join(buf)
            if joined.endswith(">") or (len(joined) >= 2 and joined[-2] == ">" and joined[-1].isdigit()):
                buf.append(ch)
                i += 1
                continue
            # && 与单 & 都分隔命令（bash 语义）；单 & 后台符同样拆段，
            # 否则 `echo foo & rm -rf /` 被合并为单段被白名单首词遮蔽（B3r2-2 修复）
            flush_segment()
            i += 2 if (i + 1 < n and command[i + 1] == "&") else 1
            continue
        buf.append(ch)
        i += 1
    flush_segment()
    return segments


def _risk_rank(risk: Risk) -> int:
    """风险排序辅助（SAFE < WRITE < DANGEROUS），复合命令取最坏段。"""
    return {Risk.SAFE: 0, Risk.WRITE: 1, Risk.DANGEROUS: 2}.get(risk, 0)


def _strip_quoted(command: str) -> str:
    """剥离引号内内容（替换为空格），保留引号外结构供模式匹配。

    用于 curl|sh 等模式检测——`echo 'x | sh'` 的引号内字面量不应触发
    `| sh` 危险模式（S1 修复）。
    """
    out: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in command:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        out.append(ch)
    return "".join(out)


def _split_pipes(command: str) -> list[str]:
    """按管道 | 拆分（引号感知）：管道右端命令独立执行，须独立定级。

    引号内 `|` 不作分隔符；`||`（或）按单个管道处理（两分支都需定级）。
    """
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "|":
            # 引号外管道：`||` 拆两段；单 `|` 拆两段
            parts.append("".join(buf).strip())
            buf = []
            if i + 1 < n and command[i + 1] == "|":
                i += 2
            else:
                i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return [p for p in parts if p]
