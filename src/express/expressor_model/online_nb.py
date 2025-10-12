import math
from typing import Dict, List
from collections import defaultdict, Counter

class OnlineNaiveBayes:
    def __init__(self, alpha: float = 0.5, beta: float = 0.5, gamma: float = 1.0, vocab_size: int = 200000):
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.V = vocab_size

        self.cls_counts: Dict[str, float] = defaultdict(float)                 # cid -> total token count
        self.token_counts: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))  # cid -> term -> count
        self._logZ: Dict[str, float] = {}                                      # cache log(∑counts + Vα)

    def _invalidate(self, cid: str):
        if cid in self._logZ:
            del self._logZ[cid]

    def _logZ_c(self, cid: str) -> float:
        if cid not in self._logZ:
            Z = self.cls_counts[cid] + self.V * self.alpha
            self._logZ[cid] = math.log(max(Z, 1e-12))
        return self._logZ[cid]

    def score_batch(self, tf: Counter, cids: List[str]) -> Dict[str, float]:
        total_cls = sum(self.cls_counts.values())
        n_cls = max(1, len(self.cls_counts))
        denom_prior = math.log(total_cls + self.beta * n_cls)

        out: Dict[str, float] = {}
        for cid in cids:
            prior = math.log(self.cls_counts[cid] + self.beta) - denom_prior
            s = prior
            logZ = self._logZ_c(cid)
            tc = self.token_counts[cid]
            for term, qtf in tf.items():
                num = tc.get(term, 0.0) + self.alpha
                s += qtf * (math.log(num) - logZ)
            out[cid] = s
        return out

    def update_positive(self, tf: Counter, cid: str):
        inc = 0.0
        tc = self.token_counts[cid]
        for term, c in tf.items():
            tc[term] += float(c)
            inc += float(c)
        self.cls_counts[cid] += inc
        self._invalidate(cid)

    def decay(self, factor: float = None):
        g = self.gamma if factor is None else factor
        if g >= 1.0:
            return
        for cid in list(self.cls_counts.keys()):
            self.cls_counts[cid] *= g
            for term in list(self.token_counts[cid].keys()):
                self.token_counts[cid][term] *= g
            self._invalidate(cid)