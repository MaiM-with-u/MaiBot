from typing import Optional
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ComponentType

from src.common.logger import get_logger

logger = get_logger("tool_api")

def get_tool_instance(tool_name: str) -> Optional[BaseTool]:
    """获取公开工具实例"""
    from src.plugin_system.core import component_registry

    tool_class = component_registry.get_component_class(tool_name, ComponentType.TOOL)
    if not tool_class:
        return None
        
    return tool_class()

def get_llm_available_tool_definitions():
    from src.plugin_system.core import component_registry
    
    llm_available_tools = component_registry.get_llm_available_tools()
    return [tool_class().get_tool_definition() for tool_class in llm_available_tools.values()]


