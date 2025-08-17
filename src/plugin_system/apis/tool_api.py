from typing import Any, Dict, List, Optional, Type, Union
from datetime import datetime
from src.plugin_system.base.base_tool import BaseTool
from src.plugin_system.base.component_types import ComponentType
from src.common.tool_history import ToolHistoryManager
from src.common.logger import get_logger

logger = get_logger("tool_api")


def get_tool_instance(tool_name: str) -> Optional[BaseTool]:
    """获取公开工具实例"""
    from src.plugin_system.core import component_registry

    # 获取插件配置
    tool_info = component_registry.get_component_info(tool_name, ComponentType.TOOL)
    if tool_info:
        plugin_config = component_registry.get_plugin_config(tool_info.plugin_name)
    else:
        plugin_config = None

    tool_class: Type[BaseTool] = component_registry.get_component_class(tool_name, ComponentType.TOOL)  # type: ignore
    return tool_class(plugin_config) if tool_class else None


def get_llm_available_tool_definitions():
    """获取LLM可用的工具定义列表

    Returns:
        List[Tuple[str, Dict[str, Any]]]: 工具定义列表，为[("tool_name", 定义)]
    """
    from src.plugin_system.core import component_registry

    llm_available_tools = component_registry.get_llm_available_tools()
    return [(name, tool_class.get_tool_definition()) for name, tool_class in llm_available_tools.items()]


def get_tool_history(
    tool_names: Optional[List[str]] = None,
    start_time: Optional[Union[datetime, str]] = None,
    end_time: Optional[Union[datetime, str]] = None,
    chat_id: Optional[str] = None,
    limit: Optional[int] = None,
    status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    获取工具调用历史记录
    
    Args:
        tool_names: 工具名称列表，为空则查询所有工具
        start_time: 开始时间，可以是datetime对象或ISO格式字符串
        end_time: 结束时间，可以是datetime对象或ISO格式字符串
        chat_id: 会话ID，用于筛选特定会话的调用
        limit: 返回记录数量限制
        status: 执行状态筛选("completed"或"error")
        
    Returns:
        List[Dict]: 工具调用记录列表，每条记录包含以下字段：
            - tool_name: 工具名称
            - timestamp: 调用时间
            - arguments: 调用参数
            - result: 调用结果
            - execution_time: 执行时间
            - status: 执行状态
            - chat_id: 会话ID
    """
    history_manager = ToolHistoryManager()
    return history_manager.query_history(
        tool_names=tool_names,
        start_time=start_time,
        end_time=end_time,
        chat_id=chat_id,
        limit=limit,
        status=status
    )


def get_tool_history_text(
    tool_names: Optional[List[str]] = None,
    start_time: Optional[Union[datetime, str]] = None,
    end_time: Optional[Union[datetime, str]] = None,
    chat_id: Optional[str] = None,
    limit: Optional[int] = None,
    status: Optional[str] = None
) -> str:
    """
    获取工具调用历史记录的文本格式
    
    Args:
        tool_names: 工具名称列表，为空则查询所有工具
        start_time: 开始时间，可以是datetime对象或ISO格式字符串
        end_time: 结束时间，可以是datetime对象或ISO格式字符串
        chat_id: 会话ID，用于筛选特定会话的调用
        limit: 返回记录数量限制
        status: 执行状态筛选("completed"或"error")
        
    Returns:
        str: 格式化的工具调用历史记录文本
    """
    history = get_tool_history(
        tool_names=tool_names,
        start_time=start_time,
        end_time=end_time,
        chat_id=chat_id,
        limit=limit,
        status=status
    )
    
    if not history:
        return "没有找到工具调用记录"
        
    text = "工具调用历史记录:\n"
    for record in history:
        # 提取结果中的name和content
        result = record['result']
        if isinstance(result, dict):
            name = result.get('name', record['tool_name'])
            content = result.get('content', str(result))
        else:
            name = record['tool_name']
            content = str(result)
            
        # 格式化内容
        content = content.strip().replace('\n', ' ')
        if len(content) > 200:
            content = content[:200] + "..."
            
        # 格式化时间
        timestamp = datetime.fromisoformat(record['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        
        text += f"[{timestamp}] {name}\n"
        text += f"结果: {content}\n\n"
    
    return text


def clear_tool_history() -> None:
    """
    清除所有工具调用历史记录
    """
    history_manager = ToolHistoryManager()
    history_manager.clear_history()
