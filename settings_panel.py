#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception as e:
    raise SystemExit("缺少 tkinter，請先安裝：sudo apt install -y python3-tk") from e

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
CONFIG_DIR = Path(CONFIG_HOME) / "asr-ime-fcitx"
CONFIG_FILE = CONFIG_DIR / "config.json"
HOTKEY_FILE = CONFIG_DIR / "hotkeys.conf"

DEFAULT_CONFIG = {
    "backend": "google",
    "language": "zh-TW",
    "force_traditional": True,
    "process_on_stop": True,
    "local_model": "small",
    "local_device": "auto",
    "local_compute_type": "auto",
    "postprocess_mode": "command",
    "postprocess_provider": "copilot",
    "postprocess_program": "copilot",
    "postprocess_args": '-s --model gpt-5-mini -p "請將以下語音辨識結果改寫為繁體中文，補上自然標點、斷句與段落；不要新增內容，只回傳結果：{text}" --allow-all',
    "postprocess_timeout_sec": 12,
    "auto_apply_on_save": True,
}

DEFAULT_HOTKEYS = ["Control+Alt+v", "Control+Alt+r", "F8", "Shift+F8"]

PROVIDER_PRESETS = {
    "custom": ("", ""),
    "copilot": (
        "copilot",
        '-s --model gpt-5-mini -p "請將以下語音辨識結果改寫為繁體中文，補上自然標點、斷句與段落；不要新增內容，只回傳結果：{text}" --allow-all',
    ),
    "gemini": (
        "gemini",
        "--output-format text -p 請幫以下語句加入自然中文標點與斷句，只回傳處理後文字：{text}",
    ),
    "claude-code": (
        "claude",
        "-p --output-format text --permission-mode dontAsk 請幫以下語句加入自然中文標點與斷句，只回傳處理後文字：{text}",
    ),
}


def to_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return default


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if isinstance(raw, dict):
        for key in cfg:
            if key in raw:
                cfg[key] = raw[key]
    return cfg


def load_hotkeys():
    try:
        lines = [line.strip() for line in HOTKEY_FILE.read_text(encoding="utf-8").splitlines()]
        keys = [line for line in lines if line and not line.startswith("#")]
        if keys:
            return keys
    except Exception:
        pass
    return list(DEFAULT_HOTKEYS)


def save_config(cfg, hotkeys):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HOTKEY_FILE.write_text("\n".join(hotkeys) + "\n", encoding="utf-8")


def apply_runtime(script_dir):
    msgs = []
    ok = True

    try:
        subprocess.run(["fcitx5-remote", "-r"], cwd=script_dir, check=True, capture_output=True, text=True)
        msgs.append("✓ 已重新載入 Fcitx")
    except Exception as e:
        ok = False
        msgs.append(f"✗ 重新載入 Fcitx 失敗：{e}")

    try:
        subprocess.run([str(script_dir / "start.sh"), "--stop"], cwd=script_dir, check=False, capture_output=True, text=True)
        proc = subprocess.run([str(script_dir / "start.sh")], cwd=script_dir, check=True, capture_output=True, text=True)
        if proc.stdout.strip():
            msgs.append("✓ 已重啟 ASR daemon")
        else:
            msgs.append("✓ 已套用設定並重啟")
    except Exception as e:
        ok = False
        msgs.append(f"✗ 重啟 ASR daemon 失敗：{e}")

    return ok, "\n".join(msgs)


