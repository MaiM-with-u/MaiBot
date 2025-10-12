from typing import List, Tuple, Type, Any

# 导入新插件系统
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo
from src.plugin_system.base.config_types import ConfigField
from src.person_info.person_info import Person
from src.plugin_system.base.base_tool import BaseTool, ToolParamType

# 导入依赖的系统组件
from src.common.logger import get_logger

from src.plugins.built_in.relation.relation import BuildRelationAction

logger = get_logger("relation_actions")


class GetPersonInfoTool(BaseTool):
    """获取用户信息"""

    name = "get_person_info"
    description = "获取某个人的信息，包括印象，特征点，与用户的关系等等"
    parameters = [
        ("person_name", ToolParamType.STRING, "需要获取信息的人的名称", True, None),
        ("info_type", ToolParamType.STRING, "需要获取信息的类型", True, None),
    ]

    available_for_llm = True

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行比较两个数的大小

        Args:
            function_args: 工具参数

        Returns:
            dict: 工具执行结果
        """
        person_name: str = function_args.get("person_name")  # type: ignore
        info_type: str = function_args.get("info_type")  # type: ignore

        person = Person(person_name=person_name)
        if not person:
            return {"content": f"用户 {person_name} 不存在"}
        if not person.is_known:
            return {"content": f"不认识用户 {person_name}"}

        relation_str = await person.build_relationship(info_type=info_type)

        return {"content": relation_str}


@register_plugin
class RelationActionsPlugin(BasePlugin):
    """关系动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name: str = "relation_actions"  # 内部标识符
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
            "enabled": ConfigField(type=bool, default=False, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="1.0.1", description="配置文件版本"),
        },
        "components": {
            "relation_max_memory_num": ConfigField(type=int, default=10, description="关系记忆最大数量"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 根据配置注册组件 ---
        components = []
        components.append((BuildRelationAction.get_action_info(), BuildRelationAction))
        components.append((GetPersonInfoTool.get_tool_info(), GetPersonInfoTool))

        return components
