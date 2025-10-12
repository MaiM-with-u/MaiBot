from typing import List, Tuple, Type

# 导入新插件系统
from src.plugin_system import BasePlugin, ComponentInfo, register_plugin
from src.plugin_system.base.config_types import ConfigField

# 导入依赖的系统组件
from src.common.logger import get_logger

from src.plugins.built_in.memory.build_memory import GetMemoryAction, GetMemoryTool

logger = get_logger("memory_build")


@register_plugin
class MemoryBuildPlugin(BasePlugin):
    """记忆构建插件

    系统内置插件，提供基础的聊天交互功能：
    - GetMemory: 获取记忆

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name: str = "memory_build"  # 内部标识符
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
            "config_version": ConfigField(type=str, default="1.1.1", description="配置文件版本"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 根据配置注册组件 ---
        components = []
        # components.append((GetMemoryAction.get_action_info(), GetMemoryAction))
        components.append((GetMemoryTool.get_tool_info(), GetMemoryTool))

        return components
