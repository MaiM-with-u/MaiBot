from typing import List, Optional, Dict, Any
import strawberry

# from packaging.version import Version
import os

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@strawberry.type
class APIBotConfig:
    """机器人配置类"""

    INNER_VERSION: str  # 配置文件内部版本号（toml为字符串）
    MAI_VERSION: str  # 硬编码的版本信息

    # bot
    BOT_QQ: Optional[int]  # 机器人QQ号
    BOT_NICKNAME: Optional[str]  # 机器人昵称
    BOT_ALIAS_NAMES: List[str]  # 机器人别名列表

    # group
    talk_allowed_groups: List[int]  # 允许回复消息的群号列表
    talk_frequency_down_groups: List[int]  # 降低回复频率的群号列表
    ban_user_id: List[int]  # 禁止回复和读取消息的QQ号列表

    # personality
    personality_core: str  # 人格核心特点描述
    personality_sides: List[str]  # 人格细节描述列表

    # identity
    identity_detail: List[str]  # 身份特点列表
    age: int  # 年龄（岁）
    gender: str  # 性别
    appearance: str  # 外貌特征描述

    # platforms
    platforms: Dict[str, str]  # 平台信息

    # chat
    allow_focus_mode: bool  # 是否允许专注聊天状态
    base_normal_chat_num: int  # 最多允许多少个群进行普通聊天
    base_focused_chat_num: int  # 最多允许多少个群进行专注聊天
    observation_context_size: int  # 观察到的最长上下文大小
    message_buffer: bool  # 是否启用消息缓冲
    ban_words: List[str]  # 禁止词列表
    ban_msgs_regex: List[str]  # 禁止消息的正则表达式列表

    # normal_chat
    model_reasoning_probability: float  # 推理模型概率
    model_normal_probability: float  # 普通模型概率
    emoji_chance: float  # 表情符号出现概率
    thinking_timeout: int  # 思考超时时间
    willing_mode: str  # 意愿模式
    response_willing_amplifier: float  # 回复意愿放大器
    response_interested_rate_amplifier: float  # 回复兴趣率放大器
    down_frequency_rate: float  # 降低频率率
    emoji_response_penalty: float  # 表情回复惩罚
    mentioned_bot_inevitable_reply: bool  # 提及 bot 必然回复
    at_bot_inevitable_reply: bool  # @bot 必然回复

    # focus_chat
    reply_trigger_threshold: float  # 回复触发阈值
    default_decay_rate_per_second: float  # 默认每秒衰减率

    # compressed
    compressed_length: int  # 压缩长度
    compress_length_limit: int  # 压缩长度限制

    # emoji
    max_emoji_num: int  # 最大表情符号数量
    max_reach_deletion: bool  # 达到最大数量时是否删除
    check_interval: int  # 检查表情包的时间间隔(分钟)
    save_pic: bool  # 是否保存图片
    save_emoji: bool  # 是否保存表情包
    steal_emoji: bool  # 是否偷取表情包
    enable_check: bool  # 是否启用表情包过滤
    check_prompt: str  # 表情包过滤要求

    # memory
    build_memory_interval: int  # 记忆构建间隔
    build_memory_distribution: List[float]  # 记忆构建分布
    build_memory_sample_num: int  # 采样数量
    build_memory_sample_length: int  # 采样长度
    memory_compress_rate: float  # 记忆压缩率
    forget_memory_interval: int  # 记忆遗忘间隔
    memory_forget_time: int  # 记忆遗忘时间（小时）
    memory_forget_percentage: float  # 记忆遗忘比例
    consolidate_memory_interval: int  # 记忆整合间隔
    consolidation_similarity_threshold: float  # 相似度阈值
    consolidation_check_percentage: float  # 检查节点比例
    memory_ban_words: List[str]  # 记忆禁止词列表

    # mood
    mood_update_interval: float  # 情绪更新间隔
    mood_decay_rate: float  # 情绪衰减率
    mood_intensity_factor: float  # 情绪强度因子

    # keywords_reaction
    keywords_reaction_enable: bool  # 是否启用关键词反应
    keywords_reaction_rules: List[Dict[str, Any]]  # 关键词反应规则

    # chinese_typo
    chinese_typo_enable: bool  # 是否启用中文错别字
    chinese_typo_error_rate: float  # 中文错别字错误率
    chinese_typo_min_freq: int  # 中文错别字最小频率
    chinese_typo_tone_error_rate: float  # 中文错别字声调错误率
    chinese_typo_word_replace_rate: float  # 中文错别字单词替换率

    # response_splitter
    enable_response_splitter: bool  # 是否启用回复分割器
    response_max_length: int  # 回复最大长度
    response_max_sentence_num: int  # 回复最大句子数
    enable_kaomoji_protection: bool  # 是否启用颜文字保护

    model_max_output_length: int  # 模型最大输出长度

    # remote
    remote_enable: bool  # 是否启用远程功能

    # experimental
    enable_friend_chat: bool  # 是否启用好友聊天
    talk_allowed_private: List[int]  # 允许私聊的QQ号列表
    pfc_chatting: bool  # 是否启用PFC聊天

    # 模型配置
    llm_reasoning: Dict[str, Any]  # 推理模型配置
    llm_normal: Dict[str, Any]  # 普通模型配置
    llm_topic_judge: Dict[str, Any]  # 主题判断模型配置
    summary: Dict[str, Any]  # 总结模型配置
    vlm: Dict[str, Any]  # VLM模型配置
    llm_heartflow: Dict[str, Any]  # 心流模型配置
    llm_observation: Dict[str, Any]  # 观察模型配置
    llm_sub_heartflow: Dict[str, Any]  # 子心流模型配置
    llm_plan: Optional[Dict[str, Any]]  # 计划模型配置
    embedding: Dict[str, Any]  # 嵌入模型配置
    llm_PFC_action_planner: Optional[Dict[str, Any]]  # PFC行动计划模型配置
    llm_PFC_chat: Optional[Dict[str, Any]]  # PFC聊天模型配置
    llm_PFC_reply_checker: Optional[Dict[str, Any]]  # PFC回复检查模型配置
    llm_tool_use: Optional[Dict[str, Any]]  # 工具使用模型配置

    api_urls: Optional[Dict[str, str]]  # API地址配置

    @staticmethod
    def validate_config(config: dict):
        """
        校验传入的 toml 配置字典是否合法。
        :param config: toml库load后的配置字典
        :raises: ValueError, KeyError, TypeError
        """
        # 检查主层级
        required_sections = [
            "inner",
            "bot",
            "groups",
            "personality",
            "identity",
            "platforms",
            "chat",
            "normal_chat",
            "focus_chat",
            "emoji",
            "memory",
            "mood",
            "keywords_reaction",
            "chinese_typo",
            "response_splitter",
            "remote",
            "experimental",
            "model",
        ]
        for section in required_sections:
            if section not in config:
                raise KeyError(f"缺少配置段: [{section}]")

        # 检查部分关键字段
        if "version" not in config["inner"]:
            raise KeyError("缺少 inner.version 字段")
        if not isinstance(config["inner"]["version"], str):
            raise TypeError("inner.version 必须为字符串")

        if "qq" not in config["bot"]:
            raise KeyError("缺少 bot.qq 字段")
        if not isinstance(config["bot"]["qq"], int):
            raise TypeError("bot.qq 必须为整数")

        if "personality_core" not in config["personality"]:
            raise KeyError("缺少 personality.personality_core 字段")
        if not isinstance(config["personality"]["personality_core"], str):
            raise TypeError("personality.personality_core 必须为字符串")

        if "identity_detail" not in config["identity"]:
            raise KeyError("缺少 identity.identity_detail 字段")
        if not isinstance(config["identity"]["identity_detail"], list):
            raise TypeError("identity.identity_detail 必须为列表")

        # 可继续添加更多字段的类型和值检查
        # ...

        # 检查模型配置
        model_keys = [
            "llm_reasoning",
            "llm_normal",
            "llm_topic_judge",
            "summary",
            "vlm",
            "llm_heartflow",
            "llm_observation",
            "llm_sub_heartflow",
            "embedding",
        ]
        if "model" not in config:
            raise KeyError("缺少 [model] 配置段")
        for key in model_keys:
            if key not in config["model"]:
                raise KeyError(f"缺少 model.{key} 配置")

        # 检查通过
        return True


