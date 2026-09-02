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
import time
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
# 裁判端点可切到任意 OpenAI 兼容网关（如裁判模型过载时改用千问）：
#   JUDGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
#   JUDGE_API_KEY=sk-*** JUDGE_MODEL=qwen3-max python eval/judge_ragas.py
# 密钥只经环境变量传入，绝不入库（凭据不写仓库/记忆）。
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek-v4-pro")
JUDGE_BASE_URL = os.environ.get("JUDGE_BASE_URL", "https://api.deepseek.com/v1")
CASES = {
    "e1": REPO / "eval/cases/e1-small-task/task.md",
    "e2": REPO / "eval/cases/e2-engineering/task.md",
    "e3": REPO / "eval/cases/e3-agent-from-scratch/task.md",
    "e5": REPO / "eval/cases/e5-rollback/task.md",
    "e7": REPO / "eval/cases/e7-resume/task.md",
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
    """提取会话文件中最后一条实质性 assistant 文本（终答/最终报告）。

    会话布局三级兼容（实测教训 2026-09-02）：v1.1 实例内两层
    （sessions/<日>/<id>.jsonl）→ v1.0 实例内一层（sessions/<id>.jsonl）
    → v1.1 用户级集中存储（按首行 meta 的 workspace 匹配）。
    此前漏了 v1.0 一层布局，导致 v1.0 全部 report_faithfulness 因「未找到
    智能体最终报告」被误判为 0（并非真实版本差异，与 e4 check.sh 同源缺陷）。
    """
    candidates = sorted(
        glob.glob(os.path.join(instance, ".glaucous", "sessions", "*", "*.jsonl"))
    )
    if not candidates:
        # v1.0 旧布局：实例内一层（sessions/<id>.jsonl）
        candidates = sorted(
            glob.glob(os.path.join(instance, ".glaucous", "sessions", "*.jsonl"))
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
    if not texts:
        return "(无终答)"
    # 取最后一条「实质性」终答（实测教训 2026-09-02）：管道模式下若 stdin
    # 残留应答行被当作新消息，会话尾部会出现「待命。」等状态性短回复，
    # 直接取最后一条会掩盖真实任务终答 → report_faithfulness 被误判为 0
    # （e1/v1.1 实测：真实终答详述了修改与测试结果，却因尾部「待命。」被忽略）。
    substantive = [t for t in texts if len(t.strip()) >= 40]
    return substantive[-1] if substantive else texts[-1]


def git_evidence(instance: str) -> str:
    def run(*args: str) -> str:
        r = subprocess.run(args, capture_output=True, text=True, cwd=instance)
        return r.stdout.strip()

    init = run("git", "rev-list", "--max-parents=0", "HEAD")
    if not init:
        return "(无 git 历史)"
    stat = run("git", "diff", init, "--stat")
    diff = run("git", "diff", init)
    parts = [f"git diff --stat:\n{stat}", f"diff 摘要:\n{diff[:3000]}"]

    # 未跟踪文件必须纳入证据（实测教训 2026-09-02）：agent 新增的 tests/、
    # README.md 等往往未 git add，`git diff` 对它们完全不可见 → 裁判据残缺
    # 证据误判「未新增测试用例」（e1/v1.1 code_quality 被误判为 0，而
    # tests/test_calc.py 实际存在）。故补上清单 + 内容摘要。
    # 排除工具运行副产物（同日第二个实测教训）：.glaucous/ 是智能体自身
    # 状态目录（审计日志/checkpoint 索引/记忆），__pycache__ 是字节码缓存，
    # 两者均非 agent 的「范围外改动」——纳入证据会让裁判把 scope_adherence
    # 系统性误判为 0（所有 v1.1 实例都必然产生 .glaucous/）。
    _RUNTIME_NOISE = (".glaucous", "__pycache__", ".pytest_cache", ".mini_agent_log")
    untracked = [
        f for f in run("git", "ls-files", "--others", "--exclude-standard").splitlines()
        if f.strip() and not any(noise in f for noise in _RUNTIME_NOISE)
    ]
    if untracked:
        parts.append("新增（未跟踪）文件清单:\n" + "\n".join(untracked[:20]))
        snippets = []
        for name in untracked[:6]:
            try:
                with open(os.path.join(instance, name), encoding="utf-8", errors="replace") as fh:
                    snippets.append(f"--- 新增文件 {name} ---\n{fh.read(1200)}")
            except OSError:
                continue
        if snippets:
            parts.append("新增文件内容摘要:\n" + "\n\n".join(snippets))
    # 若运行副产物已进入 diff（agent 自行 git add/commit 时），给裁判明确的
    # 排除指引（不篡改证据，只加注解）
    if any(noise in stat or noise in diff for noise in _RUNTIME_NOISE):
        parts.append(
            "证据说明：.glaucous/（审计日志、checkpoint 索引、记忆）与 __pycache__/ 为"
            "智能体自身运行副产物，不属于任务范围内的代码改动，评判范围遵守时应排除。"
        )
    return "\n\n".join(parts)


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
    """单准则判定（带应用层重试退避）。

    ragas 内部仅重试 1 次，裁判模型 503（Server Overloaded）拖动会让整批
    判定报废（2026-09-02 实测：30 项全成 value=-1）——故在此层对可重试
    错误（5xx/429/超时/连接）做指数退避重试。
    """
    metric = AspectCritic(name=name, definition=definition, llm=judge)
    sample = SingleTurnSample(user_input=task, response=evidence)
    last_exc: Exception | None = None
    for attempt, delay in enumerate(_RETRY_DELAYS):
        try:
            result = await metric.single_turn_ascore(sample)
            if hasattr(result, "value"):
                return int(result.value), str(getattr(result, "reason", "") or "")
            return int(float(result)), ""
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc) or attempt == len(_RETRY_DELAYS) - 1:
                break
            print(f"  [{name}] 可重试错误，{delay}s 后重试: {str(exc)[:100]}", flush=True)
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# 重试退避梯度（秒）：裁判模型 503 过载常为分钟级，短退避无意义
_RETRY_DELAYS: tuple[int, ...] = (20, 60, 180)


def _is_retryable(exc: BaseException) -> bool:
    """可重试判定：5xx / 429 / 超时 / 连接类错误（4xx 业务错误不重试）。"""
    text = str(exc)
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return any(k in text for k in ("503", "502", "500", "429", "Overloaded", "too busy", "timed out", "timeout", "Connection"))


def _wait_judge_ready(client: OpenAI, probe_minutes: int = 40) -> bool:
    """裁判模型可用性预检：过载（503）时轮询等待恢复。

    2026-09-02 实测教训：pro 裁判整批 503 时，30 项判定全成 value=-1
    并覆盖了 ragas_scores.json 里的历史真实分数——故开跑前先预检，
    持续不可用则直接退出（不产出垃圾数据）。
    """
    deadline = time.time() + probe_minutes * 60
    attempt = 0
    while True:
        try:
            client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[{"role": "user", "content": "回复 OK"}],
                max_tokens=16,
            )
            if attempt:
                print(f"裁判模型 {JUDGE_MODEL} 已恢复（等待 {attempt} 轮），开始判定。", flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            if not _is_retryable(exc):
                print(f"裁判模型不可用（非临时错误，不重试）：{str(exc)[:200]}")
                return False
            if time.time() > deadline:
                print(
                    f"裁判模型 {JUDGE_MODEL} 持续过载（{probe_minutes} 分钟内未恢复），"
                    f"本次不产出评分以免覆盖历史数据。稍后重跑即可。"
                )
                return False
            attempt += 1
            print(f"裁判模型 {JUDGE_MODEL} 暂不可用（{str(exc)[:60]}），60s 后重试探测（第 {attempt} 次）…", flush=True)
            time.sleep(60)


def explain_verdict(
    client: OpenAI, name: str, definition: str,
    task: str, evidence: str, verdict: int,
) -> str:
    """裁判理由补采（评审修复：AspectCritic 仅返回 0/1 判定，reason 恒空）。

    用同一裁判模型对已作出的判定做一句话归因，落盘供审计——分数可解释
    是评测体系的基本要求（原版 reason 全空使评分不可复核）。
    同样带重试退避（裁判模型 503 拖动时不静默丢理由）。

    max_tokens 必须放宽（实测教训 2026-09-02）：思考模型的推理过程占用
    输出预算，300 会被 reasoning 吃光导致 message.content 为空（半数理由
    丢失）——与裁判判定同理须 3000+；仍为空时回退取 reasoning_content 尾部。
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate(_RETRY_DELAYS):
        try:
            r = client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": (
                        "你是编程智能体评测裁判。给定准则、证据与已作出的判定，"
                        "用不超过 80 字说明判定依据（引用关键证据），直接输出依据文本。"
                    )},
                    {"role": "user", "content": (
                        f"[准则 {name}]\n{definition}\n\n"
                        f"[任务]\n{task[:1200]}\n\n"
                        f"[证据]\n{evidence[:6000]}\n\n"
                        f"[判定] {'通过' if verdict == 1 else '不通过'}\n请给出判定依据。"
                    )},
                ],
                max_tokens=3000,
                temperature=0,
            )
            msg = r.choices[0].message
            text = (msg.content or "").strip()
            if not text:
                # 思考模型回退：推理字段尾部通常就是结论（预算仍不足时的兜底）
                text = (getattr(msg, "reasoning_content", "") or "").strip()
                if text:
                    text = text[-300:]
            return text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc) or attempt == len(_RETRY_DELAYS) - 1:
                break
            time.sleep(delay)
    # 理由采集失败不阻断评分：返回空串（与原版兼容），异常已由调用方记录
    print(f"  [{name}] 理由补采失败: {str(last_exc)[:120]}", flush=True)
    return ""


async def main() -> None:
    # 裁判密钥优先用 JUDGE_API_KEY（裁判端点可与被测网关不同），回退 GLAUCOUS_API_KEY
    api_key = os.environ.get("JUDGE_API_KEY") or os.environ.get("GLAUCOUS_API_KEY")
    if not api_key:
        print("FAIL: JUDGE_API_KEY / GLAUCOUS_API_KEY 未设置（先 source ~/.profile）")
        return
    client = OpenAI(api_key=api_key, base_url=JUDGE_BASE_URL)
    print(f"裁判：{JUDGE_MODEL} @ {JUDGE_BASE_URL}", flush=True)
    # 裁判请求：放宽输出上限（防截断）；思考模式保持开启（判断需推理）
    judge = llm_factory(
        JUDGE_MODEL,
        client=client,
        max_tokens=8192,
    )

    # 开跑前预检裁判可用性：持续过载则不产出（避免全 -1 数据覆盖历史评分）
    if not _wait_judge_ready(client):
        return

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
                if not reason:
                    # AspectCritic 只出 0/1：同一裁判模型补采判定依据（可审计性）
                    reason = await asyncio.to_thread(
                        explain_verdict, client, name, definition, task, evidence, value
                    )
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
