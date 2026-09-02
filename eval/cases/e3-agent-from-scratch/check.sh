#!/usr/bin/env bash
# e3 判定：产物四文件齐备 + pytest 全绿 + run() 行为冒烟（客观判据，无主观分）
cd "$1" || { echo "FAIL: 实例目录不存在"; exit 1; }
[ -f agent.py ] || { echo "FAIL: agent.py 缺失"; exit 1; }
[ -f tools.py ] || { echo "FAIL: tools.py 缺失"; exit 1; }
[ -f pyproject.toml ] || { echo "FAIL: pyproject.toml 缺失"; exit 1; }
grep -qi "pytest" pyproject.toml || { echo "FAIL: pyproject.toml 未声明 pytest"; exit 1; }
ls tests/*.py >/dev/null 2>&1 || { echo "FAIL: 无测试文件"; exit 1; }
if ! python3 -m pytest tests/ -q >/dev/null 2>&1; then
  echo "FAIL: pytest 未全绿（或无法收集）"
  exit 1
fi
# 产物实际能运行：run() 路由与工具往返（临时目录，不污染工作区）
if ! python3 -c "
import tempfile, os, sys
sys.path.insert(0, '.')
from agent import run
from tools import read_file, write_file
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, 't.txt')
    write_file(p, 'hello')
    assert read_file(p) == 'hello'
assert isinstance(run('读取 demo'), str) and run('读取 demo')
assert '不支持' in run('完全无关的任务 xyz')
"; then
  echo "FAIL: run()/工具行为不符合契约"
  exit 1
fi
echo "PASS"
