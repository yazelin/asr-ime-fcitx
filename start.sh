#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/asr-ime-fcitx"
PID_FILE="$CACHE_DIR/daemon.pid"
LOG_FILE="$CACHE_DIR/daemon.log"
STATE_FILE="/tmp/fcitx-asr-ime-state.json"
SYSTEM_IM_CONF="/usr/share/fcitx5/inputmethod/asrime.conf"
SYSTEM_ADDON_CONF="/usr/share/fcitx5/addon/asrimefcitxnative.conf"
mkdir -p "$CACHE_DIR"

print_runtime_state() {
  [[ -f "$STATE_FILE" ]] || return 0
  python3 - "$STATE_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
except Exception:
    raise SystemExit(0)

listening = state.get("listening")
if listening is True:
    print("listening: ON")
elif listening is False:
    print("listening: OFF")

last_text = state.get("last_text")
if last_text:
    print(f"last_text: {last_text}")

last_error = state.get("last_error")
if last_error:
    print(f"last_error: {last_error}")

mode = state.get("mode")
if mode:
    print(f"mode: {mode}")

backend = state.get("backend")
if backend:
    print(f"backend: {backend}")

postprocess_mode = state.get("postprocess_mode")
if postprocess_mode:
    print(f"postprocess: {postprocess_mode}")

postprocess_provider = state.get("postprocess_provider")
if postprocess_provider:
    print(f"provider: {postprocess_provider}")
PY
}

force_setup=0
setup_only=0
show_help=0
daemon_stop=0
daemon_status=0
send_toggle=0
open_settings=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup)
      force_setup=1
      shift
      ;;
    --setup-only)
      setup_only=1
      shift
      ;;
    --stop)
      daemon_stop=1
      shift
      ;;
    --status)
      daemon_status=1
      shift
      ;;
    --toggle)
      send_toggle=1
      shift
      ;;
    --settings)
      open_settings=1
      shift
      ;;
    -h|--help)
      show_help=1
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ $show_help -eq 1 ]]; then
  cat <<'EOF'
Usage:
  ./start.sh                      # 啟動 daemon + 提示切換到 ASR 輸入法
  ./start.sh --setup              # 強制重新 setup（含編譯 addon）
  ./start.sh --setup-only         # 只 setup
  ./start.sh --stop               # 停止 ASR daemon
  ./start.sh --status             # 查看 daemon 狀態
  ./start.sh --toggle             # 手動切換錄音開關（不依賴熱鍵）
  ./start.sh --settings           # 開啟設定面板（切換 Google/本機、修改熱鍵）
  ./start.sh -- [daemon args...]  # 傳遞參數給 daemon_asr.py
EOF
  exit 0
fi

if [[ $open_settings -eq 1 ]]; then
  if [[ ! -x ".venv/bin/python" ]]; then
    echo "尚未建立虛擬環境，請先執行：./setup.sh --with-apt"
    exit 1
  fi
  exec "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/settings_panel.py"
fi

if [[ $daemon_stop -eq 1 ]]; then
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "ASR daemon stopped (pid=$pid)"
    fi
    rm -f "$PID_FILE"
  else
    echo "ASR daemon is not running."
  fi
  exit 0
fi

if [[ $daemon_status -eq 1 ]]; then
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "ASR daemon running (pid=$pid)"
      echo "log: $LOG_FILE"
      print_runtime_state
      exit 0
    fi
  fi
  echo "ASR daemon not running."
  exit 1
fi

if [[ $send_toggle -eq 1 ]]; then
  if python3 - <<'PY'
import os
import sys
path = "/tmp/fcitx-asr-ime-cmd.fifo"
try:
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
except OSError:
    sys.exit(1)
try:
    os.write(fd, b"toggle\n")
finally:
    os.close(fd)
PY
  then
    echo "已送出 toggle 指令"
    sleep 0.2
    print_runtime_state
    exit 0
  fi
  echo "無法送出 toggle，請先執行 ./start.sh 啟動 daemon"
  exit 1
fi

needs_setup=0
if [[ ! -x ".venv/bin/python" ]]; then
  needs_setup=1
elif [[ ! -f "$SYSTEM_IM_CONF" || ! -f "$SYSTEM_ADDON_CONF" ]]; then
  needs_setup=1
fi

if [[ $force_setup -eq 1 || $setup_only -eq 1 ]]; then
  ./setup.sh --with-apt
fi

if [[ $setup_only -eq 1 ]]; then
  echo "Setup complete."
  exit 0
fi

if [[ $needs_setup -eq 1 ]]; then
  echo "尚未完成系統安裝，請先執行：./setup.sh --with-apt"
  exit 1
fi

# 清理舊版 local 安裝，避免重複/不可用項目干擾
rm -f \
  "$HOME/.local/share/fcitx5/inputmethod/asrime.conf" \
  "$HOME/.local/share/fcitx5/inputmethod/asr-ime-fcitx-online.conf" \
  "$HOME/.local/share/fcitx5/addon/asrimefcitxnative.conf"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" || true)"
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "ASR daemon already running (pid=$pid)"
  else
    rm -f "$PID_FILE"
  fi
fi

if [[ ! -f "$PID_FILE" ]]; then
  nohup "$ROOT_DIR/.venv/bin/python" -u "$ROOT_DIR/daemon_asr.py" "$@" >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "ASR daemon started (pid=$(cat "$PID_FILE"))"
fi

if ! fcitx5-remote >/dev/null 2>&1; then
  nohup fcitx5 -d >/dev/null 2>&1 &
  sleep 1
fi

fcitx5-remote -r >/dev/null 2>&1 || true
fcitx5-remote -s asrime >/dev/null 2>&1 || true
current_im="$(fcitx5-remote -n 2>/dev/null || true)"

echo "請切換到輸入法：ASR Voice Native (Fcitx5)"
if [[ "$current_im" != "asrime" ]]; then
  echo "⚠️  目前輸入法是：${current_im:-unknown}（不是 ASR）"
  echo "請在 fcitx5-configtool 把 ASR Voice Native (Fcitx5) 加入目前群組"
fi
echo "熱鍵：Ctrl+Alt+V / Ctrl+Alt+R / F8 / Shift+F8"
echo "也可用：./start.sh --toggle"
echo "查看日誌：tail -f $LOG_FILE"
print_runtime_state
