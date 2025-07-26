import re

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from src.config.config_base import ConfigBase

"""
须知：
1. 本文件中记录了所有的配置项
2. 所有新增的class都需要继承自ConfigBase
3. 所有新增的class都应在config.py中的Config类中添加字段
4. 对于新增的字段，若为可选项，则应在其后添加field()并设置default_factory或default
"""


@dataclass
class BotConfig(ConfigBase):
    """QQ机器人配置类"""
    
    platform: str
    """平台"""

    qq_account: str
    """QQ账号"""

    nickname: str
    """昵称"""

    alias_names: list[str] = field(default_factory=lambda: [])
    """别名列表"""


@dataclass
class PersonalityConfig(ConfigBase):
    """人格配置类"""

    personality_core: str
    """核心人格"""

    personality_side: str
    """人格侧写"""

    identity: str = ""
    """身份特征"""

    compress_personality: bool = True
    """是否压缩人格，压缩后会精简人格信息，节省token消耗并提高回复性能，但是会丢失一些信息，如果人设不长，可以关闭"""

    compress_identity: bool = True
    """是否压缩身份，压缩后会精简身份信息，节省token消耗并提高回复性能，但是会丢失一些信息，如果不长，可以关闭"""


@dataclass
class RelationshipConfig(ConfigBase):
    """关系配置类"""

    enable_relationship: bool = True
    """是否启用关系系统"""

    relation_frequency: int = 1
    """关系频率，麦麦构建关系的速度"""


