from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from maim_message import Seg

from src.llm_models.payload_content.tool_option import ToolParamType as ToolParamType
from src.llm_models.payload_content.tool_option import ToolCall as ToolCall

# 组件类型枚举
class ComponentType(Enum):
    """组件类型枚举"""

    ACTION = "action"  # 动作组件
    COMMAND = "command"  # 命令组件
    TOOL = "tool"  # 工具组件
    SCHEDULER = "scheduler"  # 定时任务组件（预留）
    EVENT_HANDLER = "event_handler"  # 事件处理组件
    EVENT = "event"  # 事件组件

    def __str__(self) -> str:
        return self.value


# 动作激活类型枚举
class ActionActivationType(Enum):
    """动作激活类型枚举"""

    NEVER = "never"  # 从不激活（默认关闭）
    ALWAYS = "always"  # 默认参与到planner
    LLM_JUDGE = "llm_judge"  # LLM判定是否启动该action到planner
    RANDOM = "random"  # 随机启用action到planner
    KEYWORD = "keyword"  # 关键词触发启用action到planner

    def __str__(self):
        return self.value


# 聊天模式枚举
class ChatMode(Enum):
    """聊天模式枚举"""

    FOCUS = "focus"  # Focus聊天模式
    NORMAL = "normal"  # Normal聊天模式
    PRIORITY = "priority"  # 优先级聊天模式
    ALL = "all"  # 所有聊天模式

    def __str__(self):
        return self.value


# 事件类型枚举
class EventType(Enum):
    """
    事件类型枚举类
    """

    ON_START = "on_start"  # 启动事件，用于调用按时任务
    ON_STOP = "on_stop"  # 停止事件，用于调用按时任务
    ON_MESSAGE = "on_message"
    ON_PLAN = "on_plan"
    POST_LLM = "post_llm"
    AFTER_LLM = "after_llm"
    POST_SEND = "post_send"
    AFTER_SEND = "after_send"
    UNKNOWN = "unknown"  # 未知事件类型

    def __str__(self) -> str:
        return self.value


@dataclass
class PythonDependency:
    """Python包依赖信息"""

    package_name: str  # 包名称
    version: str = ""  # 版本要求，例如: ">=1.0.0", "==2.1.3", ""表示任意版本
    optional: bool = False  # 是否为可选依赖
    description: str = ""  # 依赖描述
    install_name: str = ""  # 安装时的包名（如果与import名不同）

    def __post_init__(self):
        if not self.install_name:
            self.install_name = self.package_name

    def get_pip_requirement(self) -> str:
        """获取pip安装格式的依赖字符串"""
        if self.version:
            return f"{self.install_name}{self.version}"
        return self.install_name


@dataclass
class ComponentInfo:
    """组件信息"""

    name: str  # 组件名称
    component_type: ComponentType  # 组件类型
    description: str = ""  # 组件描述
    enabled: bool = True  # 是否启用
    plugin_name: str = ""  # 所属插件名称
    is_built_in: bool = False  # 是否为内置组件
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ActionInfo(ComponentInfo):
    """动作组件信息"""

    action_parameters: Dict[str, str] = field(
        default_factory=dict
    )  # 动作参数与描述，例如 {"param1": "描述1", "param2": "描述2"}
    action_require: List[str] = field(default_factory=list)  # 动作需求说明
    associated_types: List[str] = field(default_factory=list)  # 关联的消息类型
    # 激活类型相关
    focus_activation_type: ActionActivationType = ActionActivationType.ALWAYS #已弃用
    normal_activation_type: ActionActivationType = ActionActivationType.ALWAYS #已弃用
    activation_type: ActionActivationType = ActionActivationType.ALWAYS 
    random_activation_probability: float = 0.0
    llm_judge_prompt: str = ""
    activation_keywords: List[str] = field(default_factory=list)  # 激活关键词列表
    keyword_case_sensitive: bool = False
    # 模式和并行设置
    parallel_action: bool = False

    def __post_init__(self):
        super().__post_init__()
        if self.activation_keywords is None:
            self.activation_keywords = []
        if self.action_parameters is None:
            self.action_parameters = {}
        if self.action_require is None:
            self.action_require = []
        if self.associated_types is None:
            self.associated_types = []
        self.component_type = ComponentType.ACTION


@dataclass
class CommandInfo(ComponentInfo):
    """命令组件信息"""

    command_pattern: str = ""  # 命令匹配模式（正则表达式）

    def __post_init__(self):
        super().__post_init__()
        self.component_type = ComponentType.COMMAND


