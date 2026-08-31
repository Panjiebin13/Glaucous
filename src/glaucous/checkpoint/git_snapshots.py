"""Git 快照子进程封装（v1.1-M4 任务 4.1，FR-40；spec §3.1）。

设计（spec 决策 1，对概设 §5.1/§5.2 的显式修正）：
- `git stash create` 只快照已跟踪文件、无法捕获 untracked（概设 §5.2 性能行的
  `--include-untracked` 表述不成立——stash create 无此参数），改用临时索引：
  GIT_INDEX_FILE=<临时索引> read-tree → add -A → rm --cached（任意层级排除
  .glaucous，r3-B6：两条 glob pathspec 拆独立调用，避免多 pathspec 原子匹配
  检查整条失败）→ write-tree → commit-tree → update-ref；
- 全程不触碰用户工作树与真实索引、不污染 stash 列表；对象有 ref 引用不被 gc；
- 零新依赖（需求 §5 约束 6）：全部经 git 命令子进程，超时/非零码 → GitError。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class GitError(RuntimeError):
    """git 命令非零退出码/超时/找不到 git。"""


def _run(root: Path, *args: str, timeout: int = 30, env_extra: dict[str, str] | None = None) -> str:
    """执行 git 子命令：cwd=root，超时/非零码 → GitError（带 stderr 摘要）。

    统一注入 core.quotepath=off（B1）：git 默认把非 ASCII 路径输出为八进制
    转义串，不关掉则中文等文件名在 ls-files/ls-tree/diff 输出中无法还原，
    A 项 unlink 与变更清单会静默失效。
    """
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise GitError(f"找不到 git 命令：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} 超时（{timeout}s）") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise GitError(f"git {' '.join(args)} 失败：{detail}")
    return proc.stdout.strip()


def is_git_workspace(workspace: Path) -> bool:
    """rev-parse 探测：非 Git 工作区 / 无 git 命令 → False（不自动 init，概设 §5.2）。"""
    try:
        _run(workspace, "rev-parse", "--is-inside-work-tree")
        return True
    except GitError:
        return False


def repo_root(workspace: Path) -> Path:
    """工作区所属仓库根（rev-parse --show-toplevel）——快照/回退统一以根为 cwd。"""
    return Path(_run(workspace, "rev-parse", "--show-toplevel"))


def head_commit(root: Path) -> str | None:
    """当前 HEAD commit；空仓库（无任何提交）→ None。"""
    try:
        return _run(root, "rev-parse", "HEAD")
    except GitError:
        return None


def _excluded(path: str, excludes) -> bool:
    """路径是否落在排除目录下（B5：excludes 为目录名列表，匹配任意层级的
    目录段——如 ".glaucous" 命中根级与任何子目录下的 .glaucous）。"""
    segments = path.split("/")
    return any(seg in excludes for seg in segments)


def create_snapshot(root: Path, message: str, excludes=(".glaucous",)) -> str:
    """临时索引五步生成全量工作树快照，返回 commit hash（spec 决策 1）。

    - 快照含 untracked（add -A 尊重 .gitignore，gitignored 文件不进快照，S8）；
    - excludes（决策 5）：排除的目录名（任意层级，B5）——add 后经 rm --cached
      从临时索引移除；不用 add 的 exclude pathspec，它命中被忽略路径时报错
      「Use -f」（实测）；无匹配时 rm 报错同样容忍（try/except）；
    - commit-tree 经 -c 注入身份，不依赖用户全局 git 配置；
    - 空仓库（head=None）：read-tree 空树、commit-tree 省略 -p。
    """
    head = head_commit(root)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="glaucous-ckpt-", suffix=".index")
    os.close(tmp_fd)
    tmp_index = tmp_name
    try:
        ident = ("-c", "user.name=glaucous", "-c", "user.email=glaucous@local")
        if head:
            _run(root, *ident, "read-tree", "HEAD", env_extra={"GIT_INDEX_FILE": tmp_index})
        else:
            _run(root, *ident, "read-tree", "--empty", env_extra={"GIT_INDEX_FILE": tmp_index})
        _run(root, *ident, "add", "-A", "--", ".", env_extra={"GIT_INDEX_FILE": tmp_index})
        for e in excludes:
            # r3-B6：两条 glob 拆独立调用——git rm 多 pathspec 的匹配检查是
            # 原子的，合并调用在「深层命中 + 根级无条目」组合下整条失败
            for pattern in (f":(glob)**/{e}/**", f":(glob){e}/**"):
                try:
                    _run(
                        root, *ident, "rm", "-q", "--cached", "-r",
                        "--", pattern,
                        env_extra={"GIT_INDEX_FILE": tmp_index},
                    )
                except GitError:
                    pass  # 该模式无匹配条目：排除目标本就不存在，容忍
        tree = _run(root, "write-tree", env_extra={"GIT_INDEX_FILE": tmp_index})
        commit_args = [*ident, "commit-tree", tree, "-m", message]
        if head:
            commit_args += ("-p", head)
        commit = _run(root, *commit_args)
    finally:
        try:
            os.unlink(tmp_index)
        except OSError:
            pass
    return commit


def diff_against(root: Path, ref: str, excludes=(".glaucous",)) -> list[dict]:
    """工作树相对快照 ref 的变更清单 → [{status: M/D/A, path}]（spec §3.1）。

    - M/D：`git diff --name-status <ref>`（已跟踪文件的修改/删除；R/C 重命名
      取旧路径列为还原项，S6——新路径不在 ref 树中会进 A 项移除清单）；
    - A（B1）：diff 产不出 untracked 新增——另行计算：
      tracked(ls-files) ∪ untracked非忽略(others --exclude-standard) − ref 树集合；
    - 两路结果均按 excludes 过滤（B2：任意层级 .glaucous/ 不进回退面）；
      gitignored 文件天然不在 tracked/others 内（S8 显式设计）。
    """
    changes: list[dict] = []
    out = _run(root, "diff", "--name-status", ref, "--", ".")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        status = parts[0][0]
        if status in "MD":
            path = parts[1]
        elif status in "RC":
            path = parts[1]  # 重命名/复制：旧路径按还原处理（S6）
            status = "M"
        else:
            continue
        if _excluded(path, excludes):
            continue
        changes.append({"status": status, "path": path.strip('"')})
    tree_files = set(_run(root, "ls-tree", "-r", "--name-only", ref).splitlines())
    tracked = set(_run(root, "ls-files").splitlines())
    others = set(_run(root, "ls-files", "--others", "--exclude-standard").splitlines())
    for path in sorted((tracked | others) - tree_files):
        if _excluded(path, excludes):
            continue
        changes.append({"status": "A", "path": path})
    return changes


def restore_from(root: Path, ref: str, excludes=(".glaucous",)) -> None:
    """从快照还原工作树与索引（M/D 项；A 项由 store 移除）。

    r3-B7：必须保留 exclude pathspec——pathspec 还限定 restore 触碰的路径
    集合，若省略，「用户 tracked 的 .glaucous 文件」（索引有、ref 树无）会被
    整体删除（审计失真 + 静默数据丢失，决策 5 直接违反）。"""
    pathspecs = [
        f"--source={ref}", "--worktree", "--staged", "--", ".",
        *(f":(exclude,glob)**/{e}/**" for e in excludes),
        *(f":(exclude,glob){e}/**" for e in excludes),
    ]
    _run(root, "restore", *pathspecs)


def update_ref(root: Path, ref: str, commit: str) -> None:
    """登记快照引用（refs/glaucous/checkpoints/<seq>，不被 gc）。"""
    _run(root, "update-ref", ref, commit)


def delete_ref(root: Path, ref: str) -> None:
    """删除快照引用（保留淘汰；update-ref -d 对已删 ref 幂等报错由调用方容忍）。"""
    _run(root, "update-ref", "-d", ref)