@dataclass
class ChatConfig(ConfigBase):
    """聊天配置类"""

    max_context_size: int = 18
    """上下文长度"""
    
    willing_amplifier: float = 1.0

    replyer_random_probability: float = 0.5
    """
    发言时选择推理模型的概率（0-1之间）
    选择普通模型的概率为 1 - reasoning_normal_model_probability
    """

    thinking_timeout: int = 40
    """麦麦最长思考规划时间，超过这个时间的思考会放弃（往往是api反应太慢）"""

    talk_frequency: float = 1
    """回复频率阈值"""

    mentioned_bot_inevitable_reply: bool = False
    """提及 bot 必然回复"""

    at_bot_inevitable_reply: bool = False
    """@bot 必然回复"""

    # 修改：基于时段的回复频率配置，改为数组格式
    time_based_talk_frequency: list[str] = field(default_factory=lambda: [])
    """
    基于时段的回复频率配置（全局）
    格式：["HH:MM,frequency", "HH:MM,frequency", ...]
    示例：["8:00,1", "12:00,2", "18:00,1.5", "00:00,0.5"]
    表示从该时间开始使用该频率，直到下一个时间点
    """

    # 新增：基于聊天流的个性化时段频率配置
    talk_frequency_adjust: list[list[str]] = field(default_factory=lambda: [])
    """
    基于聊天流的个性化时段频率配置
    格式：[["platform:chat_id:type", "HH:MM,frequency", "HH:MM,frequency", ...], ...]
    示例：[
        ["qq:1026294844:group", "12:20,1", "16:10,2", "20:10,1", "00:10,0.3"],
        ["qq:729957033:group", "8:20,1", "12:10,2", "20:10,1.5", "00:10,0.2"]
    ]
    每个子列表的第一个元素是聊天流标识符，后续元素是"时间,频率"格式
    表示从该时间开始使用该频率，直到下一个时间点
    """

    focus_value: float = 1.0
    """麦麦的专注思考能力，越低越容易专注，消耗token也越多"""

    def get_current_talk_frequency(self, chat_stream_id: Optional[str] = None) -> float:
        """
        根据当前时间和聊天流获取对应的 talk_frequency

        Args:
            chat_stream_id: 聊天流ID，格式为 "platform:chat_id:type"

        Returns:
            float: 对应的频率值
        """
        # 优先检查聊天流特定的配置
        if chat_stream_id and self.talk_frequency_adjust:
            stream_frequency = self._get_stream_specific_frequency(chat_stream_id)
            if stream_frequency is not None:
                return stream_frequency

        # 如果没有聊天流特定配置，检查全局时段配置
        if self.time_based_talk_frequency:
            global_frequency = self._get_time_based_frequency(self.time_based_talk_frequency)
            if global_frequency is not None:
                return global_frequency

        # 如果都没有匹配，返回默认值
        return self.talk_frequency

    def _get_time_based_frequency(self, time_freq_list: list[str]) -> Optional[float]:
        """
        根据时间配置列表获取当前时段的频率

        Args:
            time_freq_list: 时间频率配置列表，格式为 ["HH:MM,frequency", ...]

        Returns:
            float: 频率值，如果没有配置则返回 None
        """
        from datetime import datetime

        current_time = datetime.now().strftime("%H:%M")
        current_hour, current_minute = map(int, current_time.split(":"))
        current_minutes = current_hour * 60 + current_minute

        # 解析时间频率配置
        time_freq_pairs = []
        for time_freq_str in time_freq_list:
            try:
                time_str, freq_str = time_freq_str.split(",")
                hour, minute = map(int, time_str.split(":"))
                frequency = float(freq_str)
                minutes = hour * 60 + minute
                time_freq_pairs.append((minutes, frequency))
            except (ValueError, IndexError):
                continue

        if not time_freq_pairs:
            return None

        # 按时间排序
        time_freq_pairs.sort(key=lambda x: x[0])

        # 查找当前时间对应的频率
        current_frequency = None
        for minutes, frequency in time_freq_pairs:
            if current_minutes >= minutes:
                current_frequency = frequency
            else:
                break

        # 如果当前时间在所有配置时间之前，使用最后一个时间段的频率（跨天逻辑）
        if current_frequency is None and time_freq_pairs:
            current_frequency = time_freq_pairs[-1][1]

        return current_frequency

    def _get_stream_specific_frequency(self, chat_stream_id: str):
        """
        获取特定聊天流在当前时间的频率

        Args:
            chat_stream_id: 聊天流ID（哈希值）

        Returns:
            float: 频率值，如果没有配置则返回 None
        """
        # 查找匹配的聊天流配置
        for config_item in self.talk_frequency_adjust:
            if not config_item or len(config_item) < 2:
                continue

            stream_config_str = config_item[0]  # 例如 "qq:1026294844:group"

            # 解析配置字符串并生成对应的 chat_id
            config_chat_id = self._parse_stream_config_to_chat_id(stream_config_str)
            if config_chat_id is None:
                continue

            # 比较生成的 chat_id
            if config_chat_id != chat_stream_id:
                continue

            # 使用通用的时间频率解析方法
            return self._get_time_based_frequency(config_item[1:])

        return None

    def _parse_stream_config_to_chat_id(self, stream_config_str: str) -> Optional[str]:
        """
        解析流配置字符串并生成对应的 chat_id

        Args:
            stream_config_str: 格式为 "platform:id:type" 的字符串

        Returns:
            str: 生成的 chat_id，如果解析失败则返回 None
        """
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None

            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]

            # 判断是否为群聊
            is_group = stream_type == "group"

            # 使用与 ChatStream.get_stream_id 相同的逻辑生成 chat_id
            import hashlib

            if is_group:
                components = [platform, str(id_str)]
            else:
                components = [platform, str(id_str), "private"]
            key = "_".join(components)
            return hashlib.md5(key.encode()).hexdigest()

        except (ValueError, IndexError):
            return None


@dataclass
class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    ban_words: set[str] = field(default_factory=lambda: set())
    """过滤词列表"""

    ban_msgs_regex: set[str] = field(default_factory=lambda: set())
    """过滤正则表达式列表"""


@dataclass
class NormalChatConfig(ConfigBase):
    """普通聊天配置类"""

    willing_mode: str = "classical"
    """意愿模式"""

@dataclass
class ExpressionConfig(ConfigBase):
    """表达配置类"""

    enable_expression: bool = True
    """是否启用表达方式"""

    expression_style: str = ""
    """表达风格"""

    learning_interval: int = 300
    """学习间隔（秒）"""

    enable_expression_learning: bool = True
    """是否启用表达学习"""

    expression_groups: list[list[str]] = field(default_factory=list)
    """
    表达学习互通组
    格式: [["qq:12345:group", "qq:67890:private"]]
    """


@dataclass
class ToolConfig(ConfigBase):
    """工具配置类"""

    enable_tool: bool = False
    """是否在聊天中启用工具"""
    
@dataclass
class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    enable_asr: bool = False
    """是否启用语音识别"""


