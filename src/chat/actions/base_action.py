from abc import ABC, abstractmethod
from typing import Tuple, Dict, Type
from src.common.logger_manager import get_logger

logger = get_logger("base_action")

# 全局动作注册表
_ACTION_REGISTRY: Dict[str, Type["BaseAction"]] = {}
_DEFAULT_ACTIONS: Dict[str, str] = {}

# 动作激活类型枚举
class ActionActivationType:
    ALWAYS = "always"  # 默认参与到planner
    LLM_JUDGE = "llm_judge"  # LLM判定是否启动该action到planner  
    RANDOM = "random"  # 随机启用action到planner
    KEYWORD = "keyword"  # 关键词触发启用action到planner

# 聊天模式枚举
class ChatMode:
    FOCUS = "focus"  # Focus聊天模式
    NORMAL = "normal"  # Normal聊天模式
    ALL = "all"  # 所有聊天模式

def register_action(cls):
    """
    动作注册装饰器

    用法:
        @register_action
        class MyAction(BaseAction):
            action_name = "my_action"
            action_description = "我的动作"
            focus_activation_type = ActionActivationType.ALWAYS
            normal_activation_type = ActionActivationType.ALWAYS
            mode_enable = ChatMode.ALL
            parallel_action = False
            ...
    """
    # 检查类是否有必要的属性
    if not hasattr(cls, "action_name") or not hasattr(cls, "action_description"):
        logger.error(f"动作类 {cls.__name__} 缺少必要的属性: action_name 或 action_description")
        return cls

    action_name = cls.action_name
    action_description = cls.action_description
    is_enabled = getattr(cls, "enable_plugin", True)  # 默认启用插件

    if not action_name or not action_description:
        logger.error(f"动作类 {cls.__name__} 的 action_name 或 action_description 为空")
        return cls

    # 将动作类注册到全局注册表
    _ACTION_REGISTRY[action_name] = cls

    # 如果启用插件，添加到默认动作集
    if is_enabled:
        _DEFAULT_ACTIONS[action_name] = action_description

    logger.info(f"已注册动作: {action_name} -> {cls.__name__}，插件启用: {is_enabled}")
    return cls


class BaseAction(ABC):
    """动作基类接口

    所有具体的动作类都应该继承这个基类，并实现handle_action方法。
    """

    def __init__(self, action_data: dict, reasoning: str, cycle_timers: dict, thinking_id: str):
        """初始化动作

        Args:
            action_name: 动作名称
            action_data: 动作数据
            reasoning: 执行该动作的理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
        """
        # 每个动作必须实现
        self.action_name: str = "base_action"
        self.action_description: str = "基础动作"
        self.action_parameters: dict = {}
        self.action_require: list[str] = []
        
        # 动作激活类型设置
        # Focus模式下的激活类型，默认为always
        self.focus_activation_type: str = ActionActivationType.ALWAYS
        # Normal模式下的激活类型，默认为always  
        self.normal_activation_type: str = ActionActivationType.ALWAYS
        
        # 随机激活的概率(0.0-1.0)，用于RANDOM激活类型
        self.random_activation_probability: float = 0.3
        # LLM判定的提示词，用于LLM_JUDGE激活类型
        self.llm_judge_prompt: str = ""
        # 关键词触发列表，用于KEYWORD激活类型
        self.activation_keywords: list[str] = []
        # 关键词匹配是否区分大小写
        self.keyword_case_sensitive: bool = False

        # 模式启用设置：指定在哪些聊天模式下启用此动作
        # 可选值: "focus"(仅Focus模式), "normal"(仅Normal模式), "all"(所有模式)
        self.mode_enable: str = ChatMode.ALL

        # 并行执行设置：仅在Normal模式下生效，设置为True的动作可以与回复动作并行执行
        # 而不是替代回复动作，适用于图片生成、TTS、禁言等不需要覆盖回复的动作
        self.parallel_action: bool = False

        self.associated_types: list[str] = []

        self.enable_plugin: bool = True  # 是否启用插件，默认启用

        self.action_data = action_data
        self.reasoning = reasoning
        self.cycle_timers = cycle_timers
        self.thinking_id = thinking_id

    @abstractmethod
    async def handle_action(self) -> Tuple[bool, str]:
        """处理动作的抽象方法，需要被子类实现

        Returns:
            Tuple[bool, str]: (是否执行成功, 回复文本)
        """
        pass