@strawberry.type
class APIEnvConfig:
    """环境变量配置"""

    HOST: str  # 服务主机地址
    PORT: int  # 服务端口

    PLUGINS: List[str]  # 插件列表

    MONGODB_HOST: str  # MongoDB 主机地址
    MONGODB_PORT: int  # MongoDB 端口
    DATABASE_NAME: str  # 数据库名称

    CHAT_ANY_WHERE_BASE_URL: str  # ChatAnywhere 基础URL
    SILICONFLOW_BASE_URL: str  # SiliconFlow 基础URL
    DEEP_SEEK_BASE_URL: str  # DeepSeek 基础URL

    DEEP_SEEK_KEY: Optional[str]  # DeepSeek API Key
    CHAT_ANY_WHERE_KEY: Optional[str]  # ChatAnywhere API Key
    SILICONFLOW_KEY: Optional[str]  # SiliconFlow API Key

    SIMPLE_OUTPUT: Optional[bool]  # 是否简化输出
    CONSOLE_LOG_LEVEL: Optional[str]  # 控制台日志等级
    FILE_LOG_LEVEL: Optional[str]  # 文件日志等级
    DEFAULT_CONSOLE_LOG_LEVEL: Optional[str]  # 默认控制台日志等级
    DEFAULT_FILE_LOG_LEVEL: Optional[str]  # 默认文件日志等级

    @strawberry.field
    def get_env(self) -> str:
        return "env"

    @staticmethod
    def validate_config(config: dict):
        """
        校验环境变量配置字典是否合法。
        :param config: 环境变量配置字典
        :raises: KeyError, TypeError
        """
        required_fields = [
            "HOST",
            "PORT",
            "PLUGINS",
            "MONGODB_HOST",
            "MONGODB_PORT",
            "DATABASE_NAME",
            "CHAT_ANY_WHERE_BASE_URL",
            "SILICONFLOW_BASE_URL",
            "DEEP_SEEK_BASE_URL",
        ]
        for field in required_fields:
            if field not in config:
                raise KeyError(f"缺少环境变量配置字段: {field}")

        if not isinstance(config["HOST"], str):
            raise TypeError("HOST 必须为字符串")
        if not isinstance(config["PORT"], int):
            raise TypeError("PORT 必须为整数")
        if not isinstance(config["PLUGINS"], list):
            raise TypeError("PLUGINS 必须为列表")
        if not isinstance(config["MONGODB_HOST"], str):
            raise TypeError("MONGODB_HOST 必须为字符串")
        if not isinstance(config["MONGODB_PORT"], int):
            raise TypeError("MONGODB_PORT 必须为整数")
        if not isinstance(config["DATABASE_NAME"], str):
            raise TypeError("DATABASE_NAME 必须为字符串")
        if not isinstance(config["CHAT_ANY_WHERE_BASE_URL"], str):
            raise TypeError("CHAT_ANY_WHERE_BASE_URL 必须为字符串")
        if not isinstance(config["SILICONFLOW_BASE_URL"], str):
            raise TypeError("SILICONFLOW_BASE_URL 必须为字符串")
        if not isinstance(config["DEEP_SEEK_BASE_URL"], str):
            raise TypeError("DEEP_SEEK_BASE_URL 必须为字符串")

        # 可选字段类型检查
        optional_str_fields = [
            "DEEP_SEEK_KEY",
            "CHAT_ANY_WHERE_KEY",
            "SILICONFLOW_KEY",
            "CONSOLE_LOG_LEVEL",
            "FILE_LOG_LEVEL",
            "DEFAULT_CONSOLE_LOG_LEVEL",
            "DEFAULT_FILE_LOG_LEVEL",
        ]
        for field in optional_str_fields:
            if field in config and config[field] is not None and not isinstance(config[field], str):
                raise TypeError(f"{field} 必须为字符串或None")

        if (
            "SIMPLE_OUTPUT" in config
            and config["SIMPLE_OUTPUT"] is not None
            and not isinstance(config["SIMPLE_OUTPUT"], bool)
        ):
            raise TypeError("SIMPLE_OUTPUT 必须为布尔值或None")

        # 检查通过
        return True


print("当前路径：")
print(ROOT_PATH)
