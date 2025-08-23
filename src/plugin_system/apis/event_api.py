from typing import Dict, Type
from src.plugin_system.base.component_types import (
    ComponentType,
    EventInfo,
)
from src.plugin_system.base.base_event import BaseEvent
from src.plugin_system.base.base_events_handler import BaseEventHandler

# === 事件管理方法 ===
def register_event(event_name: str) -> None:
    """
    注册一个新的事件。

    Args:
        event_name (str): 事件名称。
    """
    from src.plugin_system.core.component_registry import component_registry
    event_info = EventInfo(name=event_name, component_type=ComponentType.EVENT)
    event_class = BaseEvent(event_name)
    try:
        component_registry.register_component(event_info, event_class)
        return True
    except:
        return False
    
def get_event(event_name: str) -> BaseEvent | None:
    """
    获取指定事件的实例。

    Args:
        event_name (str): 事件名称。

    Returns:
        BaseEvent: 事件实例，如果事件不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry
    return component_registry.get_component_class(event_name, ComponentType.EVENT)

def get_event_subcribers(event_name: str) -> Dict[str, "BaseEventHandler"]:
    """
    获取订阅指定事件的所有事件处理器。

    Args:
        event_name (str): 事件名称。

    Returns:
        dict: 包含所有订阅该事件的事件处理器的字典，键为处理器名称，值为 BaseEventHandler 对象。
    """
    event = get_event(event_name)
    if event is None:
        return {}
    return {handler.handler_name: handler for handler in event.subcribers}

def get_handler(handler_name: str) -> Type["BaseEventHandler"] | None:
    """
    获取指定名称的事件处理器实例。

    Args:
        handler_name (str): 事件处理器名称。

    Returns:
        BaseEventHandler: 事件处理器实例，如果处理器不存在则返回 None。
    """
    from src.plugin_system.core.component_registry import component_registry
    return component_registry.get_component_class(handler_name, ComponentType.EVENT_HANDLER)

def get_current_enabled_events() -> Dict[str, BaseEvent]:
    """
    获取当前所有已启用的事件。

    Returns:
        dict: 包含所有已启用事件的字典，键为事件名称，值为 BaseEvent 对象。
    """
    from src.plugin_system.core.component_registry import component_registry
    return {name: event for name, event in component_registry._event_registry.items() if event.enabled}

def get_current_unenabled_events() -> Dict[str, BaseEvent]:
    """
    获取当前所有已禁用的事件。

    Returns:
        dict: 包含所有已启用事件的字典，键为事件名称，值为 BaseEvent 对象。
    """
    from src.plugin_system.core.component_registry import component_registry
    return {name: event for name, event in component_registry._event_registry.items() if not event.enabled}

def get_all_registered_events() -> Dict[str, BaseEvent]:
    """
    获取所有已注册的事件。

    Returns:
        dict: 包含所有已注册事件的字典，键为事件名称，值为 BaseEvent 对象。
    """
    from src.plugin_system.core.component_registry import component_registry
    return component_registry._event_registry

def init_default_events() -> None:
    """
    初始化默认事件。
    """
    default_events = [
        "on_start",
        "on_stop",
        "on_message",
        "post_llm",
        "after_llm",
        "post_send",
        "after_send"
    ]
    for event_name in default_events:
        register_event(event_name)