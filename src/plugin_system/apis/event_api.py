from typing import Dict
from src.plugin_system.core.component_registry import component_registry
from src.plugin_system.base.component_types import (
    ComponentType,
    EventInfo,
)
from src.plugin_system.base.base_event import BaseEvent

# === 事件管理方法 ===
def register_event(event_name: str) -> None:
    """
    注册一个新的事件。

    Args:
        event_name (str): 事件名称。
    """
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
    return component_registry.get_component_class(event_name, ComponentType.EVENT)

def get_current_enabled_events() -> Dict[str, BaseEvent]:
    """
    获取当前所有已启用的事件。

    Returns:
        dict: 包含所有已启用事件的字典，键为事件名称，值为 BaseEvent 对象。
    """
    return {name: event for name, event in component_registry._event_registry.items() if event.enabled}

def get_current_unenabled_events() -> Dict[str, BaseEvent]:
    """
    获取当前所有已禁用的事件。

    Returns:
        dict: 包含所有已启用事件的字典，键为事件名称，值为 BaseEvent 对象。
    """
    return {name: event for name, event in component_registry._event_registry.items() if not event.enabled}

def get_all_registered_events() -> Dict[str, BaseEvent]:
    """
    获取所有已注册的事件。

    Returns:
        dict: 包含所有已注册事件的字典，键为事件名称，值为 BaseEvent 对象。
    """
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