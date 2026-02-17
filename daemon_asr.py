#!/usr/bin/env python3
"""
Fcitx native addon companion daemon.
- Reads toggle command from /tmp/fcitx-asr-ime-cmd.fifo
- Performs STT via configurable backend (Google Web Speech / local Whisper)
- Sends text back to addon through /tmp/fcitx-asr-ime-commit.fifo
"""

import argparse
import errno
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Optional, Callable

import numpy as np

try:
    import sounddevice as sd
except OSError as e:
    raise SystemExit("sounddevice 載入失敗，請先安裝：sudo apt install -y libportaudio2") from e

try:
    import speech_recognition as sr
except ModuleNotFoundError as e:
    raise SystemExit("缺少 SpeechRecognition，請先執行：python -m pip install -r requirements.txt") from e

try:
    from faster_whisper import WhisperModel
except ModuleNotFoundError:
    WhisperModel = None

try:
    from opencc import OpenCC
except ModuleNotFoundError:
    OpenCC = None

# Compiled regexes used by smart edit functions for efficiency
# Chinese filler words are matched literally; we avoid word-boundary anchors
# because \b semantics vary with Unicode. We match the token occurrences and
# then collapse extra spaces. English fillers use \b and IGNORECASE to avoid
# removing substrings within words.

CHINESE_FILLERS = [
    "嗯",
    "啊",
    "那個",
    "這個",
    "就是",
    "然後",
    "對啊",
    "其實",
    "說實在",
    "呃",
    "欸",
]
ENGLISH_FILLERS = [
    "um",
    "uh",
    "like",
    "you know",
    "well",
    "so",
    "actually",
]

# Tone adjustment prompts for different tones. These are small instruction
# snippets injected into postprocess prompts to steer output style.
TONE_PROMPTS = {
    "casual": "請以輕鬆自然的語氣回應：",
    "formal": "請以正式且禮貌的語氣回應：",
    "professional": "請以專業且精準的語氣回應：",
    "creative": "請以創意且生動的語氣回應：",
}

# Language specific configurations (filler words and backend language codes)
LANGUAGE_CONFIGS = {
    "zh-TW": {
        "filler_words": CHINESE_FILLERS,
        "backend_language": "zh-TW",
    },
    "en-US": {
        "filler_words": ENGLISH_FILLERS,
        "backend_language": "en-US",
    },
    "ja-JP": {
        "filler_words": ["えーと", "あの", "まあ", "その", "えっと"],
        "backend_language": "ja-JP",
    },
}

# Compile patterns once for performance
_CHINESE_FILLER_RE = re.compile(r"(?:" + "|".join(re.escape(w) for w in CHINESE_FILLERS) + r")")
# English fillers use word boundaries and case-insensitive matching
_ENGLISH_FILLER_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in ENGLISH_FILLERS) + r")\b", flags=re.IGNORECASE)

# Self-correction patterns (try conservative matches in order)
# 1) "不是 A 而是 B" or "不是 A 是 B" -> keep prefix + B
# 2) "我是說 X", "我說 X" -> keep X
# 3) "應該是 X", "更正 X", "不對 X" -> keep X

# Match "不是 A 而是 B" capturing optional prefix before '不是'
_NOT_PATTERN = re.compile(r"(?P<prefix>.*?)不是\s*(?P<A>[^而是,，。.?!？]+?)\s*(?:而是|是)\s*(?P<B>.+)$")
# Simple correction markers capturing the corrected text
_I_MEANT_PATTERN = re.compile(r"(?:我是說|我說)\s*(?P<X>.+)$")
_SHOULD_BE_PATTERN = re.compile(r"(?:應該是|更正)[：:\s,，]*?(?P<X>.+)$")
_WRONG_PATTERN = re.compile(r"不對[：:\s,，]*?(?P<X>.+)$")


def filter_filler_words(text: str, language: Optional[str] = None) -> str:
    """Remove filler words from text using language-specific lists.

    If language is None, falls back to the legacy behaviour that removes a
    combination of Chinese and English filler words. For 'en-US' matching is
    performed case-insensitively with word boundaries. Returns the cleaned
    string, or the original text on regex errors.
    """
    try:
        s = str(text)
        if language and language in LANGUAGE_CONFIGS:
            fillers = LANGUAGE_CONFIGS[language].get("filler_words", [])
            if not fillers:
                return s
            if language == "en-US":
                pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in fillers) + r")\b", flags=re.IGNORECASE)
            else:
                pattern = re.compile(r"(?:" + "|".join(re.escape(w) for w in fillers) + r")")
            s = pattern.sub("", s)
        else:
            # Legacy combined behaviour
            s = _CHINESE_FILLER_RE.sub("", s)
            s = _ENGLISH_FILLER_RE.sub("", s)
        # Collapse multiple spaces and trim
        s = re.sub(r"\s+", " ", s)
        # Remove space before common punctuation
        s = re.sub(r"\s+([,\.，。!?！？:：;；])", r"\1", s)
        return s.strip()
    except re.error:
        return text


