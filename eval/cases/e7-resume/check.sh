#!/usr/bin/env bash
# e7 判定：续接后任务完成（与 e2 同口径的终态判定：四文件齐备 + 测试全绿 + add 已修复）
cd "$1" || { echo "FAIL: 实例目录不存在"; exit 1; }
[ -f README.md ] || { echo "FAIL: README.md 缺失"; exit 1; }
[ -f pyproject.toml ] || { echo "FAIL: pyproject.toml 缺失"; exit 1; }
grep -qi "pytest" pyproject.toml || { echo "FAIL: pyproject.toml 未声明 pytest"; exit 1; }
ls tests/*.py >/dev/null 2>&1 || { echo "FAIL: 无测试文件"; exit 1; }
if ! python3 -m pytest tests/ -q >/dev/null 2>&1; then
  echo "FAIL: pytest 未全绿（或无法收集）"
  exit 1
fi
if ! python3 -c "
import sys; sys.path.insert(0, '.')
from src.calc import add
assert add(2, 3) == 5
assert add(-2, -3) == -5
" 2>/dev/null; then
  echo "FAIL: add 未修复"
  exit 1
fi
echo "PASS"
