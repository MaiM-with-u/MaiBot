from typing import Optional, Union, Dict
from src.plugin_system.base.component_types import (
    CommandInfo,
    ActionInfo,
    EventHandlerInfo,
    PluginInfo,
    ComponentType,
    ToolInfo,
)


def get_component_instance(
    component_name: str, component_type: ComponentType
) -> Optional[object]:
    """
    获取指定组件的实例。

    Args:
        component_name (str): 组件名称。
        component_type (ComponentType): 组件类型。

    Returns:
        object: 组件实例，如果组件不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry
    return component_registry.get_component_class(component_name, component_type)  # type: ignoreq

# === 插件信息查询 ===
def get_all_plugin_info() -> Dict[str, PluginInfo]:
    """
    获取所有插件的信息。

    Returns:
        dict: 包含所有插件信息的字典，键为插件名称，值为 PluginInfo 对象。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_all_plugins()


def get_plugin_info(plugin_name: str) -> Optional[PluginInfo]:
    """
    获取指定插件的信息。

    Args:
        plugin_name (str): 插件名称。

    Returns:
        PluginInfo: 插件信息对象，如果插件不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_plugin_info(plugin_name)


# === 组件查询方法 ===
def get_component_info(
    component_name: str, component_type: ComponentType
) -> Optional[Union[CommandInfo, ActionInfo, EventHandlerInfo]]:
    """
    获取指定组件的信息。

    Args:
        component_name (str): 组件名称。
        component_type (ComponentType): 组件类型。
    Returns:
        Union[CommandInfo, ActionInfo, EventHandlerInfo]: 组件信息对象，如果组件不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_component_info(component_name, component_type)  # type: ignore


def get_components_info_by_type(
    component_type: ComponentType,
) -> Dict[str, Union[CommandInfo, ActionInfo, EventHandlerInfo]]:
    """
    获取指定类型的所有组件信息。

    Args:
        component_type (ComponentType): 组件类型。

    Returns:
        dict: 包含指定类型组件信息的字典，键为组件名称，值为对应的组件信息对象。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_components_by_type(component_type)  # type: ignore


def get_enabled_components_info_by_type(
    component_type: ComponentType,
) -> Dict[str, Union[CommandInfo, ActionInfo, EventHandlerInfo]]:
    """
    获取指定类型的所有启用的组件信息。

    Args:
        component_type (ComponentType): 组件类型。

    Returns:
        dict: 包含指定类型启用组件信息的字典，键为组件名称，值为对应的组件信息对象。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_enabled_components_by_type(component_type)  # type: ignore


# === Action 查询方法 ===
def get_registered_action_info(action_name: str) -> Optional[ActionInfo]:
    """
    获取指定 Action 的注册信息。

    Args:
        action_name (str): Action 名称。

    Returns:
        ActionInfo: Action 信息对象，如果 Action 不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_registered_action_info(action_name)


def get_registered_command_info(command_name: str) -> Optional[CommandInfo]:
    """
    获取指定 Command 的注册信息。

    Args:
        command_name (str): Command 名称。

    Returns:
        CommandInfo: Command 信息对象，如果 Command 不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_registered_command_info(command_name)


def get_registered_tool_info(tool_name: str) -> Optional[ToolInfo]:
    """
    获取指定 Tool 的注册信息。

    Args:
        tool_name (str): Tool 名称。

    Returns:
        ToolInfo: Tool 信息对象，如果 Tool 不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_registered_tool_info(tool_name)


# === EventHandler 特定查询方法 ===
def get_registered_event_handler_info(
    event_handler_name: str,
) -> Optional[EventHandlerInfo]:
    """
    获取指定 EventHandler 的注册信息。

    Args:
        event_handler_name (str): EventHandler 名称。

    Returns:
        EventHandlerInfo: EventHandler 信息对象，如果 EventHandler 不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.get_registered_event_handler_info(event_handler_name)


# === 组件管理方法 ===
def globally_enable_component(component_name: str, component_type: ComponentType) -> bool:
    """
    全局启用指定组件。

    Args:
        component_name (str): 组件名称。
        component_type (ComponentType): 组件类型。

    Returns:
        bool: 启用成功返回 True，否则返回 False。
    """
    from src.plugin_system.core.component_registry import component_registry

    return component_registry.enable_component(component_name, component_type)


async def globally_disable_component(component_name: str, component_type: ComponentType) -> bool:
    """
    全局禁用指定组件。

    **此函数是异步的，确保在异步环境中调用。**

    Args:
        component_name (str): 组件名称。
        component_type (ComponentType): 组件类型。

    Returns:
        bool: 禁用成功返回 True，否则返回 False。
    """
    from src.plugin_system.core.component_registry import component_registry

    return await component_registry.disable_component(component_name, component_type)


def locally_enable_component(component_name: str, component_type: ComponentType, stream_id: str) -> bool:
    """
    局部启用指定组件。

    Args:
        component_name (str): 组件名称。
        component_type (ComponentType): 组件类型。
        stream_id (str): 消息流 ID。

    Returns:
        bool: 启用成功返回 True，否则返回 False。
    """
    from src.plugin_system.core.global_announcement_manager import global_announcement_manager

    match component_type:
        case ComponentType.ACTION:
            return global_announcement_manager.enable_specific_chat_action(stream_id, component_name)
        case ComponentType.COMMAND:
            return global_announcement_manager.enable_specific_chat_command(stream_id, component_name)
        case ComponentType.TOOL:
            return global_announcement_manager.enable_specific_chat_tool(stream_id, component_name)
        case ComponentType.EVENT_HANDLER:
            return global_announcement_manager.enable_specific_chat_event_handler(stream_id, component_name)
        case _:
            raise ValueError(f"未知 component type: {component_type}")


def locally_disable_component(component_name: str, component_type: ComponentType, stream_id: str) -> bool:
    """
    局部禁用指定组件。

    Args:
        component_name (str): 组件名称。
        component_type (ComponentType): 组件类型。
        stream_id (str): 消息流 ID。

    Returns:
        bool: 禁用成功返回 True，否则返回 False。
    """
    from src.plugin_system.core.global_announcement_manager import global_announcement_manager

    match component_type:
        case ComponentType.ACTION:
            return global_announcement_manager.disable_specific_chat_action(stream_id, component_name)
        case ComponentType.COMMAND:
            return global_announcement_manager.disable_specific_chat_command(stream_id, component_name)
        case ComponentType.TOOL:
            return global_announcement_manager.disable_specific_chat_tool(stream_id, component_name)
        case ComponentType.EVENT_HANDLER:
            return global_announcement_manager.disable_specific_chat_event_handler(stream_id, component_name)
        case _:
            raise ValueError(f"未知 component type: {component_type}")


def get_locally_disabled_components(stream_id: str, component_type: ComponentType) -> list[str]:
    """
    获取指定消息流中禁用的组件列表。

    Args:
        stream_id (str): 消息流 ID。
        component_type (ComponentType): 组件类型。

    Returns:
        list[str]: 禁用的组件名称列表。
    """
    from src.plugin_system.core.global_announcement_manager import global_announcement_manager

    match component_type:
        case ComponentType.ACTION:
            return global_announcement_manager.get_disabled_chat_actions(stream_id)
        case ComponentType.COMMAND:
            return global_announcement_manager.get_disabled_chat_commands(stream_id)
        case ComponentType.TOOL:
            return global_announcement_manager.get_disabled_chat_tools(stream_id)
        case ComponentType.EVENT_HANDLER:
            return global_announcement_manager.get_disabled_chat_event_handlers(stream_id)
        case _:
            raise ValueError(f"未知 component type: {component_type}")
