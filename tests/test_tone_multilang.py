import asr_helper


def test_tone_prompt():
    # Ensure known tones map to prompts and include example markers
    p = asr_helper.tone_prompt("formal")
    assert "正式" in p or "書面" in p
    assert "範例" in p


def test_get_next_language():
    langs = ["zh-TW", "en-US", "ja-JP"]
    assert asr_helper.get_next_language(langs, "zh-TW") == "en-US"
    assert asr_helper.get_next_language(langs, "en-US") == "ja-JP"
    assert asr_helper.get_next_language(langs, "ja-JP") == "zh-TW"
    # Unknown current returns first
    assert asr_helper.get_next_language(langs, "ko-KR") == "zh-TW"


def test_language_switch():
    langs = ["zh-TW", "en-US", "ja-JP"]
    current = "zh-TW"
    seq = []
    for _ in range(5):
        current = asr_helper.switch_language(current, langs)
        seq.append(current)
    # After 5 switches starting from zh-TW we should have cycled accordingly
    assert seq == ["en-US", "ja-JP", "zh-TW", "en-US", "ja-JP"]


def test_language_specific_fillers():
    # Chinese fillers removed
    src = "嗯 我今天 去 超市 買 了 蘋果 然後 回家"
    out = asr_helper.filter_filler_words(src)
    assert "嗯" not in out
    assert "然後" not in out
    assert "我今天" in out

    # English fillers removed (case-insensitive)
    src2 = "Well, I think um this is good, you know"
    out2 = asr_helper.filter_filler_words(src2)
    assert "um" not in out2.lower()
    assert "you know" not in out2.lower()
    assert "think" in out2

    # Self-correction detection
    corr = "我不是要去吃飯 而是要去睡覺"
    got = asr_helper.detect_self_correction(corr)
    assert "睡覺" in got

    corr2 = "我是說 明天早上見"
    assert asr_helper.detect_self_correction(corr2) == "明天早上見"