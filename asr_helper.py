import re

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

_CHINESE_FILLER_RE = re.compile(r"(?:" + "|".join(re.escape(w) for w in CHINESE_FILLERS) + r")")
_ENGLISH_FILLER_RE = re.compile(r"(?<!\\w)(?:" + "|".join(re.escape(w) for w in ENGLISH_FILLERS) + r")(?!\\w)", flags=re.IGNORECASE)


def filter_filler_words(text: str) -> str:
    try:
        s = str(text)
        s = _CHINESE_FILLER_RE.sub("", s)
        s = _ENGLISH_FILLER_RE.sub("", s)
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"\s+([,\.，。!?！？:：;；])", r"\1", s)
        return s.strip()
    except re.error:
        return text


# Conservative self-correction patterns
_NOT_PATTERN = re.compile(r"(?P<prefix>.*?)不是\s*(?P<A>[^而是,，。.?!？]+?)\s*(?:而是|是)\s*(?P<B>.+)$")
_I_MEANT_PATTERN = re.compile(r"(?:我是說|我說)\s*(?P<X>.+)$")
_SHOULD_BE_PATTERN = re.compile(r"(?:應該是|更正)[：:\\s,，]*?(?P<X>.+)$")
_WRONG_PATTERN = re.compile(r"不對[：:\\s,，]*?(?P<X>.+)$")


def detect_self_correction(text: str) -> str:
    try:
        t = str(text).strip()
        m = _NOT_PATTERN.search(t)
        if m:
            prefix = (m.group("prefix") or "").strip()
            b = (m.group("B") or "").strip()
            if prefix:
                return (prefix + b).strip()
            return b
        for pat in (_I_MEANT_PATTERN, _SHOULD_BE_PATTERN, _WRONG_PATTERN):
            m2 = pat.search(t)
            if m2:
                x = (m2.group("X") or "").strip()
                return x
        return t
    except re.error:
        return text


def get_next_language(langs, current):
    """Return the next language in langs after current, cycling around."""
    if not langs:
        return current
    try:
        i = langs.index(current)
    except ValueError:
        return langs[0]
    return langs[(i + 1) % len(langs)]


def switch_language(current, langs):
    """Simulate pressing a hotkey to switch language."""
    return get_next_language(langs, current)


def tone_prompt(tone: str) -> str:
    """Return a short example prompt describing the tone for README/tests."""
    mapping = {
        "casual": "請用口語、自然且親切的語氣回寫。範例：『嗯 我今天去超市 買蘋果』->『我今天去超市買了蘋果。』",
        "formal": "請用正式且禮貌的書面語回寫。範例：『我今天去超市』->『我今天前往超市購物。』",
        "professional": "請用專業且簡潔的語氣回寫，適合工作/報告。範例：『完成了任務』->『該任務已完成。』",
        "creative": "請用較有創意或活潑的語氣回寫，可適度延展表達。範例：『今天天氣好』->『陽光燦爛，今天正是出門散步的好日子！』",
    }
    return mapping.get(tone, mapping["casual"])