import re
from typing import List, Optional, Set

try:
    import jieba
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")

def simple_en_tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())

class Tokenizer:
    def __init__(self, stopwords: Optional[Set[str]] = None, use_jieba: bool = True):
        self.stopwords = stopwords or set()
        self.use_jieba = use_jieba and _HAS_JIEBA

    def tokenize(self, text: str) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []
        if self.use_jieba:
            toks = [t.strip().lower() for t in jieba.cut(text) if t.strip()]
        else:
            toks = simple_en_tokenize(text)
        return [t for t in toks if t not in self.stopwords]