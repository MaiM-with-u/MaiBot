from typing import List, Tuple, Type, Any

# 导入新插件系统
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo
from src.plugin_system.base.config_types import ConfigField
from src.person_info.person_info import Person
from src.plugin_system.base.base_tool import BaseTool, ToolParamType

# 导入依赖的系统组件
from src.common.logger import get_logger

from src.plugins.built_in.relation.relation import BuildRelationAction
from src.plugin_system.apis import llm_api

logger = get_logger("relation_actions")



class DeepThinkTool(BaseTool):
    """获取用户信息"""

    name = "deep_think"
    description = "深度思考，对某个知识，概念或逻辑问题进行全面且深入的思考，当面临复杂环境或重要问题时，使用此获得更好的解决方案。"
    parameters = [
        ("question", ToolParamType.STRING, "需要思考的问题，越具体越好（从上下文中总结）", True, None),
    ]
    
    available_for_llm = True

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行比较两个数的大小

        Args:
            function_args: 工具参数

        Returns:
            dict: 工具执行结果
        """
        question: str = function_args.get("question")  # type: ignore
        
        print(f"question: {question}")
        
        prompt = f"""
请你思考以下问题，以简洁的一段话回答：
{question}
        """
        
        models = llm_api.get_available_models()
        chat_model_config = models.get("replyer")  # 使用字典访问方式

        success, thinking_result, _, _ = await llm_api.generate_with_model(
            prompt, model_config=chat_model_config, request_type="deep_think"
        )

        logger.info(f"{question}: {thinking_result}")
        
        thinking_result =f"思考结果：{thinking_result}\n**注意** 因为你进行了深度思考，最后的回复内容可以回复的长一些，更加详细一些，不用太简洁。\n"
        
        return {"content": thinking_result}


@register_plugin
class DeepThinkPlugin(BasePlugin):
    """关系动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name: str = "deep_think"  # 内部标识符
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
            "config_version": ConfigField(type=str, default="2.0.0", description="配置文件版本"),
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 根据配置注册组件 ---
        components = []
        components.append((DeepThinkTool.get_tool_info(), DeepThinkTool))

        return components
