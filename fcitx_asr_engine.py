#!/usr/bin/env python3
"""
Fcitx5-oriented online ASR IME (via IBus frontend).
Ctrl+Alt+V toggles listening mode when this engine is active.
"""

import argparse
import queue
import threading
import time
from collections import deque

import numpy as np

try:
    import sounddevice as sd
except OSError as e:
    raise SystemExit("sounddevice 載入失敗，請先安裝：sudo apt install -y libportaudio2") from e

try:
    import speech_recognition as sr
except ModuleNotFoundError as e:
    raise SystemExit("缺少 SpeechRecognition，請先執行：python -m pip install -r requirements.txt") from e

TARGET_SAMPLE_RATE = 16000


def list_input_devices():
    devices = sd.query_devices()
    rows = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            rows.append((idx, dev["name"]))
    return rows


def resolve_input_device(device_arg):
    devices = list_input_devices()
    if not devices:
        raise SystemExit("找不到任何可用麥克風")

    if device_arg is None:
        default = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        if isinstance(default, int):
            for idx, _ in devices:
                if idx == default:
                    return idx
        return devices[0][0]

    try:
        idx = int(device_arg)
        if any(real_idx == idx for real_idx, _ in devices):
            return idx
        if 0 <= idx < len(devices):
            return devices[idx][0]
    except ValueError:
        pass

    keyword = device_arg.lower()
    for idx, name in devices:
        if keyword in name.lower():
            return idx
    raise SystemExit(f"找不到輸入裝置：{device_arg}")


def select_best_input_device(preferred_idx=None):
    devices = list_input_devices()
    if not devices:
        return None, {}

    def score_name(name):
        n = name.lower()
        score = 0
        if "default" in n:
            score += 50
        if "pulse" in n:
            score += 40
        if "pipewire" in n:
            score += 35
        if "usb" in n or "headset" in n or "mic" in n or "microphone" in n:
            score += 15
        if "hw:" in n:
            score -= 10
        return score

    scores = {idx: score_name(name) for idx, name in devices}
    best_idx = devices[0][0]
    best_score = scores[best_idx]
    for idx, _ in devices:
        s = scores[idx]
        if s > best_score:
            best_idx = idx
            best_score = s

    if preferred_idx in scores and scores[preferred_idx] >= best_score:
        return preferred_idx, scores
    return best_idx, scores


def pick_capture_rate(input_device, preferred_rate):
    dev = sd.query_devices(input_device, "input")
    default_rate = int(round(dev.get("default_samplerate", 0) or 0))
    candidates = [int(preferred_rate), default_rate, 48000, 44100, 32000, 24000, 22050, 16000, 8000]
    ordered = []
    for rate in candidates:
        if rate > 0 and rate not in ordered:
            ordered.append(rate)

    for rate in ordered:
        try:
            sd.check_input_settings(device=input_device, samplerate=rate, channels=1, dtype="float32")
            return rate
        except Exception:
            continue
    raise SystemExit("無法找到此麥克風可用取樣率；請改用 --device 指定其他麥克風")


def resample_audio(audio, src_rate, dst_rate=TARGET_SAMPLE_RATE):
    if src_rate == dst_rate:
        return audio.astype(np.float32, copy=False)
    if audio.size == 0:
        return np.empty(0, dtype=np.float32)
    dst_len = max(1, int(round(audio.size * float(dst_rate) / float(src_rate))))
    src_x = np.arange(audio.size, dtype=np.float64)
    dst_x = np.linspace(0, max(0, audio.size - 1), num=dst_len, dtype=np.float64)
    return np.interp(dst_x, src_x, audio).astype(np.float32)


def parse_args():
    parser = argparse.ArgumentParser(description="Fcitx5 線上語音輸入引擎（IBus frontend）")
    parser.add_argument("--list-devices", action="store_true", help="列出可用麥克風後離開")
    parser.add_argument("--device", help="麥克風 index/序號/名稱關鍵字")
    parser.add_argument("--language", default="zh-TW", help="語言代碼，例如 zh-TW / en-US")
    parser.add_argument("--suffix", default=" ", help="每段文字尾巴")
    parser.add_argument("--sample-rate", type=int, default=TARGET_SAMPLE_RATE, help="偏好錄音取樣率")
    parser.add_argument("--speech-threshold", type=float, default=0.005, help="語音門檻（越小越敏感）")
    parser.add_argument("--silence-sec", type=float, default=0.35, help="連續靜音多久送出一句")
    parser.add_argument("--min-speech-sec", type=float, default=0.15, help="最短語音長度")
    parser.add_argument("--max-phrase-sec", type=float, default=8.0, help="單句最長秒數")
    parser.add_argument("--pre-roll-sec", type=float, default=0.15, help="語音前導保留秒數")
    parser.add_argument("--min-emit-sec", type=float, default=0.2, help="小於此秒數的片段丟棄")
    parser.add_argument("--block-sec", type=float, default=0.08, help="音訊 block 秒數")
    parser.add_argument("--queue-size", type=int, default=8, help="轉寫工作佇列大小")
    parser.add_argument("--audio-queue", type=int, default=128, help="即時音訊佇列大小")
    parser.add_argument("--no-auto-probe-device", action="store_true", help="停用自動選 default/pulse/pipewire")
    parser.add_argument("--engine-name", default="asr-ime-fcitx-online", help="IBus engine name")
    parser.add_argument("--bus-name", default="org.freedesktop.IBus.ASRIMEFcitxOnline", help="IBus bus name")
    parser.add_argument("--verbose", action="store_true", help="顯示更多診斷資訊")
    return parser.parse_args()


