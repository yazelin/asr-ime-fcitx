#!/usr/bin/env python3
import subprocess
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception as e:
    raise SystemExit("缺少 tkinter，請先安裝：sudo apt install -y python3-tk") from e


def run_cmd(args, cwd):
    try:
        proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    except Exception as e:
        return False, str(e)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    merged = "\n".join(x for x in [out, err] if x)
    return proc.returncode == 0, merged


def main():
    root_dir = Path(__file__).resolve().parent
    start_sh = root_dir / "start.sh"
    settings_py = root_dir / "settings_panel.py"
    py_bin = root_dir / ".venv" / "bin" / "python"

    root = tk.Tk()
    root.title("ASR IME 控制面板")
    scaling = float(root.tk.call("tk", "scaling"))
    ui_scale = max(1.0, scaling / 1.4)
    root.geometry(f"{int(760 * ui_scale)}x{int(560 * ui_scale)}")
    root.minsize(640, 420)
    root.resizable(True, True)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)

    ttk.Label(frame, text="ASR IME（Fcitx5）", font=("Sans", 13, "bold")).grid(row=0, column=0, sticky="w")

    btn_row = ttk.Frame(frame)
    btn_row.grid(row=1, column=0, sticky="ew", pady=(10, 8))
    for i in range(3):
        btn_row.columnconfigure(i, weight=1)

    log_frame = ttk.Frame(frame)
    log_frame.grid(row=2, column=0, sticky="nsew")
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(0, weight=1)
    log = tk.Text(log_frame, height=1, wrap="word")
    log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log.yview)
    log.configure(yscrollcommand=log_scroll.set)
    log.grid(row=0, column=0, sticky="nsew")
    log_scroll.grid(row=0, column=1, sticky="ns")

    def append(text):
        if not text:
            return
        log.insert("end", text + "\n")
        log.see("end")

    def do_start():
        ok, out = run_cmd([str(start_sh)], root_dir)
        append(out or ("已啟動" if ok else "啟動失敗"))

    def do_stop():
        ok, out = run_cmd([str(start_sh), "--stop"], root_dir)
        append(out or ("已停止" if ok else "停止失敗"))

    def do_toggle():
        ok, out = run_cmd([str(start_sh), "--toggle"], root_dir)
        append(out or ("已切換錄音" if ok else "切換失敗"))

    def do_switch_im():
        run_cmd(["fcitx5-remote", "-o"], root_dir)
        ok, out = run_cmd(["fcitx5-remote", "-s", "asrime"], root_dir)
        _, current_im = run_cmd(["fcitx5-remote", "-n"], root_dir)
        if current_im:
            append(f"current_im: {current_im}")
        append(out or ("已切換到 asrime" if ok else "切換輸入法失敗"))

    def do_status():
        ok, out = run_cmd([str(start_sh), "--status"], root_dir)
        append(out or ("狀態查詢失敗" if not ok else "無狀態"))

    def do_settings():
        if not py_bin.exists():
            messagebox.showerror("錯誤", "找不到虛擬環境 Python，請先執行 ./setup.sh --with-apt")
            return
        if not settings_py.exists():
            messagebox.showerror("錯誤", f"找不到設定面板：{settings_py}")
            return
        try:
            subprocess.Popen([str(py_bin), str(settings_py)], cwd=root_dir)
        except Exception as e:
            messagebox.showerror("錯誤", f"開啟設定面板失敗：{e}")

    ttk.Button(btn_row, text="啟動", command=do_start).grid(row=0, column=0, sticky="ew")
    ttk.Button(btn_row, text="停止", command=do_stop).grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Button(btn_row, text="切換錄音(保底)", command=do_toggle).grid(row=0, column=2, sticky="ew")
    ttk.Button(btn_row, text="狀態", command=do_status).grid(row=1, column=0, sticky="ew", pady=(6, 0))
    ttk.Button(btn_row, text="設定", command=do_settings).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
    ttk.Button(btn_row, text="切到 ASR", command=do_switch_im).grid(row=1, column=2, sticky="ew", pady=(6, 0))

    do_status()
    root.mainloop()


if __name__ == "__main__":
    main()
