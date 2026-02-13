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
    root.geometry("680x520")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="ASR IME（Fcitx5）", font=("Sans", 13, "bold")).pack(anchor="w")

    log = tk.Text(frame, height=22, width=88)
    log.pack(fill="both", expand=True, pady=(10, 10))

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

    def do_status():
        ok, out = run_cmd([str(start_sh), "--status"], root_dir)
        append(out or ("狀態查詢失敗" if not ok else "無狀態"))

    def do_settings():
        if not py_bin.exists():
            messagebox.showerror("錯誤", "找不到虛擬環境 Python，請先執行 ./setup.sh --with-apt")
            return
        subprocess.Popen([str(py_bin), str(settings_py)], cwd=root_dir)

    btn_row = ttk.Frame(frame)
    btn_row.pack(anchor="w")

    ttk.Button(btn_row, text="啟動", command=do_start).pack(side="left")
    ttk.Button(btn_row, text="停止", command=do_stop).pack(side="left", padx=(8, 0))
    ttk.Button(btn_row, text="切換錄音", command=do_toggle).pack(side="left", padx=(8, 0))
    ttk.Button(btn_row, text="狀態", command=do_status).pack(side="left", padx=(8, 0))
    ttk.Button(btn_row, text="設定", command=do_settings).pack(side="left", padx=(8, 0))

    do_status()
    root.mainloop()


if __name__ == "__main__":
    main()