def detect_self_correction(text: str) -> str:
    """Detect and apply conservative self-corrections in text.

    Tries several patterns in order:
    - "不是 A 而是 B" / "不是 A 是 B" -> keep prefix + B (i.e., the corrected part)
    - "我是說 X" / "我說 X" -> keep X
    - "應該是 X", "更正 X", "不對 X" -> keep X

    The function avoids aggressive deletions by using conservative regexes and
    returning the input unchanged if no pattern matches.
    """
    try:
        t = str(text).strip()
        # Pattern: prefix 不是 A (而是|是) B -> return prefix + B
        m = _NOT_PATTERN.search(t)
        if m:
            prefix = (m.group("prefix") or "").strip()
            b = (m.group("B") or "").strip()
            if prefix:
                return (prefix + b).strip()
            return b

        # Other correction markers: keep the corrected fragment
        for pat in (_I_MEANT_PATTERN, _SHOULD_BE_PATTERN, _WRONG_PATTERN):
            m2 = pat.search(t)
            if m2:
                x = (m2.group("X") or "").strip()
                return x

        return t
    except re.error:
        return text


TARGET_SAMPLE_RATE = 16000
STATE_FILE = "/tmp/fcitx-asr-ime-state.json"
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
CONFIG_DIR = os.path.join(CONFIG_HOME, "asr-ime-fcitx")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HOTKEY_FILE = os.path.join(CONFIG_DIR, "hotkeys.conf")

# Vosk model download/config
VOSK_MODEL_DIR = os.path.expanduser("~/.cache/asr-ime-fcitx/vosk-models")
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"


def download_vosk_model(model_name: str = "vosk-model-small-cn-0.22") -> str:
    """Download and extract a Vosk model into the cache directory.

    If the model directory already exists, the function returns its path.
    On success returns the absolute path to the extracted model directory.
    Raises RuntimeError on failure.
    """
    model_path = os.path.join(VOSK_MODEL_DIR, model_name)
    if os.path.exists(model_path):
        return model_path
    os.makedirs(VOSK_MODEL_DIR, exist_ok=True)
    zip_path = os.path.join(VOSK_MODEL_DIR, model_name + ".zip")
    try:
        import urllib.request
        import zipfile

        print(f"Downloading Vosk model {model_name}...")
        urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(VOSK_MODEL_DIR)
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return model_path
    except Exception as e:
        raise RuntimeError(f"Failed to download or extract Vosk model: {e}")


class VoskStreamingRecognizer:
    """A thin wrapper around vosk.Model and KaldiRecognizer for streaming.

    process_chunk accepts a mono float32 numpy array and returns a tuple of
    (text, status) where status is either 'partial' or 'final'.
    """

    def __init__(self, model_path: str, sample_rate: int = 16000):
        try:
            from vosk import Model, KaldiRecognizer  # type: ignore
        except Exception:
            raise RuntimeError("Missing vosk, please pip install vosk")
        self.model = Model(model_path)
        self.sample_rate = int(sample_rate)
        self.rec = KaldiRecognizer(self.model, float(self.sample_rate))

    def process_chunk(self, audio_chunk: np.ndarray) -> tuple[str, str]:
        """Process a short audio chunk and return (text, status).

        audio_chunk should be a mono float32 numpy array in range [-1.0, 1.0].
        Returns (partial_text, 'partial') or (final_text, 'final').
        """
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32, copy=False)
        pcm16 = np.clip(audio_chunk, -1.0, 1.0)
        data = (pcm16 * 32767).astype(np.int16).tobytes()
        if self.rec.AcceptWaveform(data):
            try:
                res = json.loads(self.rec.Result())
            except Exception:
                return "", "final"
            return res.get("text", ""), "final"
        try:
            pres = json.loads(self.rec.PartialResult())
        except Exception:
            return "", "partial"
        return pres.get("partial", ""), "partial"

    def reset(self) -> None:
        """Reset the internal recognizer state for a new utterance."""
        try:
            self.rec.Reset()
        except Exception:
            # Some vosk versions may not expose Reset; recreate recognizer instead
            from vosk import KaldiRecognizer  # type: ignore

            self.rec = KaldiRecognizer(self.model, float(self.sample_rate))


DEFAULT_CONFIG = {
    "backend": "google",
    "language": "zh-TW",
    "force_traditional": True,
    "input_device": "auto",
    "auto_start_listening": True,
    "process_on_stop": True,
    "local_model": "small",
    "local_device": "auto",
    "local_compute_type": "auto",
    "postprocess_mode": "command",
    "postprocess_provider": "copilot",
    "postprocess_program": "copilot",
    "postprocess_args": '-s --model gpt-5-mini -p "請快速處理以下語音辨識結果：轉成繁體中文、補上自然標點與斷句、整理成短段落；不要新增內容，不要解釋，只回傳結果：{text}" --allow-all',
    "postprocess_timeout_sec": 12,
    # Smart edit options
    "enable_filler_filter": True,
    "enable_self_correction": True,
}


def notify(summary, body=""):
    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return
    cmd = [notify_bin, "-a", "ASR IME", "-t", "1400", summary]
    if body:
        cmd.append(body)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def show_partial_result(text: str) -> None:
    """Show a transient notification for partial ASR results."""
    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return
    try:
        subprocess.run([notify_bin, "ASR Partial Result", str(text)], check=False)
    except Exception:
        pass


