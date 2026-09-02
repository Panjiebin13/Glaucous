"""Spec 子系统命令（v1.1-M5，FR-59；自 commands.py 拆出，spec §3.5）。

/spec [需求|status|cancel]：发起全流程 / 管理面；/specs：全量列表卡。
运行时零顶层依赖（SpecPipeline/SpecStore 函数内延迟导入）——可被
commands.py 安全 re-export 供测试与分派。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import ReplContext

_STATUS_LABEL = {
    "draft": "起草中", "reviewing": "评审中", "approved": "已批准",
    "executing": "执行中", "code_review": "代码评审中",
    "verified": "已验收", "archived": "已归档",
}


def _spec_status_lines(doc) -> list[str]:
    """Spec 状态卡内容行（/spec status 与 /spec 无参共用）。"""
    tasks = doc.tasks()
    done = sum(1 for _no, finished, _t in tasks if finished)
    lines = [
        f"Spec：{doc.spec_id}（{doc.name}）",
        f"状态：{_STATUS_LABEL.get(doc.status, doc.status)} · 轮次：{doc.meta.get('round', 0)}"
        + (f" · 模式：{doc.meta['mode']}" if doc.meta.get("mode") else "")
        + (f" · 验收：{doc.meta['acceptance']}" if doc.meta.get("acceptance") else ""),
        f"任务：{done}/{len(tasks)} 已完成 · 验收标准 {len(doc.acceptance())} 条",
        f"文档：.glaucous/specs/{doc.path.name}（read_spec 可回读全文）",
    ]
    lines += [f"  [{'x' if finished else ' '}] {text}" for _no, finished, text in tasks]
    return lines


async def _cmd_spec(ctx: "ReplContext", arg: str) -> bool:
    """/spec [需求|status|cancel]（FR-59）：发起全流程 / 管理面。"""
    from .spec.pipeline import SpecPipeline
    from .spec.store import SpecStore

    store = SpecStore(ctx.workspace)
    sub = arg.strip()
    if sub == "status":
        doc = store.active() or (store.list_all() or [None])[0]
        if doc is None:
            ctx.renderer.note("尚无 Spec 文档（/spec <需求> 可发起）。")
            return True
        ctx.renderer.info("◆ Spec 状态")
        for line in _spec_status_lines(doc):
            ctx.renderer.note(f"  {line}")
        return True
    pipeline = SpecPipeline(ctx)
    if sub == "cancel":
        doc = store.active()
        if doc is None:
            ctx.renderer.note("无活跃 Spec，无需取消。")
            return True
        await pipeline.cancel(doc)
        return True
    if not sub:
        # 无参：存在 executing → 进度卡 + 继续/取消；其他非终态 → 状态提示（决策 12）
        doc = store.active()
        if doc is None:
            ctx.renderer.note("用法：/spec <需求> 发起 Spec 流程；/specs 查看全部。")
            return True
        ctx.renderer.info("◆ 当前 Spec 进度")
        for line in _spec_status_lines(doc):
            ctx.renderer.note(f"  {line}")
        if doc.status == "executing":
            await pipeline.ask_continue(doc)  # 公开方法（S3：不跨层访问 _hooks）
        else:
            ctx.renderer.note(
                f"状态 {doc.status} 不支持自动续跑（轮内上下文已失）；"
                "可 /spec cancel 归档后重新发起，已执行改动有 checkpoint 兜底。"
            )
        return True
    await pipeline.start(sub)
    return True


async def _cmd_specs(ctx: "ReplContext") -> bool:
    """/specs：全量列表卡（id/name/status/round/updated_at，倒序）。"""
    from .spec.store import SpecStore

    store = SpecStore(ctx.workspace)
    docs = store.list_all()
    for warning in getattr(store, "warnings", []):
        ctx.renderer.note(f"⚠ {warning}")
    if not docs:
        ctx.renderer.note("尚无 Spec 文档（/spec <需求> 可发起）。")
        return True
    ctx.renderer.info(f"◆ Spec 列表（{len(docs)} 个）")
    for doc in docs:
        ctx.renderer.note(
            f"  {doc.spec_id} · {doc.name} · "
            f"{_STATUS_LABEL.get(doc.status, doc.status)} · 轮次 {doc.meta.get('round', 0)} · "
            f"{doc.meta.get('updated_at', '')}"
        )
    return True
