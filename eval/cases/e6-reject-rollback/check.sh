#!/usr/bin/env bash
# e6 判定：拒绝联动回退后，工作树与初始提交完全一致 + 审计留痕含拒绝记录
# （由人工在首个写审批选择「拒绝并回退」之后运行）
cd "$1" || { echo "FAIL: 实例目录不存在"; exit 1; }
INIT=$(git rev-list --max-parents=0 HEAD)
if [ -n "$(git diff "$INIT" --name-only)" ]; then
  echo "FAIL: 文件未回到任务前（与初始提交仍有差异）"
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "FAIL: 残留新增文件未移除"
  exit 1
fi
AUDIT="$1/.glaucous/audit.log"
[ -f "$AUDIT" ] || { echo "FAIL: 审计日志缺失"; exit 1; }
if ! grep -q '"decision": "reject"' "$AUDIT" && ! grep -q '"allowed": false' "$AUDIT"; then
  echo "FAIL: 审计日志无拒绝记录"
  exit 1
fi
echo "PASS"