def show_final_result(text: str) -> None:
    """Show a notification for the final ASR result."""
    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return
    try:
        subprocess.run([notify_bin, "ASR Final Result", str(text)], check=False)
    except Exception:
        pass


def update_state(state_file, **kwargs):
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    state.update(kwargs)
    state["updated_at"] = time.time()
    tmp = state_file + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, state_file)
    except Exception:
        pass


def load_user_config(path):
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return cfg
    except Exception:
        return cfg

    if not isinstance(raw, dict):
        return cfg
    for key in cfg:
        if key in raw:
            cfg[key] = raw[key]
    return cfg


def apply_config(args, cfg):
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

    if args.backend is None:
        args.backend = str(cfg.get("backend", "google")).lower()
    if args.language is None:
        args.language = str(cfg.get("language", "zh-TW"))
    if args.force_traditional is None:
        args.force_traditional = to_bool(cfg.get("force_traditional", True), True)
    if args.device is None:
        device_cfg = str(cfg.get("input_device", "auto")).strip()
        if device_cfg and device_cfg.lower() != "auto":
            args.device = device_cfg
    if args.process_on_stop is None:
        args.process_on_stop = to_bool(cfg.get("process_on_stop", True), True)
    if args.local_model is None:
        args.local_model = str(cfg.get("local_model", "small"))
    if args.local_device is None:
        args.local_device = str(cfg.get("local_device", "auto"))
    if args.local_compute_type is None:
        args.local_compute_type = str(cfg.get("local_compute_type", "auto"))
    if args.postprocess_mode is None:
        args.postprocess_mode = str(cfg.get("postprocess_mode", "heuristic")).lower()
    if args.postprocess_program is None:
        args.postprocess_program = str(cfg.get("postprocess_program", ""))
    if args.postprocess_args is None:
        args.postprocess_args = str(cfg.get("postprocess_args", ""))
    if args.postprocess_timeout_sec is None:
        timeout = cfg.get("postprocess_timeout_sec", 12)
        try:
            args.postprocess_timeout_sec = max(1.0, float(timeout))
        except Exception:
            args.postprocess_timeout_sec = 12.0

    if args.backend not in {"google", "local"}:
        args.backend = "google"
    if args.postprocess_mode not in {"none", "heuristic", "command", "smart"}:
        args.postprocess_mode = "heuristic"

    # Context memory config
    if getattr(args, 'enable_context_memory', None) is None:
        args.enable_context_memory = to_bool(cfg.get("enable_context_memory", False), False)
    if getattr(args, 'context_length', None) is None:
        try:
            cl = int(cfg.get("context_length", 5))
        except Exception:
            cl = 5
        # enforce sensible bounds
        args.context_length = max(1, min(10, cl))

def ensure_fifo(path):
    if os.path.exists(path):
        if os.path.isfile(path):
            os.remove(path)
    if not os.path.exists(path):
        os.mkfifo(path, 0o600)


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
        if "sysdefault" in n:
            score -= 120
        if n.strip() == "default":
            score += 15
        elif "default" in n:
            score += 8
        if "pulse" in n:
            score += 40
        if "pipewire" in n:
            score += 35
        if "usb" in n or "headset" in n or "mic" in n or "microphone" in n:
            score += 15
        if "input" in n:
            score += 10
        if "monitor" in n or "stereo mix" in n:
            score -= 120
        if "dummy" in n or "null" in n:
            score -= 120
        if "hw:" in n:
            score -= 10
        return score

    scores = {idx: score_name(name) for idx, name in devices}
    best_idx = devices[0][0]
    best_score = scores[best_idx]
    for idx, _ in devices:
        if scores[idx] > best_score:
            best_idx = idx
            best_score = scores[idx]

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


def heuristic_punctuate(text):
    t = re.sub(r"\s+", " ", text.strip())
    if not t:
        return t

    # Keep mixed Chinese/English readable.
    t = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", t)
    t = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", t)

    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", t))
    has_punct = bool(re.search(r"[，。！？；：,.!?;:]", t))
    if not has_cjk:
        if not has_punct and not t.endswith((".", "!", "?")):
            return t + "."
        return t

    if not has_punct:
        connectors = [
            "然後",
            "但是",
            "可是",
            "所以",
            "而且",
            "因為",
            "如果",
            "另外",
            "最後",
            "不過",
        ]
        for c in connectors:
            t = re.sub(fr"(?<!^)(?<![，。！？；：]){re.escape(c)}", f"，{c}", t)
        t = re.sub(r"，{2,}", "，", t)
        if not t.endswith(("。", "！", "？")):
            t += "。"
    return t


def build_tone_aware_prompt(base_prompt: str, tone: str) -> str:
    """Return a modified prompt that injects a tone instruction.

    The function looks for the placeholder "{text}" in base_prompt and
    injects a short tone instruction before the placeholder so downstream
    postprocess programs receive a tone-guided prompt. If the tone is unknown
    the base_prompt is returned unchanged.
    """
    try:
        instr = TONE_PROMPTS.get(tone, "")
        if not instr:
            return base_prompt
        if "{text}" in base_prompt:
            return base_prompt.replace("{text}", instr + "{text}")
        # If no placeholder is present, prepend the instruction to the prompt
        return instr + base_prompt
    except Exception:
        return base_prompt


