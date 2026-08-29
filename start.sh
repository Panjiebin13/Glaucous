#!/usr/bin/env bash
# Glaucous 一键启动脚本（WSL 内使用）
# 用法：bash start.sh [--resume]  （其余参数透传给 glaucous CLI）
set -e
cd "$(dirname "$0")"

# 载入 API 密钥（~/.profile 中的 export）
set -a; source ~/.profile 2>/dev/null; set +a

if [ -z "$GLAUCOUS_API_KEY" ]; then
  echo "错误：GLAUCOUS_API_KEY 未设置（应已在 ~/.profile 中 export）" >&2
  exit 1
fi

exec ~/miniconda3/envs/glaucous/bin/glaucous --workspace . "$@"
