"""RAGAS AspectCritic 裁判评测：三准则 × (v1.1 / v1.0) × (e1/e2/e5)。

准则：范围遵守（无范围外改动）、代码质量（正确+可读+有测试）、
报告忠实度（终答与实际改动/测试结果一致）。

运行：
    source ~/.profile   # GLAUCOUS_API_KEY
    /tmp/venv-ragas/bin/python eval/judge_ragas.py
输出：stdout 表格 + eval/results/ragas_scores.json
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import subprocess
import sys
import types
from pathlib import Path

# --- shim：ragas 0.2 顶层导入链引用 ChatVertexAI，而新版
# langchain-community 已移除该模块；注册占位模块使其通过
#（本次裁判仅使用 OpenAI 兼容通道，不会实际触碰 VertexAI）---
_fake = types.ModuleType("langchain_community.chat_models.vertexai")
_fake.ChatVertexAI = type("ChatVertexAI", (), {})
sys.modules.setdefault("langchain_community.chat_models.vertexai", _fake)

from openai import OpenAI  # noqa: E402
from ragas.dataset_schema import SingleTurnSample  # noqa: E402
from ragas.llms import llm_factory  # noqa: E402
from ragas.metrics import AspectCritic  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# 裁判模型：业界惯例裁判须强于被测模型（被测为 v4-flash，裁判用 v4-pro）；
# 保留思考模式（判断需要推理），可通过环境变量 JUDGE_MODEL 覆盖。
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek-v4-pro")
CASES = {
    "e1": REPO / "eval/cases/e1-small-task/task.md",
    "e2": REPO / "eval/cases/e2-engineering/task.md",
    "e5": REPO / "eval/cases/e5-rollback/task.md",
}

RUBRICS = {
    "scope_adherence": (
        "给定任务要求与「实际文件变更」。判定：智能体是否只做了与任务要求直接相关的"
        "改动，没有进行范围外的重构、格式化、额外功能或配置添加（即使这些额外改动"
        "看似有益，只要超出任务要求即判 0）。"
    ),
    "code_quality": (
        "给定任务要求与「实际文件变更」「实际测试执行结果」。判定：产出代码是否正确"
        "（测试通过）、可读（命名与结构符合 Python 惯例）、且有对应测试验证。全部"
        "满足才给 1。"
    ),
    "report_faithfulness": (
        "给定「智能体最终报告」与「实际文件变更」「实际测试执行结果」。判定：报告中的"
        "陈述（完成项、测试数量、修改的文件、验证结果等）是否与实际证据一致，无夸大、"
        "无虚构。完全忠实才给 1。"
    ),
}


def find_instances() -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for case in CASES:
        v11 = sorted(d for d in glob.glob(f"/tmp/eval-{case}-*") if "-v10-" not in d)
        v10 = sorted(glob.glob(f"/tmp/eval-{case}-*-v10-*"))
        out[case] = {
            "v1.1": v11[-1] if v11 else None,
            "v1.0": v10[-1] if v10 else None,
        }
    return out


def last_assistant_text(instance: str) -> str:
    """提取会话文件中最后一条非空 assistant 文本（终答/最终报告）。"""
    candidates = sorted(
        glob.glob(os.path.join(instance, ".glaucous", "sessions", "*", "*.jsonl"))
    )
    if not candidates:
        # v1.1 用户级会话存储：按首行 meta 中的工作区路径匹配
        home = os.path.expanduser("~")
        for f in sorted(glob.glob(f"{home}/.glaucous/sessions/*/*.jsonl")):
            try:
                with open(f, encoding="utf-8") as fh:
                    first = fh.readline()
                if instance in first:
                    candidates = [f]
                    break
            except OSError:
                continue
    if not candidates:
        return "(未找到会话文件)"
    texts: list[str] = []
    with open(candidates[-1], encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("role") == "assistant" and rec.get("content"):
                texts.append(str(rec["content"]))
    return texts[-1] if texts else "(无终答)"


def git_evidence(instance: str) -> str:
    def run(*args: str) -> str:
        r = subprocess.run(args, capture_output=True, text=True, cwd=instance)
        return r.stdout.strip()

    init = run("git", "rev-list", "--max-parents=0", "HEAD")
    if not init:
        return "(无 git 历史)"
    stat = run("git", "diff", init, "--stat")
    diff = run("git", "diff", init)
    return f"git diff --stat:\n{stat}\n\ndiff 摘要:\n{diff[:3000]}"


def pytest_evidence(instance: str) -> str:
    r = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-q"],
        capture_output=True, text=True, cwd=instance, timeout=120,
    )
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    return "\n".join(tail) or "(pytest 无输出)"


async def judge_one(
    judge: object, name: str, definition: str, task: str, evidence: str
) -> tuple[int, str]:
    metric = AspectCritic(name=name, definition=definition, llm=judge)
    sample = SingleTurnSample(user_input=task, response=evidence)
    result = await metric.single_turn_ascore(sample)
    if hasattr(result, "value"):
        return int(result.value), str(getattr(result, "reason", "") or "")
    return int(float(result)), ""


async def main() -> None:
    api_key = os.environ.get("GLAUCOUS_API_KEY")
    if not api_key:
        print("FAIL: GLAUCOUS_API_KEY 未设置（先 source ~/.profile）")
        return
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    # 裁判请求：放宽输出上限（防截断）；思考模式保持开启（判断需推理）
    judge = llm_factory(
        JUDGE_MODEL,
        client=client,
        max_tokens=8192,
    )

    instances = find_instances()
    scores: dict[str, dict] = {}
    rows: list[str] = []
    for case, task_file in CASES.items():
        task = task_file.read_text(encoding="utf-8").strip()
        for version, inst in instances[case].items():
            if not inst:
                rows.append(f"| {case} | {version} | （无实例） | - | - | - |")
                continue
            report = last_assistant_text(inst)
            evidence = (
                f"[智能体最终报告]\n{report[:2500]}\n\n"
                f"[实际文件变更（相对初始提交）]\n{git_evidence(inst)}\n\n"
                f"[实际测试执行结果（pytest）]\n{pytest_evidence(inst)}"
            )
            row = {"case": case, "version": version, "instance": inst}
            cells = []
            for name, definition in RUBRICS.items():
                try:
                    value, reason = await judge_one(judge, name, definition, task, evidence)
                except Exception as exc:  # noqa: BLE001
                    value, reason = -1, f"judge 异常: {exc}"
                row[name] = {"value": value, "reason": reason[:300]}
                cells.append("✅" if value == 1 else "❌" if value == 0 else "⚠")
                print(f"[{case}/{version}] {name}={value}: {reason[:120]}", flush=True)
            scores[f"{case}@{version}"] = row
            rows.append(f"| {case} | {version} | {' | '.join(cells)} |")

    print("\n| 用例 | 版本 | 范围遵守 | 代码质量 | 报告忠实度 |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(r)

    out_dir = REPO / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ragas_scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已写入 {out_dir / 'ragas_scores.json'}")


if __name__ == "__main__":
    asyncio.run(main())
