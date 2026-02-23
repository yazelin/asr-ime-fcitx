#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:
    # Allow importing module in non-GUI test environments; GUI functions are only used in main().
    tk = None
    messagebox = None
    ttk = None

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
CONFIG_DIR = Path(CONFIG_HOME) / "asr-ime-fcitx"
CONFIG_FILE = CONFIG_DIR / "config.json"
HOTKEY_FILE = CONFIG_DIR / "hotkeys.conf"

DEFAULT_CONFIG = {
    "backend": "local",
    "language": "zh-TW",
    "input_device": "auto",
    "force_traditional": True,
    "process_on_stop": False,
    "speech_threshold": 0.13,
    "local_model": "large-v3",
    "local_device": "auto",
    "local_compute_type": "auto",
    "postprocess_mode": "heuristic",
    "command_provider": "copilot",
    "auto_apply_on_save": True,
}

DEFAULT_HOTKEYS = ["F8"]


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
    root.geometry(f"{int(640 * ui_scale)}x{int(520 * ui_scale)}")
    root.minsize(520, 400)
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

    backend_var = tk.StringVar(value=str(cfg.get("backend", "local")))
    input_device_var = tk.StringVar(value=str(cfg.get("input_device", "auto")))
    force_traditional_var = tk.BooleanVar(value=to_bool(cfg.get("force_traditional", True), True))
    process_on_stop_var = tk.BooleanVar(value=to_bool(cfg.get("process_on_stop", False), False))
    speech_threshold_var = tk.StringVar(value=str(cfg.get("speech_threshold", 0.13)))
    local_model_var = tk.StringVar(value=str(cfg.get("local_model", "large-v3")))
    local_device_var = tk.StringVar(value=str(cfg.get("local_device", "auto")))
    local_compute_var = tk.StringVar(value=str(cfg.get("local_compute_type", "auto")))
    command_provider_var = tk.StringVar(value=str(cfg.get("command_provider", "copilot")))
    auto_apply_var = tk.BooleanVar(value=to_bool(cfg.get("auto_apply_on_save", True), True))

    row = 0
    ttk.Label(frame, text="語音辨識後端").grid(row=row, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=backend_var, values=["local", "google"], state="readonly", width=24).grid(
        row=row, column=1, sticky="ew"
    )
    row += 1

    input_device_row = row
    ttk.Label(frame, text="麥克風（auto / index / 關鍵字）").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=input_device_var, width=28).grid(row=row, column=1, sticky="ew", pady=(8, 0))
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

    ttk.Label(frame, text="語音門檻（背景噪音過濾）").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frame, textvariable=speech_threshold_var, width=10).grid(row=row, column=1, sticky="w", pady=(8, 0))
    tk.Label(frame, text="越大越不容易誤觸，建議 0.05~0.3", fg="gray").grid(row=row, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
    row += 1

    ttk.Label(frame, text="指令模式 AI（Shift+F8）").grid(row=row, column=0, sticky="w", pady=(8, 0))
    ttk.Combobox(frame, textvariable=command_provider_var, values=["copilot", "claude"], state="readonly", width=24).grid(
        row=row, column=1, sticky="ew", pady=(8, 0)
    )
    tk.Label(frame, text="copilot=GPT-5-mini / claude=Haiku", fg="gray").grid(row=row, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
    row += 1

    sep1 = ttk.Separator(frame, orient="horizontal")
    sep1.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 8))
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

    sep2 = ttk.Separator(frame, orient="horizontal")
    sep2.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 8))
    row += 1

    ttk.Checkbutton(frame, text="儲存後自動套用（reload + restart）", variable=auto_apply_var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(10, 0)
    )
    row += 1

    def on_list_devices():
        py_bin = script_dir / ".venv" / "bin" / "python"
        daemon_py = script_dir / "daemon_asr.py"
        if not py_bin.exists() or not daemon_py.exists():
            messagebox.showerror("錯誤", "找不到 daemon 或虛擬環境，請先執行 ./setup.sh --with-apt")
            return
        try:
            proc = subprocess.run(
                [str(py_bin), str(daemon_py), "--list-devices"],
                cwd=script_dir,
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            messagebox.showerror("錯誤", f"列出麥克風失敗：{e}")
            return
        output = (proc.stdout or proc.stderr or "").strip()
        if not output:
            output = "未偵測到可用麥克風。"
        messagebox.showinfo("可用麥克風清單", output)

    ttk.Button(frame, text="列出麥克風", command=on_list_devices).grid(
        row=input_device_row,
        column=2,
        sticky="w",
        padx=(8, 0),
        pady=(8, 0),
    )

    def on_save():
        new_cfg = {
            "backend": backend_var.get().strip() or "local",
            "language": "zh-TW",
            "input_device": input_device_var.get().strip() or "auto",
            "force_traditional": bool(force_traditional_var.get()),
            "process_on_stop": bool(process_on_stop_var.get()),
            "speech_threshold": float(speech_threshold_var.get().strip() or "0.13"),
            "local_model": local_model_var.get().strip() or "large-v3",
            "local_device": local_device_var.get().strip() or "auto",
            "local_compute_type": local_compute_var.get().strip() or "auto",
            "postprocess_mode": "heuristic",
            "command_provider": command_provider_var.get().strip() or "copilot",
            "auto_apply_on_save": bool(auto_apply_var.get()),
        }

        save_config(new_cfg, ["F8"])

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