def run_ibus_engine(args):
    try:
        import gi

        gi.require_version("IBus", "1.0")
        from gi.repository import GLib, GObject, IBus
    except Exception as e:
        raise SystemExit(
            "缺少 IBus Python 依賴，請先安裝：sudo apt install -y python3-gi gir1.2-ibus-1.0 ibus"
        ) from e

    class OnlineASREngine(IBus.Engine):
        __gtype_name__ = "EngineASRIMEFcitxOnline"

        def __init__(self):
            super().__init__()
            self.recognizer = sr.Recognizer()
            self.running = False
            self.capture_stop = threading.Event()
            self.jobs_stop = threading.Event()
            self.block_queue = queue.Queue(maxsize=args.audio_queue)
            self.jobs = queue.Queue(maxsize=args.queue_size)
            self.capture_thread = None
            self.segment_thread = None

            requested = resolve_input_device(args.device)
            self.input_device = requested
            if args.device is None and not args.no_auto_probe_device:
                best, scores = select_best_input_device(requested)
                if args.verbose and scores:
                    for idx, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
                        print(f"[probe] device {idx} priority={score}")
                if best is not None and best != requested and scores.get(best, 0) > scores.get(requested, 0):
                    self.input_device = best
                    print(
                        f"Auto-selected mic: {best} (priority {scores.get(best, 0)}) "
                        f"instead of {requested} (priority {scores.get(requested, 0)})"
                    )

            self.capture_rate = pick_capture_rate(self.input_device, args.sample_rate)
            self.transcriber_thread = threading.Thread(target=self._transcriber_loop, daemon=True)
            self.transcriber_thread.start()

            name = sd.query_devices(self.input_device)["name"]
            print(f"Engine ready: device={self.input_device} ({name}), capture={self.capture_rate}Hz")
            self._show_status("Ctrl+Alt+V 開始/停止語音輸入")

        def _show_status(self, text):
            GLib.idle_add(self._show_status_idle, text)

        def _show_status_idle(self, text):
            try:
                self.update_auxiliary_text(IBus.Text.new_from_string(text), True)
            except Exception:
                pass
            return False

        def _commit_text_idle(self, text, speech_sec, decode_sec, e2e_sec):
            try:
                self.commit_text(IBus.Text.new_from_string(f"{text}{args.suffix}"))
                if args.verbose:
                    print(
                        f"[online] {text}  (audio {speech_sec:.2f}s / decode {decode_sec:.2f}s / e2e {e2e_sec:.2f}s)"
                    )
            except Exception as e:
                print(f"[warn] commit 失敗：{e}", file=sys.stderr)
            return False

        def _enqueue_job(self, audio, speech_seconds):
            payload = (audio, speech_seconds, time.perf_counter())
            try:
                self.jobs.put_nowait(payload)
            except queue.Full:
                try:
                    self.jobs.get_nowait()
                except queue.Empty:
                    pass
                self.jobs.put_nowait(payload)

        def _transcribe_once(self, audio):
            pcm16 = np.clip(audio, -1.0, 1.0)
            raw = (pcm16 * 32767).astype(np.int16).tobytes()
            audio_data = sr.AudioData(raw, TARGET_SAMPLE_RATE, 2)
            return self.recognizer.recognize_google(audio_data, language=args.language)

        def _transcriber_loop(self):
            while not self.jobs_stop.is_set() or not self.jobs.empty():
                try:
                    audio, speech_seconds, queued_at = self.jobs.get(timeout=0.1)
                except queue.Empty:
                    continue

                decode_start = time.perf_counter()
                try:
                    text = self._transcribe_once(audio).strip()
                except sr.UnknownValueError:
                    if args.verbose:
                        print(f"[online] empty ({speech_seconds:.2f}s audio)")
                    continue
                except sr.RequestError as e:
                    print(f"[warn] 線上 STT 請求失敗：{e}", file=sys.stderr)
                    continue
                except Exception as e:
                    print(f"[warn] 線上 STT 失敗：{e}", file=sys.stderr)
                    continue

                if not text:
                    if args.verbose:
                        print(f"[online] empty ({speech_seconds:.2f}s audio)")
                    continue

                decode_sec = time.perf_counter() - decode_start
                e2e_sec = time.perf_counter() - queued_at
                GLib.idle_add(self._commit_text_idle, text, speech_seconds, decode_sec, e2e_sec)

        def _capture_loop(self):
            blocksize = max(1, int(self.capture_rate * args.block_sec))

            def callback(indata, frames, time_info, status):
                if status and args.verbose:
                    print(f"[audio] {status}", file=sys.stderr)
                block = indata[:, 0].copy()
                try:
                    self.block_queue.put_nowait(block)
                except queue.Full:
                    try:
                        self.block_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.block_queue.put_nowait(block)
                    except queue.Full:
                        pass

            try:
                with sd.InputStream(
                    samplerate=self.capture_rate,
                    channels=1,
                    dtype="float32",
                    callback=callback,
                    device=self.input_device,
                    blocksize=blocksize,
                ):
                    while not self.capture_stop.is_set():
                        time.sleep(0.1)
            except Exception as e:
                print(f"[warn] 麥克風流失敗：{e}", file=sys.stderr)
                self._show_status(f"麥克風錯誤：{e}")

        def _segment_loop(self):
            block_sec = args.block_sec
            pre_roll_blocks = max(1, int(args.pre_roll_sec / block_sec))
            silence_blocks = max(1, int(args.silence_sec / block_sec))
            min_speech_blocks = max(1, int(args.min_speech_sec / block_sec))
            max_phrase_blocks = max(1, int(args.max_phrase_sec / block_sec))
            min_emit_samples = max(1, int(args.min_emit_sec * self.capture_rate))

            pre_roll = deque(maxlen=pre_roll_blocks)
            in_speech = False
            phrase_blocks = []
            speech_blocks = 0
            silence_run = 0

            while not self.capture_stop.is_set() or not self.block_queue.empty():
                try:
                    block = self.block_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(np.square(block), dtype=np.float64)))
                voiced = rms >= args.speech_threshold

                if not in_speech:
                    pre_roll.append(block)
                    if voiced:
                        in_speech = True
                        phrase_blocks = list(pre_roll)
                        speech_blocks = len(phrase_blocks)
                        silence_run = 0
                    continue

                phrase_blocks.append(block)
                speech_blocks += 1
                silence_run = 0 if voiced else silence_run + 1

                flush = speech_blocks >= max_phrase_blocks or (
                    speech_blocks >= min_speech_blocks and silence_run >= silence_blocks
                )
                if not flush:
                    continue

                if silence_run and len(phrase_blocks) > silence_run:
                    emit_blocks = phrase_blocks[:-silence_run]
                else:
                    emit_blocks = phrase_blocks

                if emit_blocks:
                    audio = np.concatenate(emit_blocks).astype(np.float32)
                    if audio.size >= min_emit_samples:
                        speech_seconds = audio.size / self.capture_rate
                        audio_model = resample_audio(audio, self.capture_rate, TARGET_SAMPLE_RATE)
                        self._enqueue_job(audio_model, speech_seconds)

                in_speech = False
                phrase_blocks = []
                speech_blocks = 0
                silence_run = 0
                pre_roll.clear()

        def _clear_queue(self, q):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

        def start_listening(self):
            if self.running:
                return
            self.running = True
            self.capture_stop.clear()
            self._clear_queue(self.block_queue)
            self._show_status("🎤 Listening...")
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.segment_thread = threading.Thread(target=self._segment_loop, daemon=True)
            self.capture_thread.start()
            self.segment_thread.start()
            print("listening ON")

        def stop_listening(self):
            if not self.running:
                return
            self.running = False
            self.capture_stop.set()
            if self.capture_thread:
                self.capture_thread.join(timeout=1.0)
            if self.segment_thread:
                self.segment_thread.join(timeout=1.0)
            self._show_status("⏸️ Paused")
            print("listening OFF")

        def toggle_listening(self):
            if self.running:
                self.stop_listening()
            else:
                self.start_listening()

        def process_key_event(self, keyval, keycode, state):
            hotkey = int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.MOD1_MASK)
            mods = int(state & hotkey)
            released = bool(state & int(IBus.ModifierType.RELEASE_MASK))
            if not released and keyval in (IBus.KEY_v, IBus.KEY_V) and mods == hotkey:
                self.toggle_listening()
                return True
            return False

        def do_focus_in(self):
            self._show_status("Ctrl+Alt+V 開始/停止語音輸入")

        def do_destroy(self):
            self.stop_listening()
            self.jobs_stop.set()
            self.transcriber_thread.join(timeout=1.0)
            try:
                IBus.Engine.do_destroy(self)
            except Exception:
                pass

    IBus.init()
    bus = IBus.Bus()
    if not bus.is_connected():
        raise SystemExit("IBus bus 未連線，請先啟動：ibus-daemon -drx")

    factory = IBus.Factory.new(bus.get_connection())
    factory.add_engine(args.engine_name, OnlineASREngine.__gtype__)
    bus.request_name(args.bus_name, 0)

    print(f"IBus engine running: {args.engine_name}")
    print("切換到此輸入法後，按 Ctrl+Alt+V 開關語音輸入。")
    GLib.MainLoop().run()


def main():
    args = parse_args()
    if args.list_devices:
        for idx, name in list_input_devices():
            print(f"{idx}: {name}")
        return
    run_ibus_engine(args)


if __name__ == "__main__":
    main()
