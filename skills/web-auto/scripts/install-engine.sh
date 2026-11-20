#!/usr/bin/env bash
# 安装 WebSkill 引擎（Python 包 + `web-auto-skill` 命令）
#
# 主安装入口是 `npx -y github:jinshan3690/dsh-web-auto-skill setup`（跨平台，也是文档里唯一写的）。
# 本脚本是它的离线等价物：GitHub 或 pypi 不可达、或想装本地改动时用。
#   WEBSKILL_REPO=<本地仓库目录> bash install-engine.sh
# 两者共用同一个 venv（~/.webskill/.venv），重复执行不会各装一份。
#
# 可用环境变量：
#   WEBSKILL_HOME      安装目录（默认 ~/.webskill）
#   WEBSKILL_REPO      仓库地址或本地目录（默认 GitHub）
#   WEBSKILL_PYTHON    指定 python 解释器（默认 python3，需 >= 3.10）
#   WEBSKILL_SKIP_BROWSER=1   跳过 Chromium 下载（已有浏览器时）
#   WEBSKILL_SKIP_LINK=1      不改 PATH 目录，只在最后打印命令路径
set -euo pipefail

HOME_DIR="${WEBSKILL_HOME:-$HOME/.webskill}"
REPO="${WEBSKILL_REPO:-https://github.com/jinshan3690/dsh-web-auto-skill}"
PYTHON="${WEBSKILL_PYTHON:-python3}"
CMDS=(web-auto-skill)

# venv 位置：显式指定 > 已有的 .venv > 早期版本的 venv（沿用，不重复占 300MB）> 新建 .venv
if [ -n "${WEBSKILL_VENV:-}" ]; then
  VENV="$WEBSKILL_VENV"
elif [ -x "$HOME_DIR/.venv/bin/python" ]; then
  VENV="$HOME_DIR/.venv"
elif [ -x "$HOME_DIR/venv/bin/python" ]; then
  VENV="$HOME_DIR/venv"
else
  VENV="$HOME_DIR/.venv"
fi

log() { printf '[web-auto-skill] %s\n' "$*"; }
fail() { printf '[web-auto-skill] 错误: %s\n' "$*" >&2; exit 1; }

# 把 venv 里的可执行软链到 PATH 可见目录。
# 优先挑「存在 + 可写 + 已在 PATH」的目录，装完立刻能敲 `web-auto-skill`，不用改 shell 配置。
link_commands() {
  local src_dir="$1" dir="" c
  if [ "${WEBSKILL_SKIP_LINK:-0}" = "1" ]; then
    log "跳过 PATH 软链（WEBSKILL_SKIP_LINK=1），命令在 $src_dir"
    return 0
  fi

  for c in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    [ -d "$c" ] && [ -w "$c" ] || continue
    case ":$PATH:" in *":$c:"*) ;; *) continue ;; esac
    dir="$c"
    break
  done

  if [ -z "$dir" ]; then
    dir="$HOME/.local/bin"
    mkdir -p "$dir"
    log "提示：$dir 不在 PATH 里，把下面这行加进 ~/.zshrc（或 ~/.bashrc）后重开终端："
    log "  export PATH=\"$dir:\$PATH\""
  fi

  for c in "${CMDS[@]}"; do
    [ -x "$src_dir/$c" ] || continue
    # 已经指向同一个文件就不用再建，避免 BSD ln 报 "are identical"
    if [ "$dir/$c" -ef "$src_dir/$c" ] 2>/dev/null; then continue; fi
    ln -sf "$src_dir/$c" "$dir/$c" && log "命令: $dir/$c"
  done
}

# venv 里有可执行才补软链；没有说明引擎装在别处（pipx 等），不去动它
ensure_links() {
  if [ -x "$VENV/bin/web-auto-skill" ]; then
    link_commands "$VENV/bin"
  fi
}

# SKILL.md 更新了但引擎还是旧版时，用 --upgrade 强制重装
UPGRADE=0
for arg in "$@"; do
  case "$arg" in
    --upgrade) UPGRADE=1 ;;
  esac
done

if [ "$UPGRADE" = "0" ]; then
  EXISTING=""
  for c in "${CMDS[@]}"; do
    if command -v "$c" >/dev/null 2>&1; then
      EXISTING="$(command -v "$c")"
      break
    fi
  done
  [ -z "$EXISTING" ] && [ -x "$VENV/bin/web-auto-skill" ] && EXISTING="$VENV/bin/web-auto-skill"

  if [ -n "$EXISTING" ]; then
    log "已安装: $EXISTING"
    ensure_links
    log "升级到最新版: bash $0 --upgrade"
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
log "虚拟环境: $VENV"
mkdir -p "$HOME_DIR"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"

"$VENV/bin/python" -m pip install -q -U pip --timeout 30 >/dev/null 2>&1 \
  || log "pip 升级失败（网络），继续用当前版本"

pkg_install() {
  local args=(install -q --timeout "${WEBSKILL_PIP_TIMEOUT:-60}" --retries 3)
  [ -n "${WEBSKILL_PIP_INDEX:-}" ] && args+=(--index-url "$WEBSKILL_PIP_INDEX")
  "$VENV/bin/python" -m pip "${args[@]}" "$@" && return 0

  # pypi 在国内经常只是慢，换镜像重试一次即可，不用用户手工传参
  if [ -z "${WEBSKILL_PIP_INDEX:-}" ]; then
    log "默认源失败，改用阿里云镜像重试..."
    "$VENV/bin/python" -m pip "${args[@]}" \
      --index-url https://mirrors.aliyun.com/pypi/simple/ "$@" && return 0
  fi
  return 1
}

if [ -f "$REPO/pyproject.toml" ]; then
  # 本地目录：可编辑安装，改代码即生效（开发/离线场景）
  log "从本地目录安装: $REPO"
  pkg_install -e "$REPO[onnx]" || fail "pip 安装失败，检查 $REPO 是否是有效的本仓库"
else
  log "安装 webskill（含 ONNX 语义召回，约 22MB 依赖）..."
  pkg_install "webskill[onnx] @ git+$REPO" \
    || fail "pip 安装失败。检查网络/代理后重试，或指定 WEBSKILL_REPO=<本地目录或镜像地址>"
fi

if [ "${WEBSKILL_SKIP_BROWSER:-0}" != "1" ]; then
  log "下载 Chromium（约 150MB，只做一次）..."
  "$VENV/bin/python" -m playwright install chromium
fi

"$VENV/bin/python" -m webskill init >/dev/null
ensure_links

log "完成。引擎目录: $VENV"
if command -v web-auto-skill >/dev/null 2>&1; then
  log "现在可以直接用: web-auto-skill skill-list"
else
  log "命令未进 PATH，直接调用: $VENV/bin/web-auto-skill skill-list"
fi
"$VENV/bin/python" -m webskill health || true
