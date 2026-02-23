import os
import sys
import tempfile
import json
# Ensure project root is on sys.path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import settings_panel as sp


def test_to_bool_variants():
    assert sp.to_bool(True, False) is True
    assert sp.to_bool(False, True) is False
    assert sp.to_bool(1, False) is True
    assert sp.to_bool(0, True) is False
    assert sp.to_bool("yes", False) is True
    assert sp.to_bool("No", True) is False
    assert sp.to_bool("  true  ", False) is True
    assert sp.to_bool("unknown", "fallback") == "fallback"


def test_load_config_and_hotkeys_defaults(tmp_path, monkeypatch):
    # Ensure no config exists
    cfg_dir = tmp_path / "asr-ime-fcitx"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = sp.load_config()
    # Should return a dict and contain default keys
    assert isinstance(cfg, dict)
    for k in sp.DEFAULT_CONFIG:
        assert k in cfg

    hotkeys = sp.load_hotkeys()
    assert isinstance(hotkeys, list)
    assert hotkeys == list(sp.DEFAULT_HOTKEYS)


# --- Smart edit tests appended ---
import json
import pytest

# Provide lightweight stand-ins for heavy external modules so tests can import daemon_asr
import types
import sys
for _m in ("numpy", "sounddevice", "speech_recognition", "faster_whisper", "opencc"):
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)
# Provide expected attributes used by daemon_asr imports
_np = sys.modules["numpy"]
if not hasattr(_np, "ndarray"):
    _np.ndarray = type("ndarray", (), {})
    _np.float32 = "float32"
    _np.int16 = "int16"
    _np.clip = lambda x, a, b: x
sys.modules.setdefault("faster_whisper").WhisperModel = None
sys.modules.setdefault("opencc").OpenCC = None
# Minimal Recognizer stub for speech_recognition
class _DummyRecognizer:
    def __init__(self):
        pass
    def recognize_google(self, audio, language=None):
        return ""
sys.modules.setdefault("speech_recognition").Recognizer = _DummyRecognizer
import daemon_asr as ds


def test_filter_filler_words_chinese():
    s = "嗯 我想 要 那個 東西"
    out = ds.filter_filler_words(s)
    assert "嗯" not in out
    assert "那個" not in out
    assert "我想" in out


def test_filter_filler_words_english():
    s = "Um, I like, you know, this."
    out = ds.filter_filler_words(s)
    low = out.lower()
    assert "um" not in low
    assert "like" not in low
    assert "you know" not in low


@pytest.mark.parametrize(
    "inp,expected",
    [
        ("明天不是星期三而是星期四", "明天星期四"),
        ("今天不是 午後 是 晚上", "今天晚上"),
        ("我是說我要咖啡", "我要咖啡"),
        ("應該是下週一", "下週一"),
        ("更正：今天下午", "今天下午"),
        ("不對 我想要蘋果", "我想要蘋果"),
    ],
)
def test_detect_self_correction(inp, expected):
    out = ds.detect_self_correction(inp)
    assert expected in out


def test_pipeline_integration(tmp_path, monkeypatch):
    cfg = {"enable_filler_filter": True, "enable_self_correction": True}
    tmp_cfg = tmp_path / "config.json"
    tmp_cfg.write_text(json.dumps(cfg, ensure_ascii=False))
    monkeypatch.setattr(ds, "CONFIG_FILE", str(tmp_cfg))

    worker = ds.OnlineRecognizerWorker(
        backend="google",
        language="zh-TW",
        commit_fifo="/tmp/fifo",
        verbose=False,
        queue_size=1,
        state_file="/tmp/state",
        local_model="small",
        local_device="auto",
        local_compute_type="auto",
        postprocess_mode="smart",
        postprocess_program="",
        postprocess_args="",
        postprocess_timeout_sec=1,
        force_traditional=False,
    )

    text = "嗯 明天不是星期三而是星期四"
    out, err = worker.postprocess_text(text)
    assert err == ""
    assert "明天星期四" in out