def run_postprocess_command(text, program, args, timeout_sec, context_text=""):
    """Run an external postprocess command with optional context.

    If the command arguments contain the placeholder {text}, it will be replaced
    with a context-aware combined text (context + current text). Otherwise the
    combined text will be sent via stdin (prepended with context when present).

    Returns a tuple: (output_text, error_message). On error, the original text
    and an error description are returned.
    """
    if not program:
        return text, ""
    cmd = [program]
    uses_placeholder = False
    # Build combined text including context when available
    try:
        combined_text = (context_text + "\n" + text) if context_text else text
    except Exception:
        combined_text = text

    if args:
        parts = shlex.split(args)
        cmd_args = []
        for p in parts:
            if "{text}" in p:
                uses_placeholder = True
                # replace placeholder with the context-aware combined text
                cmd_args.append(p.replace("{text}", combined_text))
            else:
                cmd_args.append(p)
        cmd.extend(cmd_args)
    try:
        input_text = "" if uses_placeholder else combined_text
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=max(1.0, float(timeout_sec)),
            check=False,
        )
    except FileNotFoundError:
        return text, f"找不到後處理程式：{program}"
    except Exception as e:
        return text, f"後處理執行失敗：{e}"

    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        return text, f"後處理失敗：{err}"

    out = (proc.stdout or "").strip()
    return (out if out else text), ""


def get_next_language(current: str) -> str:
    """Return the next language code in rotation.

    Cycles through the supported languages in LANGUAGE_CONFIGS. Falls back to
    'zh-TW' for unknown or missing current values.
    """
    try:
        order = list(LANGUAGE_CONFIGS.keys())
        if not order:
            return "zh-TW"
        if not current or current not in order:
            return order[0]
        idx = order.index(current)
        return order[(idx + 1) % len(order)]
    except Exception:
        return "zh-TW"


def load_current_language(state_file: str) -> str:
    """Load the current language value from a state file.

    Returns the language code string (e.g. 'zh-TW'). Falls back to DEFAULT_CONFIG
    or 'zh-TW' if the state file is missing or invalid.
    """
    try:
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                st = json.load(f)
                lang = st.get("language") or st.get("current_language") or st.get("current")
                if isinstance(lang, str) and lang:
                    return lang
    except Exception:
        pass
    # Fallbacks
    try:
        return DEFAULT_CONFIG.get("language", "zh-TW")
    except Exception:
        return "zh-TW"


class ToggleState:
    def __init__(self):
        self._lock = threading.Lock()
        self._listening = False
        self._stopped = False

    def toggle(self):
        with self._lock:
            self._listening = not self._listening
            return self._listening

    def set_listening(self, value):
        with self._lock:
            self._listening = bool(value)

    def listening(self):
        with self._lock:
            return self._listening

    def stop(self):
        with self._lock:
            self._stopped = True
            self._listening = False

    def stopped(self):
        with self._lock:
            return self._stopped


