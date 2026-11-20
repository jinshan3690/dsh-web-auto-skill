#!/usr/bin/env bash
# 安装 WebSkill 引擎（Python 包 + `webskill` 命令 + Chromium）
#
# 为什么需要这一步：`npx skills add` 只把 SKILL.md 装进 Agent 的 skills 目录，
# 它不会安装 Python 引擎。SKILL.md 第 0 节发现引擎不存在时会调用本脚本。
#
# 可用环境变量：
#   WEBSKILL_HOME      安装目录（默认 ~/.webskill）
#   WEBSKILL_REPO      仓库地址（默认 GitHub）
#   WEBSKILL_PYTHON    指定 python 解释器（默认 python3，需 >= 3.10）
#   WEBSKILL_SKIP_BROWSER=1   跳过 Chromium 下载（已有浏览器时）
set -euo pipefail

HOME_DIR="${WEBSKILL_HOME:-$HOME/.webskill}"
REPO="${WEBSKILL_REPO:-https://github.com/jinshan3690/dsh-web-auto-skill}"
PYTHON="${WEBSKILL_PYTHON:-python3}"
VENV="$HOME_DIR/.venv"
BIN="$VENV/bin/webskill"

log() { printf '[webskill] %s\n' "$*"; }
fail() { printf '[webskill] 错误: %s\n' "$*" >&2; exit 1; }

# SKILL.md 更新了但引擎还是旧版时，用 --upgrade 强制重装
UPGRADE=0
for arg in "$@"; do
  case "$arg" in
    --upgrade) UPGRADE=1 ;;
  esac
done

if [ "$UPGRADE" = "0" ]; then
  if command -v webskill >/dev/null 2>&1; then
    log "已安装: $(command -v webskill)（升级到最新版：bash $0 --upgrade）"
    exit 0
  fi
  if [ -x "$BIN" ]; then
    log "已安装: $BIN（升级到最新版：bash $0 --upgrade）"
    exit 0
  fi
fi

command -v "$PYTHON" >/dev/null 2>&1 || fail "找不到 $PYTHON，请先装 Python 3.10+"

"$PYTHON" - <<'PY' || exit 1
import sys
if sys.version_info < (3, 10):
    sys.exit("需要 Python 3.10+，当前是 %s" % sys.version.split()[0])
PY

log "安装目录: $HOME_DIR"
mkdir -p "$HOME_DIR"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"

"$VENV/bin/python" -m pip install -q -U pip
log "安装 webskill（含 ONNX 语义召回，约 22MB 依赖）..."
"$VENV/bin/python" -m pip install -q -U "webskill[onnx] @ git+$REPO" \
  || fail "pip 安装失败。检查网络/代理后重试，或指定 WEBSKILL_REPO=<本地路径或镜像地址>"

if [ "${WEBSKILL_SKIP_BROWSER:-0}" != "1" ]; then
  log "下载 Chromium（约 150MB，只做一次）..."
  "$VENV/bin/python" -m playwright install chromium
fi

"$VENV/bin/python" -m webskill init >/dev/null
log "完成。可执行文件: $BIN"
log "把这一行加进 shell 配置即可全局使用: export PATH=\"$VENV/bin:\$PATH\""
"$BIN" health || true