@dataclass
class EmojiConfig(ConfigBase):
    """表情包配置类"""

    emoji_chance: float = 0.6
    """发送表情包的基础概率"""

    emoji_activate_type: str = "random"
    """表情包激活类型，可选：random，llm，random下，表情包动作随机启用，llm下，表情包动作根据llm判断是否启用"""

    max_reg_num: int = 200
    """表情包最大注册数量"""

    do_replace: bool = True
    """达到最大注册数量时替换旧表情包"""

    check_interval: int = 120
    """表情包检查间隔（分钟）"""

    steal_emoji: bool = True
    """是否偷取表情包，让麦麦可以发送她保存的这些表情包"""

    content_filtration: bool = False
    """是否开启表情包过滤"""

    filtration_prompt: str = "符合公序良俗"
    """表情包过滤要求"""


@dataclass
class MemoryConfig(ConfigBase):
    """记忆配置类"""

    enable_memory: bool = True

    memory_build_interval: int = 600
    """记忆构建间隔（秒）"""

    memory_build_distribution: tuple[
        float,
        float,
        float,
        float,
        float,
        float,
    ] = field(default_factory=lambda: (6.0, 3.0, 0.6, 32.0, 12.0, 0.4))
    """记忆构建分布，参数：分布1均值，标准差，权重，分布2均值，标准差，权重"""

    memory_build_sample_num: int = 8
    """记忆构建采样数量"""

    memory_build_sample_length: int = 40
    """记忆构建采样长度"""

    memory_compress_rate: float = 0.1
    """记忆压缩率"""

    forget_memory_interval: int = 1000
    """记忆遗忘间隔（秒）"""

    memory_forget_time: int = 24
    """记忆遗忘时间（小时）"""

    memory_forget_percentage: float = 0.01
    """记忆遗忘比例"""

    consolidate_memory_interval: int = 1000
    """记忆整合间隔（秒）"""

    consolidation_similarity_threshold: float = 0.7
    """整合相似度阈值"""

    consolidate_memory_percentage: float = 0.01
    """整合检查节点比例"""

    memory_ban_words: list[str] = field(default_factory=lambda: ["表情包", "图片", "回复", "聊天记录"])
    """不允许记忆的词列表"""
    
    enable_instant_memory: bool = True
    """是否启用即时记忆"""


@dataclass
class MoodConfig(ConfigBase):
    """情绪配置类"""

    enable_mood: bool = False
    """是否启用情绪系统"""
    
    mood_update_threshold: float = 1.0
    """情绪更新阈值,越高，更新越慢"""


@dataclass
class KeywordRuleConfig(ConfigBase):
    """关键词规则配置类"""

    keywords: list[str] = field(default_factory=lambda: [])
    """关键词列表"""

    regex: list[str] = field(default_factory=lambda: [])
    """正则表达式列表"""

    reaction: str = ""
    """关键词触发的反应"""

    def __post_init__(self):
        """验证配置"""
        if not self.keywords and not self.regex:
            raise ValueError("关键词规则必须至少包含keywords或regex中的一个")

        if not self.reaction:
            raise ValueError("关键词规则必须包含reaction")

        # 验证正则表达式
        for pattern in self.regex:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"无效的正则表达式 '{pattern}': {str(e)}") from e


@dataclass
class KeywordReactionConfig(ConfigBase):
    """关键词配置类"""

    keyword_rules: list[KeywordRuleConfig] = field(default_factory=lambda: [])
    """关键词规则列表"""

    regex_rules: list[KeywordRuleConfig] = field(default_factory=lambda: [])
    """正则表达式规则列表"""

    def __post_init__(self):
        """验证配置"""
        # 验证所有规则
        for rule in self.keyword_rules + self.regex_rules:
            if not isinstance(rule, KeywordRuleConfig):
                raise ValueError(f"规则必须是KeywordRuleConfig类型，而不是{type(rule).__name__}")

@dataclass
class CustomPromptConfig(ConfigBase):
    """自定义提示词配置类"""

    image_prompt: str = ""
    """图片提示词"""


@dataclass
class ResponsePostProcessConfig(ConfigBase):
    """回复后处理配置类"""

    enable_response_post_process: bool = True
    """是否启用回复后处理，包括错别字生成器，回复分割器"""


@dataclass
class ChineseTypoConfig(ConfigBase):
    """中文错别字配置类"""

    enable: bool = True
    """是否启用中文错别字生成器"""

    error_rate: float = 0.01
    """单字替换概率"""

    min_freq: int = 9
    """最小字频阈值"""

    tone_error_rate: float = 0.1
    """声调错误概率"""

    word_replace_rate: float = 0.006
    """整词替换概率"""