class OnlineRecognizerWorker(threading.Thread):
    def __init__(
        self,
        backend,
        language,
        commit_fifo,
        verbose,
        queue_size,
        state_file,
        local_model,
        local_device,
        local_compute_type,
        postprocess_mode,
        postprocess_program,
        postprocess_args,
        postprocess_timeout_sec,
        force_traditional,
        enable_context_memory=False,
        context_length=5,
    ):
        super().__init__(daemon=True)
        self.backend = backend
        self.language = language
        self.commit_fifo = commit_fifo
        self.verbose = verbose
        self.jobs = queue.Queue(maxsize=queue_size)
        self.stop_event = threading.Event()
        self.recognizer = sr.Recognizer()
        self.state_file = state_file
        # default tone and per-instance language filler regex
        self.tone = "casual"
        self.local_language = (language or "zh").split("-")[0]
        # compile instance filler regex based on initial language
        try:
            lang_conf = LANGUAGE_CONFIGS.get(language or "zh-TW", {})
            fillers = lang_conf.get("filler_words", [])
            if fillers:
                if (language or "").startswith("en"):
                    self._LANGUAGE_FILLER_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in fillers) + r")\b", flags=re.IGNORECASE)
                else:
                    self._LANGUAGE_FILLER_RE = re.compile(r"(?:" + "|".join(re.escape(w) for w in fillers) + r")")
            else:
                self._LANGUAGE_FILLER_RE = None
        except Exception:
            self._LANGUAGE_FILLER_RE = None
        self.local_model_name = local_model
        self.local_device = local_device
        self.local_compute_type = local_compute_type
        self.local_model = None
        self.postprocess_mode = postprocess_mode
        self.postprocess_program = postprocess_program
        self.postprocess_args = postprocess_args
        self.postprocess_timeout_sec = postprocess_timeout_sec
        self.force_traditional = force_traditional
        self.converter = None
        if self.force_traditional and OpenCC is not None:
            self.converter = OpenCC("s2twp")
        elif self.force_traditional and OpenCC is None:
            print("[warn] 未安裝 OpenCC，暫時無法強制繁體")

        # Context memory settings
        try:
            self.enable_context_memory: bool = bool(enable_context_memory)
        except Exception:
            self.enable_context_memory = False
        try:
            self.context_length: int = max(1, int(context_length))
        except Exception:
            self.context_length = 5
        try:
            self.context_queue = deque(maxlen=self.context_length)
        except Exception:
            self.context_queue = deque(maxlen=5)

        if self.backend == "local":
            if WhisperModel is None:
                raise RuntimeError("缺少 faster-whisper，請執行：python -m pip install faster-whisper")
            device = self.local_device
            if device == "auto":
                device = "cuda" if shutil.which("nvidia-smi") else "cpu"
            compute_type = self.local_compute_type
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"
            self.local_device = device
            self.local_compute_type = compute_type
            self.local_model = WhisperModel(
                self.local_model_name,
                device=self.local_device,
                compute_type=self.local_compute_type,
            )
            print(
                f"Whisper 推論裝置: {self.local_device} ({self.local_compute_type}), "
                f"model={self.local_model_name}"
            )

    def stop(self):
        self.stop_event.set()

    def enqueue(self, audio, speech_seconds):
        payload = (audio, speech_seconds, time.perf_counter())
        try:
            self.jobs.put_nowait(payload)
        except queue.Full:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                pass
            self.jobs.put_nowait(payload)

    def _write_commit(self, text):
        if not text:
            return
        try:
            fd = os.open(self.commit_fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno in {errno.ENXIO, errno.ENOENT}:
                msg = "無法提交文字：請先切到 asrime 輸入法"
            else:
                msg = f"無法提交文字：{e}"
            print(f"[warn] {msg}")
            update_state(self.state_file, last_error=msg)
            notify("ASR 無法輸入文字", msg)
            return
        try:
            os.write(fd, (text + "\n").encode("utf-8", errors="ignore"))
        finally:
            os.close(fd)

    def transcribe_once(self, audio):
        if self.backend == "local":
            segments, _ = self.local_model.transcribe(
                audio,
                language=self.local_language,
                beam_size=1,
                vad_filter=True,
            )
            return "".join(seg.text for seg in segments)

        pcm16 = np.clip(audio, -1.0, 1.0)
        raw = (pcm16 * 32767).astype(np.int16).tobytes()
        audio_data = sr.AudioData(raw, TARGET_SAMPLE_RATE, 2)
        return self.recognizer.recognize_google(audio_data, language=self.language)

    def postprocess_text(self, text):
        """Postprocess text according to selected mode.

        Supports existing modes: none, heuristic, command, and a new 'smart'
        mode which applies filler filtering and self-correction before either
        running an external command (if configured) or returning the processed
        text.
        """
        if self.postprocess_mode == "none":
            return text, ""
        if self.postprocess_mode == "heuristic":
            return heuristic_punctuate(text), ""
        if self.postprocess_mode == "smart":
            # Load user config to read smart edit toggles; fall back to True
            try:
                cfg = load_user_config(CONFIG_FILE)
            except Exception:
                cfg = {}
            try:
                if bool(cfg.get("enable_filler_filter", True)):
                    text = filter_filler_words(text)
            except Exception:
                # Be conservative: on error, keep original text
                pass
            try:
                if bool(cfg.get("enable_self_correction", True)):
                    text = detect_self_correction(text)
            except Exception:
                pass
            if self.postprocess_program:
                context_text = self.get_context_text() if getattr(self, 'enable_context_memory', False) else ""
                args_local = self.postprocess_args
                try:
                    if args_local and "{text}" in args_local and getattr(self, "tone", None):
                        args_local = build_tone_aware_prompt(args_local, self.tone)
                except Exception:
                    pass
                return run_postprocess_command(
                    text,
                    self.postprocess_program,
                    args_local,
                    self.postprocess_timeout_sec,
                    context_text,
                )
            return text, ""
        context_text = self.get_context_text() if getattr(self, 'enable_context_memory', False) else ""
        args_local = self.postprocess_args
        try:
            if args_local and "{text}" in args_local and getattr(self, "tone", None):
                args_local = build_tone_aware_prompt(args_local, self.tone)
        except Exception:
            pass
        return run_postprocess_command(
            text,
            self.postprocess_program,
            args_local,
            self.postprocess_timeout_sec,
            context_text,
        )

    def normalize_text(self, text):
        if not self.force_traditional or not self.converter:
            return text
        try:
            return self.converter.convert(text)
        except Exception:
            return text

    def add_to_context(self, text: str) -> None:
        """Append a recognized text item to the context queue with a timestamp.

        The item stored is a dict with keys: 'text' and 'time'. Exceptions are
        caught and ignored to avoid interfering with recognition flow.
        """
        if not getattr(self, "enable_context_memory", False):
            return
        try:
            item = {"text": str(text), "time": time.time()}
            self.context_queue.append(item)
        except Exception:
            # Swallow context errors to avoid breaking main flow
            return

    def get_context_text(self) -> str:
        """Return formatted context text suitable for prepending to prompts.

        Each entry is formatted as '- text' joined by newlines. Returns empty
        string when there is no context.
        """
        if not getattr(self, "enable_context_memory", False):
            return ""
        try:
            parts = []
            for it in list(self.context_queue):
                t = it.get("text") if isinstance(it, dict) else str(it)
                t = (t or "").strip()
                if t:
                    parts.append(f"- {t}")
            return "\n".join(parts) if parts else ""
        except Exception:
            return ""

    def switch_language(self, new_language: str) -> None:
        """Switch worker language and update related settings.

        Updates self.language, self.local_language, compiles a language-specific
        filler-word regex on the instance for later use, and writes the new
        language to the STATE_FILE via update_state. Falls back to 'zh-TW' for
        unsupported languages.
        """
        try:
            lang = new_language if isinstance(new_language, str) else str(new_language or "")
            if lang not in LANGUAGE_CONFIGS:
                lang = "zh-TW"
            self.language = lang
            self.local_language = (lang or "zh-TW").split("-")[0]
            # compile per-instance filler regex
            fillers = LANGUAGE_CONFIGS.get(lang, {}).get("filler_words", [])
            try:
                if lang == "en-US":
                    self._LANGUAGE_FILLER_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in fillers) + r")\b", flags=re.IGNORECASE)
                else:
                    self._LANGUAGE_FILLER_RE = re.compile(r"(?:" + "|".join(re.escape(w) for w in fillers) + r")")
            except Exception:
                self._LANGUAGE_FILLER_RE = None
            # persist state if possible
            try:
                update_state(self.state_file, language=self.language)
            except Exception:
                pass
            notify("語言已切換", f"當前語言：{self.language}")
        except Exception:
            return

    def run(self):
        while not self.stop_event.is_set() or not self.jobs.empty():
            try:
                audio, speech_seconds, queued_at = self.jobs.get(timeout=0.1)
            except queue.Empty:
                continue

            decode_start = time.perf_counter()
            try:
                text = self.transcribe_once(audio).strip()
            except sr.UnknownValueError:
                msg = f"未辨識到語音（{speech_seconds:.2f}s），請確認麥克風與音量"
                update_state(self.state_file, last_error=msg)
                if self.verbose:
                    print(f"[asr:{self.backend}] empty ({speech_seconds:.2f}s audio)")
                continue
            except sr.RequestError as e:
                print(f"[warn] 線上 STT 請求失敗：{e}")
                update_state(self.state_file, last_error=f"線上 STT 請求失敗：{e}")
                notify("ASR 連線失敗", str(e))
                continue
            except Exception as e:
                print(f"[warn] 線上 STT 失敗：{e}")
                update_state(self.state_file, last_error=f"線上 STT 失敗：{e}")
                continue

            decode_sec = time.perf_counter() - decode_start
            e2e_sec = time.perf_counter() - queued_at
            if not text:
                msg = f"未辨識到語音（{speech_seconds:.2f}s），請確認麥克風與音量"
                update_state(self.state_file, last_error=msg)
                if self.verbose:
                    print(f"[asr:{self.backend}] empty ({speech_seconds:.2f}s audio)")
                continue

            raw_text = text
            pp_start = time.perf_counter()
            text, pp_err = self.postprocess_text(raw_text)
            pp_sec = time.perf_counter() - pp_start
            if pp_err:
                print(f"[warn] {pp_err}")

            text = self.normalize_text(text)
            pp_changed = text.strip() != raw_text.strip()
            if self.postprocess_mode == "command":
                print(
                    f"[postprocess:command] {pp_sec:.2f}s changed={1 if pp_changed else 0}"
                    + (f" err={pp_err}" if pp_err else "")
                )

            if not text:
                if self.verbose:
                    print(f"[asr:{self.backend}] empty after postprocess")
                continue

            self._write_commit(text)
            update_state(
                self.state_file,
                last_text=text,
                last_raw_text=raw_text,
                last_error="",
                last_postprocess_error=pp_err,
                last_postprocess_sec=round(pp_sec, 3),
                last_postprocess_changed=pp_changed,
            )
            # Add recognized text to context memory when enabled
            try:
                self.add_to_context(text)
            except Exception:
                pass
            notify("ASR 辨識結果", text[:80] + ("…" if len(text) > 80 else ""))
            print(
                f"[asr:{self.backend}] {text}  "
                f"(audio {speech_seconds:.2f}s / decode {decode_sec:.2f}s / e2e {e2e_sec:.2f}s)"
            )


