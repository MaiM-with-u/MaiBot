"""
核心动作插件

将系统核心动作（reply、no_reply、emoji）转换为新插件系统格式
这是系统的内置插件，提供基础的聊天交互功能
"""

from typing import List, Tuple, Type

# 导入新插件系统
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo
from src.plugin_system.base.config_types import ConfigField

# 导入依赖的系统组件
from src.common.logger import get_logger

from src.plugins.built_in.emoji_plugin.emoji import EmojiAction

logger = get_logger("core_actions")


@register_plugin
class CoreActionsPlugin(BasePlugin):
    """核心动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name: str = "core_actions"  # 内部标识符
    enable_plugin: bool = True
    dependencies: list[str] = []  # 插件依赖列表
    python_dependencies: list[str] = []  # Python包依赖列表
    config_file_name: str = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "components": "核心组件启用配置",
    }

    # 配置Schema定义
    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="0.6.0", description="配置文件版本"),
        },
        "components": {
            "enable_emoji": ConfigField(type=bool, default=True, description="是否启用发送表情/图片动作"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 根据配置注册组件 ---
        components = []
        if self.get_config("components.enable_emoji", True):
            components.append((EmojiAction.get_action_info(), EmojiAction))

        return components
