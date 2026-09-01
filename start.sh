#!/usr/bin/env bash
# Glaucous 一键启动脚本（WSL 内使用）
# 用法：bash start.sh [workspace] [其余参数透传给 glaucous CLI]
#   bash start.sh                    # 在当前仓库目录启动（默认）
#   bash start.sh /tmp/agent-demo    # 指定工作区（录制演示用）
#   bash start.sh --resume           # 首参以 - 开头视为 CLI 参数，工作区取默认值 .
set -e
cd "$(dirname "$0")"

# 载入 API 密钥（~/.profile 中的 export）
set -a; source ~/.profile 2>/dev/null; set +a

if [ -z "$GLAUCOUS_API_KEY" ]; then
  echo "错误：GLAUCOUS_API_KEY 未设置（应已在 ~/.profile 中 export）" >&2
  exit 1
fi

# 首参非选项则作为工作区，否则默认当前目录；其余参数透传
case "${1:-}" in
  -*) WORKSPACE="." ;;
  "") WORKSPACE="." ; set -- ;;
  *)  WORKSPACE="$1"; shift ;;
esac

exec ~/miniconda3/envs/glaucous/bin/glaucous --workspace "$WORKSPACE" "$@"
