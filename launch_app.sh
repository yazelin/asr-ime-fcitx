#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/asr-ime-fcitx"
LOG_FILE="$CACHE_DIR/gui.log"
mkdir -p "$CACHE_DIR"

notify_error() {
  local msg="$1"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send -a "ASR IME" -u critical "ASR IME 控制面板" "$msg" || true
  fi
}

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  notify_error "找不到 .venv，請重新執行一鍵安裝。"
  exit 1
fi

if [[ -z "${DISPLAY:-}" ]]; then
  notify_error "目前沒有圖形 DISPLAY，請在桌面會話中開啟。"
  exit 1
fi

if ! "$ROOT_DIR/.venv/bin/python" - <<'PY' >/dev/null 2>&1
import tkinter  # noqa: F401
PY
then
  notify_error "缺少 tkinter，請執行：sudo apt install -y python3-tk"
  exit 1
fi

if ! "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/asr_ime_app.py" >>"$LOG_FILE" 2>&1; then
  notify_error "控制面板啟動失敗，請檢查：$LOG_FILE"
  exit 1
fi