def main():
    cfg = load_config()
    hotkeys = load_hotkeys()
    script_dir = Path(__file__).resolve().parent

    root = tk.Tk()
    root.title("ASR IME 設定面板")
    scaling = float(root.tk.call("tk", "scaling"))
    ui_scale = max(1.0, scaling / 1.4)
    root.geometry(f"{int(840 * ui_scale)}x{int(760 * ui_scale)}")
    root.minsize(720, 560)
    root.resizable(True, True)

    outer = ttk.Frame(root)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    frame = ttk.Frame(canvas, padding=14)
    canvas_window = canvas.create_window((0, 0), window=frame, anchor="nw")

    def sync_scroll_region(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def sync_frame_width(event):
        canvas.itemconfigure(canvas_window, width=event.width)

    frame.bind("<Configure>", sync_scroll_region)
    canvas.bind("<Configure>", sync_frame_width)
    frame.columnconfigure(1, weight=1)

    backend_var = tk.StringVar(value=str(cfg.get("backend", "google")))
    language_var = tk.StringVar(value=str(cfg.get("language", "zh-TW")))
    force_traditional_var = tk.BooleanVar(value=to_bool(cfg.get("force_traditional", True), True))
    process_on_stop_var = tk.BooleanVar(value=to_bool(cfg.get("process_on_stop", True), True))
    local_model_var = tk.StringVar(value=str(cfg.get("local_model", "small")))
    local_device_var = tk.StringVar(value=str(cfg.get("local_device", "auto")))
    local_compute_var = tk.StringVar(value=str(cfg.get("local_compute_type", "auto")))

    post_mode_var = tk.StringVar(value=str(cfg.get("postprocess_mode", "command")))
    provider_var = tk.StringVar(value=str(cfg.get("postprocess_provider", "copilot")))
    post_program_var = tk.StringVar(value=str(cfg.get("postprocess_program", "")))
    post_args_var = tk.StringVar(value=str(cfg.get("postprocess_args", "")))
    post_timeout_var = tk.StringVar(value=str(cfg.get("postprocess_timeout_sec", 12)))
    auto_apply_var = tk.BooleanVar(value=to_bool(cfg.get("auto_apply_on_save", True), True))

    row = 0
    ttk.Label(frame, text="語音辨識後端").grid(row=row, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=backend_var, values=["google", "local"], state="readonly", width=24).grid(
        row=row, column=1, sticky="ew"
    )
    row += 1

    ttk.Label(frame, text="語言").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=language_var, width=28).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Checkbutton(
        frame,
        text="強制輸出繁體中文（避免簡體）",
        variable=force_traditional_var,
    ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
    row += 1

    ttk.Checkbutton(
        frame,
        text="只在切回 OFF 時辨識一次（建議背景吵雜時開啟）",
        variable=process_on_stop_var,
    ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
    row += 1

    ttk.Label(frame, text="本機模型").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Combobox(
        frame,
        textvariable=local_model_var,
        values=["tiny", "base", "small", "medium", "large-v3"],
        width=24,
    ).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Label(frame, text="本機裝置").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Combobox(
        frame,
        textvariable=local_device_var,
        values=["auto", "cpu", "cuda"],
        state="readonly",
        width=24,
    ).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Label(frame, text="本機精度").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Combobox(
        frame,
        textvariable=local_compute_var,
        values=["auto", "int8", "float16"],
        state="readonly",
        width=24,
    ).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    sep1 = ttk.Separator(frame, orient="horizontal")
    sep1.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 8))
    row += 1

    ttk.Label(frame, text="文字後處理（標點/斷句）模式").grid(row=row, column=0, sticky="w")
    ttk.Combobox(
        frame,
        textvariable=post_mode_var,
        values=["none", "heuristic", "command"],
        state="readonly",
        width=24,
    ).grid(row=row, column=1, sticky="ew")
    row += 1

    ttk.Label(frame, text="標點模型供應商（快速套用）").grid(row=row, column=0, sticky="w", pady=(8, 0))
    provider_box = ttk.Combobox(
        frame,
        textvariable=provider_var,
        values=["custom", "copilot", "gemini", "claude-code"],
        state="readonly",
        width=24,
    )
    provider_box.grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Label(frame, text="程式（program）").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=post_program_var, width=40).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Label(frame, text="參數（args）").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=post_args_var, width=64).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Label(frame, text="逾時秒數").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=post_timeout_var, width=12).grid(row=row, column=1, sticky="ew", pady=(8, 0))
    row += 1

    ttk.Label(
        frame,
        text="command 模式會把辨識文字送到 stdin；若 args 含 {text} 會直接代入。",
    ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
    row += 1

    sep2 = ttk.Separator(frame, orient="horizontal")
    sep2.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 8))
    row += 1

    ttk.Label(frame, text="熱鍵（每行一個，Fcitx Key 格式）").grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
    row += 1

    hotkey_text = tk.Text(frame, height=8, width=68)
    hotkey_text.grid(row=row, column=0, columnspan=2, sticky="ew")
    hotkey_text.insert("1.0", "\n".join(hotkeys))
    row += 1

    ttk.Checkbutton(frame, text="儲存後自動套用（reload + restart）", variable=auto_apply_var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(10, 0)
    )
    row += 1

    def on_provider_change(_event=None):
        provider = provider_var.get().strip()
        if provider not in PROVIDER_PRESETS or provider == "custom":
            return
        program, args = PROVIDER_PRESETS[provider]
        post_program_var.set(program)
        post_args_var.set(args)
        if post_mode_var.get() == "none":
            post_mode_var.set("command")

    provider_box.bind("<<ComboboxSelected>>", on_provider_change)

    def on_save():
        language = language_var.get().strip()
        if not language:
            messagebox.showerror("錯誤", "語言不可空白")
            return

        hotkey_lines = hotkey_text.get("1.0", "end").splitlines()
        new_hotkeys = [line.strip() for line in hotkey_lines if line.strip() and not line.strip().startswith("#")]
        if not new_hotkeys:
            messagebox.showerror("錯誤", "至少要一個熱鍵")
            return

        try:
            timeout_value = max(1.0, float(post_timeout_var.get().strip() or "12"))
        except ValueError:
            messagebox.showerror("錯誤", "逾時秒數格式錯誤")
            return

        new_cfg = {
            "backend": backend_var.get().strip() or "google",
            "language": language,
            "force_traditional": bool(force_traditional_var.get()),
            "process_on_stop": bool(process_on_stop_var.get()),
            "local_model": local_model_var.get().strip() or "small",
            "local_device": local_device_var.get().strip() or "auto",
            "local_compute_type": local_compute_var.get().strip() or "auto",
            "postprocess_mode": post_mode_var.get().strip() or "heuristic",
            "postprocess_provider": provider_var.get().strip() or "custom",
            "postprocess_program": post_program_var.get().strip(),
            "postprocess_args": post_args_var.get().strip(),
            "postprocess_timeout_sec": timeout_value,
            "auto_apply_on_save": bool(auto_apply_var.get()),
        }

        save_config(new_cfg, new_hotkeys)

        if auto_apply_var.get():
            ok, msg = apply_runtime(script_dir)
            if ok:
                messagebox.showinfo("已儲存並套用", f"設定已儲存並套用。\n\n{msg}")
            else:
                messagebox.showwarning("已儲存（套用部分失敗）", f"設定已儲存，但自動套用有問題：\n\n{msg}")
        else:
            messagebox.showinfo(
                "已儲存",
                "設定已儲存。\n請手動執行：\nfcitx5-remote -r\n./start.sh --stop && ./start.sh",
            )

    btn_row = ttk.Frame(frame)
    btn_row.grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 0))
    ttk.Button(btn_row, text="儲存設定", command=on_save).pack(side="left")
    ttk.Button(btn_row, text="關閉", command=root.destroy).pack(side="left", padx=(8, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