@dataclass
class ToolInfo(ComponentInfo):
    """工具组件信息"""

    tool_parameters: List[Tuple[str, ToolParamType, str, bool, List[str] | None]] = field(default_factory=list)  # 工具参数定义
    tool_description: str = ""  # 工具描述

    def __post_init__(self):
        super().__post_init__()
        self.component_type = ComponentType.TOOL


@dataclass
class EventHandlerInfo(ComponentInfo):
    """事件处理器组件信息"""

    intercept_message: bool = False  # 是否拦截消息处理（默认不拦截）
    weight: int = 0  # 事件处理器权重，决定执行顺序

    def __post_init__(self):
        super().__post_init__()
        self.component_type = ComponentType.EVENT_HANDLER

@dataclass
class EventInfo(ComponentInfo):
    """事件组件信息"""

    def __post_init__(self):
        super().__post_init__()
        self.component_type = ComponentType.EVENT

# 事件类型枚举
class EventType(Enum):
    """
    事件类型枚举类
    """

    ON_START = "on_start"  # 启动事件，用于调用按时任务
    ON_STOP = "on_stop"  # 停止事件，用于调用按时任务
    ON_MESSAGE = "on_message"
    ON_PLAN = "on_plan"
    POST_LLM = "post_llm"
    AFTER_LLM = "after_llm"
    POST_SEND = "post_send"
    AFTER_SEND = "after_send"
    UNKNOWN = "unknown"  # 未知事件类型

    def __str__(self) -> str:
        return self.value
    
@dataclass
class PluginInfo:
    """插件信息"""

    display_name: str  # 插件显示名称
    name: str  # 插件名称
    description: str  # 插件描述
    version: str = "1.0.0"  # 插件版本
    author: str = ""  # 插件作者
    enabled: bool = True  # 是否启用
    is_built_in: bool = False  # 是否为内置插件
    components: List[ComponentInfo] = field(default_factory=list)  # 包含的组件列表
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他插件
    python_dependencies: List[PythonDependency] = field(default_factory=list)  # Python包依赖
    config_file: str = ""  # 配置文件路径
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据
    # 新增：manifest相关信息
    manifest_data: Dict[str, Any] = field(default_factory=dict)  # manifest文件数据
    license: str = ""  # 插件许可证
    homepage_url: str = ""  # 插件主页
    repository_url: str = ""  # 插件仓库地址
    keywords: List[str] = field(default_factory=list)  # 插件关键词
    categories: List[str] = field(default_factory=list)  # 插件分类
    min_host_version: str = ""  # 最低主机版本要求
    max_host_version: str = ""  # 最高主机版本要求

    def __post_init__(self):
        if self.components is None:
            self.components = []
        if self.dependencies is None:
            self.dependencies = []
        if self.python_dependencies is None:
            self.python_dependencies = []
        if self.metadata is None:
            self.metadata = {}
        if self.manifest_data is None:
            self.manifest_data = {}
        if self.keywords is None:
            self.keywords = []
        if self.categories is None:
            self.categories = []

    def get_missing_packages(self) -> List[PythonDependency]:
        """检查缺失的Python包"""
        missing = []
        for dep in self.python_dependencies:
            try:
                __import__(dep.package_name)
            except ImportError:
                if not dep.optional:
                    missing.append(dep)
        return missing

    def get_pip_requirements(self) -> List[str]:
        """获取所有pip安装格式的依赖"""
        return [dep.get_pip_requirement() for dep in self.python_dependencies]


@dataclass
class MaiMessages:
    """MaiM插件消息"""

    message_segments: List[Seg] = field(default_factory=list)
    """消息段列表，支持多段消息"""

    message_base_info: Dict[str, Any] = field(default_factory=dict)
    """消息基本信息，包含平台，用户信息等数据"""

    plain_text: str = ""
    """纯文本消息内容"""

    raw_message: Optional[str] = None
    """原始消息内容"""

    is_group_message: bool = False
    """是否为群组消息"""

    is_private_message: bool = False
    """是否为私聊消息"""

    stream_id: Optional[str] = None
    """流ID，用于标识消息流"""

    llm_prompt: Optional[str] = None
    """LLM提示词"""

    llm_response_content: Optional[str] = None
    """LLM响应内容"""
    
    llm_response_reasoning: Optional[str] = None
    """LLM响应推理内容"""
    
    llm_response_model: Optional[str] = None
    """LLM响应模型名称"""
    
    llm_response_tool_call: Optional[List[ToolCall]] = None
    """LLM使用的工具调用"""
    
    action_usage: Optional[List[str]] = None
    """使用的Action"""

    additional_data: Dict[Any, Any] = field(default_factory=dict)
    """附加数据，可以存储额外信息"""

    def __post_init__(self):
        if self.message_segments is None:
            self.message_segments = []