def command_loop(cmd_fifo, state, state_file):
    while not state.stopped():
        try:
            with open(cmd_fifo, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    cmd = line.strip().lower()
                    if cmd == "toggle":
                        listening = state.toggle()
                        print("listening ON" if listening else "listening OFF")
                        update_state(state_file, listening=listening)
                        notify("ASR 錄音中" if listening else "ASR 已停止")
                    elif cmd == "start":
                        state.set_listening(True)
                        print("listening ON")
                        update_state(state_file, listening=True)
                        notify("ASR 錄音中")
                    elif cmd == "stop":
                        state.set_listening(False)
                        print("listening OFF")
                        update_state(state_file, listening=False)
                        notify("ASR 已停止")
                    elif cmd in {"quit", "exit"}:
                        state.stop()
                        update_state(state_file, listening=False)
                        return
                    elif cmd == "switch_language":
                        try:
                            cur = load_current_language(state_file)
                        except Exception:
                            cur = None
                        try:
                            nxt = get_next_language(cur)
                            # persist language
                            update_state(state_file, language=nxt)
                            print(f"language switched to {nxt}")
                            notify("語言切換", f"已切換至 {nxt}")
                        except Exception as e:
                            print(f"[warn] failed to switch language: {e}")
                            notify("語言切換失敗", str(e))
                        
        except FileNotFoundError:
            time.sleep(0.2)
        except Exception as e:
            print(f"[warn] command loop error: {e}")
            time.sleep(0.2)


def stream_loop(args, state, input_device, capture_rate, worker):
    blocksize = max(1, int(capture_rate * args.block_sec))
    block_sec = blocksize / capture_rate
    pre_roll_blocks = max(1, int(args.pre_roll_sec / block_sec))
    silence_blocks = max(1, int(args.silence_sec / block_sec))
    min_speech_blocks = max(1, int(args.min_speech_sec / block_sec))
    max_phrase_blocks = None
    if args.max_phrase_sec > 0:
        max_phrase_blocks = max(1, int(args.max_phrase_sec / block_sec))
    min_emit_samples = max(1, int(args.min_emit_sec * capture_rate))

    pre_roll = deque(maxlen=pre_roll_blocks)
    block_queue = queue.Queue(maxsize=args.audio_queue)
    session_blocks = []
    in_speech = False
    phrase_blocks = []
    speech_blocks = 0
    silence_run = 0
    last_state = False

    def enqueue_blocks(blocks):
        if not blocks:
            return False
        audio = np.concatenate(blocks).astype(np.float32)
        if audio.size < min_emit_samples:
            return False
        speech_seconds = audio.size / capture_rate
        worker.enqueue(resample_audio(audio, capture_rate, TARGET_SAMPLE_RATE), speech_seconds)
        return True

    def callback(indata, frames, time_info, status):
        if status and args.verbose:
            print(f"[audio] {status}")
        if not state.listening():
            return
        block = indata[:, 0].copy()
        try:
            block_queue.put_nowait(block)
        except queue.Full:
            try:
                block_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                block_queue.put_nowait(block)
            except queue.Full:
                pass

    with sd.InputStream(
        samplerate=capture_rate,
        channels=1,
        dtype="float32",
        callback=callback,
        device=input_device,
        blocksize=blocksize,
    ):
        print("daemon ready: hotkeys Ctrl+Alt+V / Ctrl+Alt+R / F8 / Shift+F8 (or ./start.sh --toggle).")
        while not state.stopped():
            listening = state.listening()
            if listening != last_state:
                last_state = listening
                if not listening:
                    if args.process_on_stop:
                        while True:
                            try:
                                session_blocks.append(block_queue.get_nowait())
                            except queue.Empty:
                                break
                        queued = enqueue_blocks(session_blocks)
                        if session_blocks and not queued:
                            msg = "錄音片段太短，請多說一點再按停止"
                            update_state(args.state_file, last_error=msg)
                            if args.verbose:
                                print(f"[stream] stop flush: skipped ({msg})")
                        if args.verbose:
                            print(f"[stream] stop flush: {'queued' if queued else 'skipped'}")
                    elif phrase_blocks:
                        if silence_run and len(phrase_blocks) > silence_run:
                            emit_blocks = phrase_blocks[:-silence_run]
                        else:
                            emit_blocks = phrase_blocks
                        enqueue_blocks(emit_blocks)

                    in_speech = False
                    phrase_blocks = []
                    session_blocks = []
                    speech_blocks = 0
                    silence_run = 0
                    pre_roll.clear()
                    while True:
                        try:
                            block_queue.get_nowait()
                        except queue.Empty:
                            break

            try:
                block = block_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if not listening:
                continue

            if args.process_on_stop:
                session_blocks.append(block)
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

            flush = (
                max_phrase_blocks is not None and speech_blocks >= max_phrase_blocks
            ) or (speech_blocks >= min_speech_blocks and silence_run >= silence_blocks)
            if not flush:
                continue

            if silence_run and len(phrase_blocks) > silence_run:
                emit_blocks = phrase_blocks[:-silence_run]
            else:
                emit_blocks = phrase_blocks

            if emit_blocks:
                enqueue_blocks(emit_blocks)

            in_speech = False
            phrase_blocks = []
            speech_blocks = 0
            silence_run = 0
            pre_roll.clear()


def parse_args():
    parser = argparse.ArgumentParser(description="Fcitx ASR daemon")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device", help="麥克風 index/序號/名稱關鍵字")
    parser.add_argument("--config-file", default=CONFIG_FILE)
    parser.add_argument("--language", default=None)
    parser.add_argument("--force-traditional", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--backend", choices=["google", "local"], default=None)
    parser.add_argument("--local-model", default=None, help="local 模式模型名稱（tiny/base/small/...）")
    parser.add_argument("--local-device", choices=["auto", "cpu", "cuda"], default=None)
    parser.add_argument("--local-compute-type", default=None, help="auto/int8/float16")
    parser.add_argument("--postprocess-mode", choices=["none", "heuristic", "command", "smart"], default=None)
    parser.add_argument("--postprocess-program", default=None, help="postprocess 程式（command 模式）")
    parser.add_argument("--postprocess-args", default=None, help="postprocess 參數（command 模式）")
    parser.add_argument("--postprocess-timeout-sec", type=float, default=None)
    parser.add_argument("--sample-rate", type=int, default=TARGET_SAMPLE_RATE)
    parser.add_argument("--enable-context-memory", action="store_true", default=None)
    parser.add_argument("--context-length", type=int, default=None)
    parser.add_argument("--speech-threshold", type=float, default=0.005)
    parser.add_argument("--silence-sec", type=float, default=0.35)
    parser.add_argument("--min-speech-sec", type=float, default=0.15)
    parser.add_argument("--max-phrase-sec", type=float, default=0.0, help="最大片段秒數，0=關閉硬切")
    parser.add_argument("--pre-roll-sec", type=float, default=0.15)
    parser.add_argument("--min-emit-sec", type=float, default=0.2)
    parser.add_argument("--block-sec", type=float, default=0.08)
    parser.add_argument("--queue-size", type=int, default=8)
    parser.add_argument("--audio-queue", type=int, default=128)
    parser.add_argument("--no-auto-probe-device", action="store_true")
    parser.add_argument("--cmd-fifo", default="/tmp/fcitx-asr-ime-cmd.fifo")
    parser.add_argument("--commit-fifo", default="/tmp/fcitx-asr-ime-commit.fifo")
    parser.add_argument("--state-file", default=STATE_FILE)
    parser.add_argument(
        "--process-on-stop",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="只在切回停止時辨識一次（預設開啟）",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = load_user_config(args.config_file)
    apply_config(args, cfg)

    if args.list_devices:
        for idx, name in list_input_devices():
            print(f"{idx}: {name}")
        return

    ensure_fifo(args.cmd_fifo)
    ensure_fifo(args.commit_fifo)
    update_state(
        args.state_file,
        listening=False,
        last_error="",
        mode=("on-stop" if args.process_on_stop else "silence"),
        backend=args.backend,
        postprocess_mode=args.postprocess_mode,
        postprocess_provider=str(cfg.get("postprocess_provider", "copilot")),
        force_traditional=args.force_traditional,
    )

    requested = resolve_input_device(args.device)
    input_device = requested
    if args.device is None and not args.no_auto_probe_device:
        best, scores = select_best_input_device(requested)
        names = {idx: name for idx, name in list_input_devices()}
        if args.verbose and scores:
            for idx, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
                print(f"[probe] device {idx} priority={score}")
        if best is not None and best != requested:
            best_score = scores.get(best, 0)
            requested_score = scores.get(requested, 0)
            best_name = str(names.get(best, "")).lower()
            should_switch = best_score >= (requested_score + 20) and "sysdefault" not in best_name
            if not should_switch and args.verbose:
                print(
                    f"[probe] keep requested device {requested} "
                    f"(best={best}, best_name={names.get(best, '')}, best_score={best_score}, requested_score={requested_score})"
                )
        else:
            should_switch = False
        if should_switch:
            input_device = best
            print(
                f"Auto-selected mic: {best} ({names.get(best, 'unknown')}, priority {best_score}) "
                f"instead of {requested} ({names.get(requested, 'unknown')}, priority {requested_score})"
            )

    capture_rate = pick_capture_rate(input_device, args.sample_rate)
    input_name = sd.query_devices(input_device)["name"]
    print(f"Input device: {input_device} ({input_name})")
    print(f"Capture rate: {capture_rate}Hz -> model {TARGET_SAMPLE_RATE}Hz")
    print(f"ASR backend: {args.backend}")
    update_state(
        args.state_file,
        input_device=f"{input_device} ({input_name})",
        capture_rate=capture_rate,
    )

    state = ToggleState()
    cmd_thread = threading.Thread(target=command_loop, args=(args.cmd_fifo, state, args.state_file), daemon=True)
    cmd_thread.start()

    try:
        worker = OnlineRecognizerWorker(
            backend=args.backend,
            language=args.language,
            commit_fifo=args.commit_fifo,
            verbose=args.verbose,
            queue_size=args.queue_size,
            state_file=args.state_file,
            local_model=args.local_model,
            local_device=args.local_device,
            local_compute_type=args.local_compute_type,
            postprocess_mode=args.postprocess_mode,
            postprocess_program=args.postprocess_program,
            postprocess_args=args.postprocess_args,
            postprocess_timeout_sec=args.postprocess_timeout_sec,
            force_traditional=args.force_traditional,
            enable_context_memory=args.enable_context_memory,
            context_length=args.context_length,
        )
    except Exception as e:
        msg = f"辨識器初始化失敗：{e}"
        print(f"[warn] {msg}")
        update_state(args.state_file, last_error=msg, listening=False)
        notify("ASR 初始化失敗", str(e))
        return
    worker.start()

    try:
        stream_loop(args, state, input_device, capture_rate, worker)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        state.stop()
        update_state(
            args.state_file,
            listening=False,
            backend=args.backend,
            postprocess_mode=args.postprocess_mode,
            postprocess_provider=str(cfg.get("postprocess_provider", "copilot")),
            force_traditional=args.force_traditional,
        )
        worker.stop()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
