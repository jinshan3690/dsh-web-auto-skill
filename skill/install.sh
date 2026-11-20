#!/usr/bin/env bash
# 把 web-auto skill 安装到某个 Agent 的 skills 目录
# 用法：./install.sh [目标skills目录]
#   默认 macOS 豆包 agent：.../agent_mode/workspace/.skills
#   Claude Code：~/.claude/skills
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-$HOME/Library/Application Support/Doubao/Default/.doubao/agent_mode/workspace/.skills}"

if [ ! -f "$HERE/web-auto/SKILL.md" ]; then
  echo "找不到 $HERE/web-auto/SKILL.md" >&2
  exit 1
fi

mkdir -p "$DEST"
rm -rf "$DEST/web-auto"
cp -R "$HERE/web-auto" "$DEST/web-auto"
echo "installed -> $DEST/web-auto"
echo "在 Agent 里用 /web-auto 触发，或直接说「帮我在这个网页上填表」。"
