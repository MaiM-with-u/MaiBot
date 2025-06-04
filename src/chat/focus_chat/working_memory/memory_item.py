from typing import Dict, Any, Tuple
import time
import random
import string


class MemoryItem:
    """记忆项类，用于存储单个记忆的所有相关信息"""

    def __init__(self, data: Any, from_source: str = "", brief: str = ""):
        """
        初始化记忆项

        Args:
            data: 记忆数据
            from_source: 数据来源
            brief: 记忆内容主题
        """
        # 生成可读ID：时间戳_随机字符串
        timestamp = int(time.time())
        random_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=2))
        self.id = f"{timestamp}_{random_str}"
        self.data = data
        self.data_type = type(data)
        self.from_source = from_source
        self.brief = brief
        self.timestamp = time.time()
        # 修改summary的结构说明，用于存储可能的总结信息
        # summary结构：{
        #   "detailed": "记忆内容概括",
        #   "keypoints": ["关键概念1", "关键概念2"],
        #   "events": ["事件1", "事件2"]
        # }
        self.summary = None

        # 记忆精简次数
        self.compress_count = 0

        # 记忆提取次数
        self.retrieval_count = 0

        # 记忆强度 (初始为10)
        self.memory_strength = 10.0

        # 记忆操作历史记录
        # 格式: [(操作类型, 时间戳, 当时精简次数, 当时强度), ...]
        self.history = [("create", self.timestamp, self.compress_count, self.memory_strength)]

    def matches_source(self, source: str) -> bool:
        """检查来源是否匹配"""
        return self.from_source == source

    def set_summary(self, summary: Dict[str, Any]) -> None:
        """设置总结信息"""
        self.summary = summary

    def increase_strength(self, amount: float) -> None:
        """增加记忆强度"""
        self.memory_strength = min(10.0, self.memory_strength + amount)
        # 记录操作历史
        self.record_operation("strengthen")

    def decrease_strength(self, amount: float) -> None:
        """减少记忆强度"""
        self.memory_strength = max(0.1, self.memory_strength - amount)
        # 记录操作历史
        self.record_operation("weaken")

    def increase_compress_count(self) -> None:
        """增加精简次数并减弱记忆强度"""
        self.compress_count += 1
        # 记录操作历史
        self.record_operation("compress")

    def record_retrieval(self) -> None:
        """记录记忆被提取的情况"""
        self.retrieval_count += 1
        # 提取后强度翻倍
        self.memory_strength = min(10.0, self.memory_strength * 2)
        # 记录操作历史
        self.record_operation("retrieval")

    def record_operation(self, operation_type: str) -> None:
        """记录操作历史"""
        current_time = time.time()
        self.history.append((operation_type, current_time, self.compress_count, self.memory_strength))

    def to_tuple(self) -> Tuple[Any, str, float, str]:
        """转换为元组格式（为了兼容性）"""
        return (self.data, self.from_source, self.timestamp, self.id)

    def is_memory_valid(self) -> bool:
        """检查记忆是否有效（强度是否大于等于1）"""
        return self.memory_strength >= 1.0
