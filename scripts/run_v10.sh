#!/usr/bin/env bash
# v1.0 对照组运行器（用后即删）：/build 切模式 + 任务 + 审批应答（"1"=同意）
# 用法: bash scripts/run_v10.sh <case-name>
set -e
cd /mnt/e/panjiebing/Documents/Glaucous
CASE="${1:?用法: run_v10.sh <case>}"
BASE="eval/cases/$CASE"
INSTANCE="/tmp/eval-$CASE-v10-$(date +%Y%m%d-%H%M%S)"
cp -r "$BASE/workspace" "$INSTANCE"
cd "$INSTANCE"
git init -q
git add -A
git -c user.name=eval -c user.email=eval@eval commit -qm init
echo "=== v1.0 对照实例: $INSTANCE ==="
# v1.0 工作流：任务先进 Plan 轮（探索 + 出方案）→ 方案卡选 2（开始构建，同意所有权限）
# → 同轮继续执行。后续 "2" 为其他审批卡兜底应答（同意）
{
  cat "/mnt/e/panjiebing/Documents/Glaucous/$BASE/task.md"
  for _ in 1 2 3 4 5 6; do echo "2"; done
} | /tmp/venv-v10/bin/glaucous --workspace "$INSTANCE"
echo "=== 判定（与 v1.1 同一判据）==="
bash "/mnt/e/panjiebing/Documents/Glaucous/$BASE/check.sh" "$INSTANCE" || echo "RESULT: FAIL"
