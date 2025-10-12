from typing import Dict, Optional, Tuple, List
from collections import Counter, defaultdict
import pickle
import os

from .tokenizer import Tokenizer
from .online_nb import OnlineNaiveBayes

class ExpressorModel:
    """
    直接使用朴素贝叶斯精排（可在线学习）
    支持存储situation字段，不参与计算，仅与style对应
    """

    def __init__(self,
                 alpha: float = 0.5,
                 beta: float = 0.5,
                 gamma: float = 1.0,
                 vocab_size: int = 200000,
                 use_jieba: bool = True):
        self.tokenizer = Tokenizer(stopwords=set(), use_jieba=use_jieba)
        self.nb = OnlineNaiveBayes(alpha=alpha, beta=beta, gamma=gamma, vocab_size=vocab_size)
        self._candidates: Dict[str, str] = {}  # cid -> text (style)
        self._situations: Dict[str, str] = {}  # cid -> situation (不参与计算)

    def add_candidate(self, cid: str, text: str, situation: str = None):
        """添加候选文本和对应的situation"""
        self._candidates[cid] = text
        if situation is not None:
            self._situations[cid] = situation
        
        # 确保在nb模型中初始化该候选的计数
        if cid not in self.nb.cls_counts:
            self.nb.cls_counts[cid] = 0.0
        if cid not in self.nb.token_counts:
            self.nb.token_counts[cid] = defaultdict(float)

    def add_candidates_bulk(self, items: List[Tuple[str, str]], situations: List[str] = None):
        """批量添加候选文本和对应的situations"""
        for i, (cid, text) in enumerate(items):
            situation = situations[i] if situations and i < len(situations) else None
            self.add_candidate(cid, text, situation)

    def predict(self, text: str, k: int = None) -> Tuple[Optional[str], Dict[str, float]]:
        """直接对所有候选进行朴素贝叶斯评分"""
        toks = self.tokenizer.tokenize(text)
        if not toks:
            return None, {}
        
        if not self._candidates:
            return None, {}

        # 对所有候选进行评分
        tf = Counter(toks)
        all_cids = list(self._candidates.keys())
        scores = self.nb.score_batch(tf, all_cids)

        # 取最高分
        if not scores:
            return None, {}
        
        # 根据k参数限制返回的候选数量
        if k is not None and k > 0:
            # 按分数降序排序，取前k个
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            limited_scores = dict(sorted_scores[:k])
            best = sorted_scores[0][0] if sorted_scores else None
            return best, limited_scores
        else:
            # 如果没有指定k，返回所有分数
            best = max(scores.items(), key=lambda x: x[1])[0]
            return best, scores

    def update_positive(self, text: str, cid: str):
        """更新正反馈学习"""
        toks = self.tokenizer.tokenize(text)
        if not toks:
            return
        tf = Counter(toks)
        self.nb.update_positive(tf, cid)

    def decay(self, factor: float):
        self.nb.decay(factor=factor)
    
    def get_situation(self, cid: str) -> Optional[str]:
        """获取候选对应的situation"""
        return self._situations.get(cid)
    
    def get_style(self, cid: str) -> Optional[str]:
        """获取候选对应的style"""
        return self._candidates.get(cid)
    
    def get_candidate_info(self, cid: str) -> Tuple[Optional[str], Optional[str]]:
        """获取候选的style和situation信息"""
        return self._candidates.get(cid), self._situations.get(cid)
    
    def get_all_candidates(self) -> Dict[str, Tuple[str, Optional[str]]]:
        """获取所有候选的style和situation信息"""
        return {cid: (style, self._situations.get(cid)) 
                for cid, style in self._candidates.items()}

    def save(self, path: str):
        """保存模型"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "candidates": self._candidates,
                "situations": self._situations,
                "nb": {
                    "cls_counts": dict(self.nb.cls_counts),
                    "token_counts": {cid: dict(tc) for cid, tc in self.nb.token_counts.items()},
                    "alpha": self.nb.alpha,
                    "beta": self.nb.beta,
                    "gamma": self.nb.gamma,
                    "V": self.nb.V,
                }
            }, f)

    def load(self, path: str):
        """加载模型"""
        with open(path, "rb") as f:
            obj = pickle.load(f)
        # 还原候选文本
        self._candidates = obj["candidates"]
        # 还原situations（兼容旧版本）
        self._situations = obj.get("situations", {})
        # 还原朴素贝叶斯模型
        self.nb.cls_counts = obj["nb"]["cls_counts"]
        self.nb.token_counts = defaultdict_dict(obj["nb"]["token_counts"])
        self.nb.alpha = obj["nb"]["alpha"]
        self.nb.beta = obj["nb"]["beta"]
        self.nb.gamma = obj["nb"]["gamma"]
        self.nb.V = obj["nb"]["V"]
        self.nb._logZ.clear()

def defaultdict_dict(d: Dict[str, Dict[str, float]]):
    from collections import defaultdict
    outer = defaultdict(lambda: defaultdict(float))
    for k, inner in d.items():
        outer[k].update(inner)
    return outer