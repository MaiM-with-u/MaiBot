from typing import Dict, List, Optional
import strawberry

# from packaging.version import Version, InvalidVersion
# from packaging.specifiers import SpecifierSet, InvalidSpecifier
# from ..config.config import global_config
# import os
from packaging.version import Version


@strawberry.type
class BotConfig:
    """机器人配置类"""

    INNER_VERSION: Version
    MAI_VERSION: str  # 硬编码的版本信息

    # bot
    BOT_QQ: Optional[int]
    BOT_NICKNAME: Optional[str]
    BOT_ALIAS_NAMES: List[str]  # 别名，可以通过这个叫它

    # group
    talk_allowed_groups: set
    talk_frequency_down_groups: set
    ban_user_id: set

    # personality
    personality_core: str  # 建议20字以内，谁再写3000字小作文敲谁脑袋
    personality_sides: List[str]
    # identity
    identity_detail: List[str]
    height: int  # 身高 单位厘米
    weight: int  # 体重 单位千克
    age: int  # 年龄 单位岁
    gender: str  # 性别
    appearance: str  # 外貌特征

    # schedule
    ENABLE_SCHEDULE_GEN: bool  # 是否启用日程生成
    PROMPT_SCHEDULE_GEN: str
    SCHEDULE_DOING_UPDATE_INTERVAL: int  # 日程表更新间隔 单位秒
    SCHEDULE_TEMPERATURE: float  # 日程表温度，建议0.5-1.0
    TIME_ZONE: str  # 时区

    # message
    MAX_CONTEXT_SIZE: int  # 上下文最大消息数
    emoji_chance: float  # 发送表情包的基础概率
    thinking_timeout: int  # 思考时间
    max_response_length: int  # 最大回复长度
    message_buffer: bool  # 消息缓冲器

    ban_words: set
    ban_msgs_regex: set
    # heartflow
    # enable_heartflow: bool = False  # 是否启用心流
    sub_heart_flow_update_interval: int  # 子心流更新频率，间隔 单位秒
    sub_heart_flow_freeze_time: int  # 子心流冻结时间，超过这个时间没有回复，子心流会冻结，间隔 单位秒
    sub_heart_flow_stop_time: int  # 子心流停止时间，超过这个时间没有回复，子心流会停止，间隔 单位秒
    heart_flow_update_interval: int  # 心流更新频率，间隔 单位秒
    observation_context_size: int  # 心流观察到的最长上下文大小，超过这个值的上下文会被压缩
    compressed_length: int  # 不能大于observation_context_size,心流上下文压缩的最短压缩长度，超过心流观察到的上下文长度，会压缩，最短压缩长度为5
    compress_length_limit: int  # 最多压缩份数，超过该数值的压缩上下文会被删除

    # willing
    willing_mode: str  # 意愿模式
    response_willing_amplifier: float  # 回复意愿放大系数
    response_interested_rate_amplifier: float  # 回复兴趣度放大系数
    down_frequency_rate: float  # 降低回复频率的群组回复意愿降低系数
    emoji_response_penalty: float  # 表情包回复惩罚
    mentioned_bot_inevitable_reply: bool  # 提及 bot 必然回复
    at_bot_inevitable_reply: bool  # @bot 必然回复

    # response
    response_mode: str  # 回复策略
    MODEL_R1_PROBABILITY: float  # R1模型概率
    MODEL_V3_PROBABILITY: float  # V3模型概率
    # MODEL_R1_DISTILL_PROBABILITY: float  # R1蒸馏模型概率

    # emoji
    max_emoji_num: int  # 表情包最大数量
    max_reach_deletion: bool  # 开启则在达到最大数量时删除表情包，关闭则不会继续收集表情包
    EMOJI_CHECK_INTERVAL: int  # 表情包检查间隔（分钟）
    EMOJI_REGISTER_INTERVAL: int  # 表情包注册间隔（分钟）
    EMOJI_SAVE: bool  # 偷表情包
    EMOJI_CHECK: bool  # 是否开启过滤
    EMOJI_CHECK_PROMPT: str  # 表情包过滤要求

    # memory
    build_memory_interval: int  # 记忆构建间隔（秒）
    memory_build_distribution: list  # 记忆构建分布，参数：分布1均值，标准差，权重，分布2均值，标准差，权重
    build_memory_sample_num: int  # 记忆构建采样数量
    build_memory_sample_length: int  # 记忆构建采样长度
    memory_compress_rate: float  # 记忆压缩率

    forget_memory_interval: int  # 记忆遗忘间隔（秒）
    memory_forget_time: int  # 记忆遗忘时间（小时）
    memory_forget_percentage: float  # 记忆遗忘比例

    memory_ban_words: list  # 添加新的配置项默认值

    # mood
    mood_update_interval: float  # 情绪更新间隔 单位秒
    mood_decay_rate: float  # 情绪衰减率
    mood_intensity_factor: float  # 情绪强度因子

    # keywords
    keywords_reaction_rules: list  # 关键词回复规则

    # chinese_typo
    chinese_typo_enable: bool  # 是否启用中文错别字生成器
    chinese_typo_error_rate: float  # 单字替换概率
    chinese_typo_min_freq: int  # 最小字频阈值
    chinese_typo_tone_error_rate: float  # 声调错误概率
    chinese_typo_word_replace_rate: float  # 整词替换概率

    # response_splitter
    enable_response_splitter: bool  # 是否启用回复分割器
    response_max_length: int  # 回复允许的最大长度
    response_max_sentence_num: int  # 回复允许的最大句子数

    # remote
    remote_enable: bool  # 是否启用远程控制

    # experimental
    enable_friend_chat: bool  # 是否启用好友聊天
    # enable_think_flow: bool  # 是否启用思考流程
    enable_pfc_chatting: bool  # 是否启用PFC聊天

    # 模型配置
    llm_reasoning: Dict[str, str]  # LLM推理
    # llm_reasoning_minor: Dict[str, str]
    llm_normal: Dict[str, str]  # LLM普通
    llm_topic_judge: Dict[str, str]  # LLM话题判断
    llm_summary_by_topic: Dict[str, str]  # LLM话题总结
    llm_emotion_judge: Dict[str, str]  # LLM情感判断
    embedding: Dict[str, str]  # 嵌入
    vlm: Dict[str, str]  # VLM
    moderation: Dict[str, str]  # 审核

    # 实验性
    llm_observation: Dict[str, str]  # LLM观察
    llm_sub_heartflow: Dict[str, str]  # LLM子心流
    llm_heartflow: Dict[str, str]  # LLM心流

    api_urls: Dict[str, str]  # API URLs

