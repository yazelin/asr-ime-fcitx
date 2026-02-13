#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO_URL="https://github.com/yazelin/asr-ime-fcitx.git"
REPO_URL="${1:-${ASR_IME_REPO_URL:-$DEFAULT_REPO_URL}}"
INSTALL_DIR="${2:-$HOME/.local/src/asr-ime-fcitx}"

if ! command -v git >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --depth 1 origin
  git -C "$INSTALL_DIR" reset --hard origin/HEAD
else
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x setup.sh start.sh
./setup.sh --with-apt
./start.sh --stop >/dev/null 2>&1 || true
if ./start.sh; then
  echo "已自動啟動 ASR IME。"
else
  echo "安裝完成，但目前無法自動啟動（請在桌面環境執行 ./start.sh）"
fi

echo "安裝完成：$INSTALL_DIR"
echo "你可以從應用程式選單開啟：ASR IME 控制面板"
