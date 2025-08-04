from typing import Optional, Type
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ComponentType

from src.common.logger import get_logger

logger = get_logger("tool_api")


def get_tool_instance(tool_name: str) -> Optional[BaseTool]:
    """获取公开工具实例"""
    from src.plugin_system.core import component_registry

    tool_class: Type[BaseTool] = component_registry.get_component_class(tool_name, ComponentType.TOOL)  # type: ignore
    return tool_class() if tool_class else None


def get_llm_available_tool_definitions():
    """获取LLM可用的工具定义列表
    
    Returns:
        List[Tuple[str, Dict[str, Any]]]: 工具定义列表，为[("tool_name", 定义)]
    """
    from src.plugin_system.core import component_registry

    llm_available_tools = component_registry.get_llm_available_tools()
    return [(name, tool_class.get_tool_definition()) for name, tool_class in llm_available_tools.items()]
