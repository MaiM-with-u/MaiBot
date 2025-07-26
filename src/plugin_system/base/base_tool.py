from typing import List, Any, Optional, Type
from src.common.logger import get_logger
from rich.traceback import install
from src.plugin_system.base.component_types import ComponentType, ToolInfo
install(extra_lines=3)

logger = get_logger("base_tool")



class BaseTool:
    """所有工具的基类"""

    # 工具名称，子类必须重写
    name = None
    # 工具描述，子类必须重写
    description = None
    # 工具参数定义，子类必须重写
    parameters = None
    # 是否可供LLM使用，默认为False
    available_for_llm = False

    @classmethod
    def get_tool_definition(cls) -> dict[str, Any]:
        """获取工具定义，用于LLM工具调用

        Returns:
            dict: 工具定义字典
        """
        if not cls.name or not cls.description or not cls.parameters:
            raise NotImplementedError(f"工具类 {cls.__name__} 必须定义 name, description 和 parameters 属性")

        return {
            "type": "function",
            "function": {"name": cls.name, "description": cls.description, "parameters": cls.parameters},
        }
    
    @classmethod
    def get_tool_info(cls) -> ToolInfo:
        """获取工具信息"""
        if not cls.name or not cls.description:
            raise NotImplementedError(f"工具类 {cls.__name__} 必须定义 name 和 description 属性")
            
        return ToolInfo(
            name=cls.name,
            tool_description=cls.description,
            available_for_llm=cls.available_for_llm,
            tool_parameters=cls.parameters,
            component_type=ComponentType.TOOL,
        )

    # 工具参数定义，子类必须重写
    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行工具函数

        Args:
            function_args: 工具调用参数

        Returns:
            dict: 工具执行结果
        """
        raise NotImplementedError("子类必须实现execute方法")