@dataclass
class ResponseSplitterConfig(ConfigBase):
    """回复分割器配置类"""

    enable: bool = True
    """是否启用回复分割器"""

    max_length: int = 256
    """回复允许的最大长度"""

    max_sentence_num: int = 3
    """回复允许的最大句子数"""

    enable_kaomoji_protection: bool = False
    """是否启用颜文字保护"""


@dataclass
class TelemetryConfig(ConfigBase):
    """遥测配置类"""

    enable: bool = True
    """是否启用遥测"""


@dataclass
class DebugConfig(ConfigBase):
    """调试配置类"""

    show_prompt: bool = False
    """是否显示prompt"""


@dataclass
class ExperimentalConfig(ConfigBase):
    """实验功能配置类"""

    enable_friend_chat: bool = False
    """是否启用好友聊天"""

    pfc_chatting: bool = False
    """是否启用PFC"""


@dataclass
class MaimMessageConfig(ConfigBase):
    """maim_message配置类"""

    use_custom: bool = False
    """是否使用自定义的maim_message配置"""

    host: str = "127.0.0.1"
    """主机地址"""

    port: int = 8090
    """"端口号"""

    mode: Literal["ws", "tcp"] = "ws"
    """连接模式，支持ws和tcp"""

    use_wss: bool = False
    """是否使用WSS安全连接"""

    cert_file: str = ""
    """SSL证书文件路径，仅在use_wss=True时有效"""

    key_file: str = ""
    """SSL密钥文件路径，仅在use_wss=True时有效"""

    auth_token: list[str] = field(default_factory=lambda: [])
    """认证令牌，用于API验证，为空则不启用验证"""


@dataclass
class LPMMKnowledgeConfig(ConfigBase):
    """LPMM知识库配置类"""

    enable: bool = True
    """是否启用LPMM知识库"""

    rag_synonym_search_top_k: int = 10
    """RAG同义词搜索的Top K数量"""

    rag_synonym_threshold: float = 0.8
    """RAG同义词搜索的相似度阈值"""

    info_extraction_workers: int = 3
    """信息提取工作线程数"""

    qa_relation_search_top_k: int = 10
    """QA关系搜索的Top K数量"""

    qa_relation_threshold: float = 0.75
    """QA关系搜索的相似度阈值"""

    qa_paragraph_search_top_k: int = 1000
    """QA段落搜索的Top K数量"""

    qa_paragraph_node_weight: float = 0.05
    """QA段落节点权重"""

    qa_ent_filter_top_k: int = 10
    """QA实体过滤的Top K数量"""

    qa_ppr_damping: float = 0.8
    """QA PageRank阻尼系数"""

    qa_res_top_k: int = 10
    """QA最终结果的Top K数量"""

    embedding_dimension: int = 1024
    """嵌入向量维度，应该与模型的输出维度一致"""


@dataclass
class ModelConfig(ConfigBase):
    """模型配置类"""

    model_max_output_length: int = 800  # 最大回复长度

    utils: dict[str, Any] = field(default_factory=lambda: {})
    """组件模型配置"""

    utils_small: dict[str, Any] = field(default_factory=lambda: {})
    """组件小模型配置"""

    replyer_1: dict[str, Any] = field(default_factory=lambda: {})
    """normal_chat首要回复模型模型配置"""

    replyer_2: dict[str, Any] = field(default_factory=lambda: {})
    """normal_chat次要回复模型配置"""

    memory: dict[str, Any] = field(default_factory=lambda: {})
    """记忆模型配置"""

    emotion: dict[str, Any] = field(default_factory=lambda: {})
    """情绪模型配置"""

    vlm: dict[str, Any] = field(default_factory=lambda: {})
    """视觉语言模型配置"""

    voice: dict[str, Any] = field(default_factory=lambda: {})
    """语音识别模型配置"""

    tool_use: dict[str, Any] = field(default_factory=lambda: {})
    """专注工具使用模型配置"""

    planner: dict[str, Any] = field(default_factory=lambda: {})
    """规划模型配置"""

    embedding: dict[str, Any] = field(default_factory=lambda: {})
    """嵌入模型配置"""

    lpmm_entity_extract: dict[str, Any] = field(default_factory=lambda: {})
    """LPMM实体提取模型配置"""

    lpmm_rdf_build: dict[str, Any] = field(default_factory=lambda: {})
    """LPMM RDF构建模型配置"""

    lpmm_qa: dict[str, Any] = field(default_factory=lambda: {})
    """LPMM问答模型配置"""
