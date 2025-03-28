from dataclasses import dataclass
from typing import List

@dataclass
class MemoryConfig:
    """记忆系统配置类"""
    # 记忆构建相关配置
    memory_build_distribution: List[float]  # 记忆构建的时间分布参数
    build_memory_sample_num: int  # 每次构建记忆的样本数量
    build_memory_sample_length: int  # 每个样本的消息长度
    memory_compress_rate: float  # 记忆压缩率
    
    # 记忆遗忘相关配置
    memory_forget_time: int  # 记忆遗忘时间（小时）
    
    # 记忆过滤相关配置
    memory_ban_words: List[str]  # 记忆过滤词列表

    llm_topic_judge: str  # 话题判断模型
    llm_summary_by_topic: str  # 话题总结模型
    
    @classmethod
    def from_global_config(cls, global_config):
        """从全局配置创建记忆系统配置"""
        return cls(
            memory_build_distribution=global_config.memory_build_distribution,
            build_memory_sample_num=global_config.build_memory_sample_num,
            build_memory_sample_length=global_config.build_memory_sample_length,
            memory_compress_rate=global_config.memory_compress_rate,
            memory_forget_time=global_config.memory_forget_time,
            memory_ban_words=global_config.memory_ban_words,
            llm_topic_judge=global_config.llm_topic_judge,
            llm_summary_by_topic=global_config.llm_summary_by_topic
        ) 