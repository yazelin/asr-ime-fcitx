#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="python3"
if [[ -x "/usr/bin/python3" ]]; then
  PYTHON_BIN="/usr/bin/python3"
fi

if [[ ! -w "$ROOT_DIR" ]]; then
  echo "修復專案目錄權限..."
  sudo chown -R "$(id -un)":"$(id -gn)" "$ROOT_DIR"
fi

if [[ "${1:-}" == "--with-apt" ]]; then
  sudo apt-get update
  sudo apt-get install -y \
    build-essential cmake gettext pkg-config \
    fcitx5 fcitx5-frontend-all \
    libfcitx5core-dev libfcitx5utils-dev libfcitx5config-dev fcitx5-modules-dev \
    python3-venv python3-tk libportaudio2 flac xclip
else
  echo "Tip: 可先執行 ./setup.sh --with-apt 安裝編譯與執行相依"
fi

if [[ -e ".venv" ]]; then
  if ! rm -rf .venv 2>/dev/null; then
    sudo rm -rf .venv
    sudo chown -R "$(id -un)":"$(id -gn)" "$ROOT_DIR"
  fi
fi

"$PYTHON_BIN" -m venv .venv
"$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install -r requirements.txt

cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr
cmake --build build -j"$(nproc)"
sudo cmake --install build

# 清理舊版 local 安裝，避免在 fcitx 設定裡出現重複/不可用項目
rm -f \
  "$HOME/.local/share/fcitx5/inputmethod/asrime.conf" \
  "$HOME/.local/share/fcitx5/inputmethod/asr-ime-fcitx-online.conf" \
  "$HOME/.local/share/fcitx5/addon/asrimefcitxnative.conf"

chmod +x start.sh run_engine.sh daemon_asr.py settings_panel.py asr_ime_app.py launch_app.sh bootstrap_install.sh

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/asr-ime-fcitx.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=ASR IME 控制面板
Comment=開啟 ASR IME 設定與控制
Exec=$ROOT_DIR/launch_app.sh
Icon=audio-input-microphone
Terminal=false
Categories=Utility;AudioVideo;
EOF

echo "Setup complete."
echo "請執行：fcitx5 -r"
echo "然後在 fcitx5-configtool 新增輸入法：ASR Voice Native (Fcitx5)"
echo "應用程式啟動器：ASR IME 控制面板"
