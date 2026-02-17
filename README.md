# ASR IME Fcitx5 Native

位置：`~/SDD2/asr-ime-fcitx`  
這版是 **Fcitx5 原生 C++ addon**，不是 IBus frontend。  
目標：在 Fcitx 輸入法清單看到 **ASR Voice Native (Fcitx5)**，切到它後按 `Ctrl+Alt+V` 進行語音輸入。

## 1) 安裝與編譯

```bash
cd ~/SDD2/asr-ime-fcitx
./setup.sh --with-apt
```

### 另一台電腦快速安裝（先不用手動 clone）

```bash
curl -fsSL https://raw.githubusercontent.com/yazelin/asr-ime-fcitx/main/bootstrap_install.sh | bash
```

> 這是「一鍵安裝」流程：你不用先手動 clone，也不需要一步步輸入安裝指令。
> 注意：請**不要**在前面加 `sudo`。

安裝會：
- 建立 Python venv 並安裝 ASR 相依
- 編譯並 **系統安裝** Fcitx5 addon（`/usr`，會需要 sudo）
- 清理舊版 `~/.local` 的 ASR 設定，避免出現重複/不可用項目
- 建立應用程式啟動器：**ASR IME 控制面板**

## 2) 讓輸入法出現在 Fcitx

```bash
fcitx5 -r
fcitx5-configtool
```

在設定工具新增：**ASR Voice Native (Fcitx5)**。  
若沒看到，先確認下列檔案存在：

```bash
ls /usr/share/fcitx5/inputmethod/asrime.conf
ls /usr/share/fcitx5/addon/asrimefcitxnative.conf
ls /usr/lib/*/fcitx5/libasrimefcitxnative.so
```

## 3) 啟動 ASR daemon

```bash
./start.sh
```

也可從應用程式選單開啟 **ASR IME 控制面板**（GUI 小工具，含啟動/停止/設定）。

> `fcitx5 -r` 在某些環境會以前景卡住終端；建議直接執行 `./start.sh`（腳本會自動確保 fcitx 在背景執行）。

常用：

```bash
./start.sh --status
./start.sh --stop
./start.sh --toggle
./start.sh --settings
./start.sh -- --list-devices
./start.sh -- --device 2 --language zh-TW
./start.sh -- --max-phrase-sec 0
./start.sh -- --no-process-on-stop
./start.sh -- --verbose
```

設定面板（`./start.sh --settings`）可調整：
- 辨識後端：`google` / `local`
- 熱鍵（每行一個）
- 是否 `process-on-stop`
- 標點/斷句後處理：`none` / `heuristic` / `command`
- 標點模型供應商（快速套用）：`copilot` / `gemini` / `claude-code`
- command 模式的 `program + args + timeout`
- args 可用 `{text}` 代表辨識原文（例如 Copilot GPT-5 mini 預設）
- 強制繁體輸出（避免簡體）

改完熱鍵後請執行 `fcitx5-remote -r`；改完辨識後端後請 `./start.sh --stop && ./start.sh`。
若設定面板勾選「儲存後自動套用」，會自動執行上述流程。

## 4) 使用

1. 切換到 **ASR Voice Native (Fcitx5)**  
2. 在任何文字框按 `Ctrl+Alt+V` / `Ctrl+Alt+R` / `F8` / `Shift+F8`（開始/停止聽寫）  
3. 說話，停頓後自動 commit 到目前游標

若熱鍵衝突，可先用 `./start.sh --toggle` 驗證錄音流程是否正常。
每次切換錄音或辨識到文字時，桌面會跳 `notify-send` 通知（不需要一直 `tail`）。

若按熱鍵沒反應，先檢查：

```bash
./start.sh --status
fcitx5-remote -n
tail -f ~/.cache/asr-ime-fcitx/daemon.log
```

`fcitx5-remote -n` 必須是 `asrime`，按熱鍵時日誌才會出現 `listening ON/OFF`。
`./start.sh --status` 也會顯示 `listening: ON/OFF`、`mode`、`backend`、`postprocess`、`provider`、最近一次辨識 `last_text`、以及 `last_error`（若有）。
若 F8 有顯示錄音中/停止但沒有文字，先確認 `current_im: asrime`，並到設定面板指定正確麥克風（可按「列出麥克風」）。

若看到 `org.freedesktop.portal.Error.NotFound`，通常是桌面 portal 設定訊息，**不是致命錯誤**，可先忽略。
若設定面板打不開，先執行：`sudo apt install -y python3-tk`，再重跑 `./setup.sh --with-apt`。
若點應用程式圖示沒反應，請看：`tail -n 80 ~/.cache/asr-ime-fcitx/gui.log`。

預設是 `mode: on-stop`（切回 OFF 才做一次辨識，適合背景音大時）。  
若想改回「停頓即送出」，用 `./start.sh -- --no-process-on-stop`。

## 備註

- `backend: google`：走 Google Web Speech（免費但非官方 SLA，需網路）。  
- `backend: local`：走 `faster-whisper` 本機辨識（第一次會下載模型）。
- `postprocess: heuristic` 會嘗試自動補常見中文標點；`smart` 會啟用 Smart Edit 層，嘗試移除填充詞、做小幅自動更正，並補上標點與斷句；`command` 可接你指定的大語言模型 CLI。
- 預設已改為 `copilot + gpt-5-mini` 的 command 後處理（會補標點、斷句、段落並維持繁體）。

Notes on Settings Panel additions:
- 新增選項 `postprocess: smart`：啟用 Smart Edit 層，會在常規標點/斷句之外嘗試移除無意義填充詞（例如「嗯、啊」）並做小幅自動更正。
- 新增兩個勾選：`enable_filler_filter`（預設 ON）與 `enable_self_correction`（預設 ON），可分別關閉填充詞過濾或自動更正功能。
- 新增選項 `enable_context_memory`（預設 OFF）：啟用後會在後處理時保留先前辨識結果作為上下文，提升長對話或多句連貫性的後處理品質。
- 新增選項 `context_length`（預設 5）：設定保留的最近辨識片段數量（句/段），數值越大會提供更長的上下文但可能增加處理量。
- 若在設定面板勾選「儲存後自動套用」，會在儲存後執行：`fcitx5-remote -r` 並重啟 daemon（`./start.sh --stop && ./start.sh`）以套用變更。

使用範例：在設定面板勾選「啟用上下文記憶」，並將「上下文長度」設為 10，儲存後（若選擇自動套用）系統會重啟 daemon；之後後處理會帶入最近 10 筆辨識結果以改善回覆的連貫性與上下文理解。
Smart Edit 範例：
- 原始："嗯 我今天 去 超市 買 了 蘋果 然後 回家"
  Smart Edit："我今天去超市買了蘋果，然後回家。"
- 原始："今天 天氣 不錯 啊 我 想 去 公園 散步"
  Smart Edit："今天天氣不錯，我想去公園散步。"

（範例示意 Smart Edit 如何移除填充詞並補上標點與小幅修正語句。）
