#!/usr/bin/env bash
# e5 判定：回退完成后，工作树与初始提交完全一致 + 测试恢复全绿
# （由人工在 /rollback 或手工恢复之后运行）
cd "$1" || { echo "FAIL: 实例目录不存在"; exit 1; }
INIT=$(git rev-list --max-parents=0 HEAD)
if [ -n "$(git diff "$INIT" --name-only)" ]; then
  echo "FAIL: 文件未还原（与初始提交仍有差异）"
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "FAIL: 残留新增文件未移除"
  exit 1
fi
if ! python3 -m pytest tests/ -q >/dev/null 2>&1; then
  echo "FAIL: 测试未恢复全绿"
  exit 1
fi
echo "PASS"
