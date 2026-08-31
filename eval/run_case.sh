#!/usr/bin/env bash
# 评测运行器：复制模板 → git init → 下达任务 → 判定
# 用法：
#   bash eval/run_case.sh e1-small-task                       # v1.1（PATH 中的 glaucous）
#   bash eval/run_case.sh e1-small-task /tmp/venv-v10/bin/glaucous  # v1.0 对照
# 运行实例保留在 /tmp/eval-<case>-<时间戳>，供会话/审计复查（可复现性证据）。
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
CASE="${1:?用法: run_case.sh <case> [glaucous 命令]}"
AGENT="${2:-glaucous}"
BASE="$ROOT/cases/$CASE"
if [ ! -d "$BASE" ]; then
  echo "FAIL: 用例不存在: $CASE"
  exit 1
fi

INSTANCE="/tmp/eval-$CASE-$(date +%Y%m%d-%H%M%S)"
cp -r "$BASE/workspace" "$INSTANCE"
cd "$INSTANCE"
git init -q
git add -A
git -c user.name=eval -c user.email=eval@eval commit -qm init
echo "=== 评测实例: $INSTANCE ==="

# 任务经 stdin 下达（非 TTY 降级路径：管道模式）
cat "$BASE/task.md" | "$AGENT" --workspace "$INSTANCE"

echo "=== 判定 ==="
bash "$BASE/check.sh" "$INSTANCE"
