#!/usr/bin/env bash
# e1 判定：测试全绿 + add 行为正确（客观判据，无主观分）
cd "$1" || { echo "FAIL: 实例目录不存在"; exit 1; }
if ! ls tests/*.py >/dev/null 2>&1; then
  echo "FAIL: 未新增测试文件"
  exit 1
fi
if ! python3 -m pytest tests/ -q >/dev/null 2>&1; then
  echo "FAIL: pytest 未全绿（或无法收集）"
  exit 1
fi
if ! python3 -c "
import sys; sys.path.insert(0, '.')
from src.calc import add
assert add(2, 3) == 5, f'add(2,3)={add(2,3)}'
assert add(-1, 1) == 0
assert add(0.5, 0.5) == 1.0
" 2>/dev/null; then
  echo "FAIL: add 结果不正确"
  exit 1
fi
echo "PASS"
