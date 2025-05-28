from dataclasses import dataclass, field
from typing import Any, Literal

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

    personality_sides: list[str] = field(default_factory=lambda: [])
    """人格侧写"""


@dataclass
class IdentityConfig(ConfigBase):
    """个体特征配置类"""

    identity_detail: list[str] = field(default_factory=lambda: [])
    """身份特征"""


@dataclass
class RelationshipConfig(ConfigBase):
    """关系配置类"""

    give_name: bool = False
    """是否给其他人取名"""


@dataclass
class ChatConfig(ConfigBase):
    """聊天配置类"""

    chat_mode: str = "normal"
    """聊天模式"""

    auto_focus_threshold: float = 1.0
    """自动切换到专注聊天的阈值，越低越容易进入专注聊天"""

    exit_focus_threshold: float = 1.0
    """自动退出专注聊天的阈值，越低越容易退出专注聊天"""


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

    normal_chat_first_probability: float = 0.3
    """
    发言时选择推理模型的概率（0-1之间）
    选择普通模型的概率为 1 - reasoning_normal_model_probability
    """

    max_context_size: int = 15
    """上下文长度"""

    message_buffer: bool = False
    """消息缓冲器"""

    emoji_chance: float = 0.2
    """发送表情包的基础概率"""

    thinking_timeout: int = 120
    """最长思考时间"""

    willing_mode: str = "classical"
    """意愿模式"""

    talk_frequency: float = 1
    """回复频率阈值"""

    response_willing_amplifier: float = 1.0
    """回复意愿放大系数"""

    response_interested_rate_amplifier: float = 1.0
    """回复兴趣度放大系数"""

    talk_frequency_down_groups: list[str] = field(default_factory=lambda: [])
    """降低回复频率的群组"""

    down_frequency_rate: float = 3.0
    """降低回复频率的群组回复意愿降低系数"""

    emoji_response_penalty: float = 0.0
    """表情包回复惩罚系数"""

    mentioned_bot_inevitable_reply: bool = False
    """提及 bot 必然回复"""

    at_bot_inevitable_reply: bool = False
    """@bot 必然回复"""


@dataclass
class FocusChatConfig(ConfigBase):
    """专注聊天配置类"""

    observation_context_size: int = 12
    """可观察到的最长上下文大小，超过这个值的上下文会被压缩"""

    compressed_length: int = 5
    """心流上下文压缩的最短压缩长度，超过心流观察到的上下文长度，会压缩，最短压缩长度为5"""

    compress_length_limit: int = 5
    """最多压缩份数，超过该数值的压缩上下文会被删除"""

    think_interval: int = 1
    """思考间隔（秒）"""


@dataclass
class FocusChatProcessorConfig(ConfigBase):
    """专注聊天处理器配置类"""

    self_identify_processor: bool = True
    """是否启用自我识别处理器"""

    tool_use_processor: bool = True
    """是否启用工具使用处理器"""

    working_memory_processor: bool = True
    """是否启用工作记忆处理器"""


@dataclass
class ExpressionConfig(ConfigBase):
    """表达配置类"""

    expression_style: str = ""
    """表达风格"""

    learning_interval: int = 300
    """学习间隔（秒）"""

    enable_expression_learning: bool = True
    """是否启用表达学习"""


@dataclass
class EmojiConfig(ConfigBase):
    """表情包配置类"""

    max_reg_num: int = 200
    """表情包最大注册数量"""

    do_replace: bool = True
    """达到最大注册数量时替换旧表情包"""

    check_interval: int = 120
    """表情包检查间隔（分钟）"""

    save_pic: bool = True
    """是否保存图片"""

    save_emoji: bool = True
    """是否保存表情包"""

    cache_emoji: bool = True
    """是否缓存表情包"""

    steal_emoji: bool = True
    """是否偷取表情包，让麦麦可以发送她保存的这些表情包"""

    content_filtration: bool = False
    """是否开启表情包过滤"""

    filtration_prompt: str = "符合公序良俗"
    """表情包过滤要求"""


@dataclass
class MemoryConfig(ConfigBase):
    """记忆配置类"""

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


@dataclass
class MoodConfig(ConfigBase):
    """情绪配置类"""

    mood_update_interval: int = 1
    """情绪更新间隔（秒）"""

    mood_decay_rate: float = 0.95
    """情绪衰减率"""

    mood_intensity_factor: float = 0.7
    """情绪强度因子"""


@dataclass
class KeywordRuleConfig(ConfigBase):
    """关键词规则配置类"""

    enable: bool = True
    """是否启用关键词规则"""

    keywords: list[str] = field(default_factory=lambda: [])
    """关键词列表"""

    regex: list[str] = field(default_factory=lambda: [])
    """正则表达式列表"""

    reaction: str = ""
    """关键词触发的反应"""


@dataclass
class KeywordReactionConfig(ConfigBase):
    """关键词配置类"""

    enable: bool = True
    """是否启用关键词反应"""

    rules: list[KeywordRuleConfig] = field(default_factory=lambda: [])
    """关键词反应规则列表"""


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
class ExperimentalConfig(ConfigBase):
    """实验功能配置类"""

    debug_show_chat_mode: bool = False
    """是否在回复后显示当前聊天模式"""

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
class ModelConfig(ConfigBase):
    """模型配置类"""

    model_max_output_length: int = 800  # 最大回复长度

    utils: dict[str, Any] = field(default_factory=lambda: {})
    """组件模型配置"""

    utils_small: dict[str, Any] = field(default_factory=lambda: {})
    """组件小模型配置"""

    normal_chat_1: dict[str, Any] = field(default_factory=lambda: {})
    """normal_chat首要回复模型模型配置"""

    normal_chat_2: dict[str, Any] = field(default_factory=lambda: {})
    """normal_chat次要回复模型配置"""

    memory_summary: dict[str, Any] = field(default_factory=lambda: {})
    """记忆的概括模型配置"""

    vlm: dict[str, Any] = field(default_factory=lambda: {})
    """视觉语言模型配置"""

    focus_working_memory: dict[str, Any] = field(default_factory=lambda: {})
    """专注工作记忆模型配置"""

    focus_chat_mind: dict[str, Any] = field(default_factory=lambda: {})
    """专注聊天规划模型配置"""

    focus_self_recognize: dict[str, Any] = field(default_factory=lambda: {})
    """专注自我识别模型配置"""

    focus_tool_use: dict[str, Any] = field(default_factory=lambda: {})
    """专注工具使用模型配置"""

    focus_planner: dict[str, Any] = field(default_factory=lambda: {})
    """专注规划模型配置"""

    focus_expressor: dict[str, Any] = field(default_factory=lambda: {})
    """专注表达器模型配置"""

    embedding: dict[str, Any] = field(default_factory=lambda: {})
    """嵌入模型配置"""

    pfc_action_planner: dict[str, Any] = field(default_factory=lambda: {})
    """PFC动作规划模型配置"""

    pfc_chat: dict[str, Any] = field(default_factory=lambda: {})
    """PFC聊天模型配置"""

    pfc_reply_checker: dict[str, Any] = field(default_factory=lambda: {})
    """PFC回复检查模型配置"""
