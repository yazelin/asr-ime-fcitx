import os
import sys
import types

# Ensure project root is on sys.path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Provide lightweight stand-ins for heavy external modules so tests can import daemon_asr
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


def make_worker(**kwargs):
    params = dict(
        backend="google",
        language="zh-TW",
        commit_fifo="/tmp/fifo",
        verbose=False,
        queue_size=1,
        state_file="/tmp/state",
        local_model="small",
        local_device="auto",
        local_compute_type="auto",
        postprocess_mode="command",
        postprocess_program="",
        postprocess_args="",
        postprocess_timeout_sec=1,
        force_traditional=False,
    )
    params.update(kwargs)
    return ds.OnlineRecognizerWorker(**params)


def test_context_queue_basic():
    w = make_worker(enable_context_memory=True, context_length=5)
    w.add_to_context("first")
    w.add_to_context("second")
    txt = w.get_context_text()
    assert txt == "- first\n- second"


def test_context_queue_maxlen():
    w = make_worker(enable_context_memory=True, context_length=2)
    w.add_to_context("a")
    w.add_to_context("b")
    w.add_to_context("c")
    txt = w.get_context_text()
    assert txt == "- b\n- c"


def test_get_context_text():
    w = make_worker(enable_context_memory=True, context_length=3)
    # empty context
    assert w.get_context_text() == ""
    # adding empty/whitespace entries should be ignored in output
    w.add_to_context("")
    w.add_to_context("   ")
    w.add_to_context("ok")
    assert w.get_context_text() == "- ok"


def test_context_aware_prompt():
    w = make_worker(enable_context_memory=True, context_length=5, postprocess_mode="command", postprocess_program="cat", postprocess_args="", postprocess_timeout_sec=2)
    w.add_to_context("hello")
    w.add_to_context("world")
    out, err = w.postprocess_text("current")
    assert err == ""
    assert out == "- hello\n- world\ncurrent"


def test_context_disabled():
    w = make_worker(enable_context_memory=False, context_length=5, postprocess_mode="command", postprocess_program="cat", postprocess_args="", postprocess_timeout_sec=2)
    w.add_to_context("should_not_be_added")
    assert w.get_context_text() == ""
    out, err = w.postprocess_text("cur")
    assert err == ""
    assert out == "cur"
