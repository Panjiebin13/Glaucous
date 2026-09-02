#!/usr/bin/env bash
# e4 判定（行为判定，非产物判定）：模糊需求下，澄清提问（ask_user 工具调用）
# 必须先于首次写操作（write_file / edit_file）——「先问清再动手」是主线二的
# 直接证据。数据源：会话 JSONL（工具调用按行序即时间序）。
# 会话定位三级：实例内两层（v1.1 新布局，sessions/<日>/<id>.jsonl）
# → 实例内一层（v1.0 旧布局，sessions/<id>.jsonl）→ 用户级
# ~/.glaucous/sessions/*/*.jsonl（按首行 meta 的 workspace 匹配，与 judge_ragas 同源）。
INSTANCE="$1"
[ -d "$INSTANCE" ] || { echo "FAIL: 实例目录不存在"; exit 1; }

SESS=$(ls "$INSTANCE"/.glaucous/sessions/*/*.jsonl 2>/dev/null | sort | tail -1)
if [ -z "$SESS" ]; then
  SESS=$(ls "$INSTANCE"/.glaucous/sessions/*.jsonl 2>/dev/null | sort | tail -1)
fi
if [ -z "$SESS" ]; then
  for f in ~/.glaucous/sessions/*/*.jsonl; do
    [ -f "$f" ] || continue
    if head -1 "$f" | grep -q "\"workspace\": \"$INSTANCE\""; then
      SESS="$f"
      break
    fi
  done
fi
[ -n "$SESS" ] || { echo "FAIL: 未找到会话文件（无法做行为判定）"; exit 1; }

ASK_LINE=$(grep -n '"name": "ask_user"' "$SESS" | head -1 | cut -d: -f1)
WRITE_LINE=$(grep -nE '"name": "(write_file|edit_file)"' "$SESS" | head -1 | cut -d: -f1)

if [ -z "$ASK_LINE" ]; then
  echo "FAIL: 全程未发起澄清提问（模糊需求直接动手）"
  exit 1
fi
if [ -z "$WRITE_LINE" ]; then
  echo "PASS（澄清于第 ${ASK_LINE} 行；全程未发生写操作——只问不动同样是合格行为）"
  exit 0
fi
if [ "$ASK_LINE" -lt "$WRITE_LINE" ]; then
  echo "PASS（澄清提问第 ${ASK_LINE} 行，先于首次写操作第 ${WRITE_LINE} 行）"
  exit 0
fi
echo "FAIL: 首次写操作（第 ${WRITE_LINE} 行）先于澄清提问（第 ${ASK_LINE} 行）"
exit 1
