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
