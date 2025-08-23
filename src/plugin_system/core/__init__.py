"""
插件核心管理模块

提供插件的加载、注册和管理功能
"""

from src.plugin_system.core.plugin_manager import plugin_manager
from src.plugin_system.core.component_registry import component_registry
from src.plugin_system.core.event_manager import event_manager
from src.plugin_system.core.global_announcement_manager import global_announcement_manager

__all__ = [
    "plugin_manager",
    "component_registry",
    "event_manager",
    "global_announcement_manager",
]
