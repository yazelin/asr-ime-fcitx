#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-${ASR_IME_REPO_URL:-}}"
INSTALL_DIR="${2:-$HOME/.local/src/asr-ime-fcitx}"

if [[ -z "$REPO_URL" ]]; then
  echo "用法："
  echo "  bash bootstrap_install.sh <repo_url> [install_dir]"
  echo "或："
  echo "  ASR_IME_REPO_URL=<repo_url> bash bootstrap_install.sh"
  exit 1
fi

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
chmod +x setup.sh
./setup.sh --with-apt

echo "安裝完成：$INSTALL_DIR"
echo "你可以從應用程式選單開啟：ASR IME 控制面板"
