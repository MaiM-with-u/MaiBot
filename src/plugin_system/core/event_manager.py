"""
事件管理器 - 实现Event和EventHandler的单例管理
提供统一的事件注册、管理和触发接口
"""

from typing import Dict, Type, List, Optional, Any
from threading import Lock

from src.common.logger import get_logger
from src.plugin_system.base.base_event import BaseEvent, HandlerResultsCollection
from src.plugin_system.base.base_events_handler import BaseEventHandler

logger = get_logger("event_manager")


class EventManager:
    """事件管理器单例类
    
    负责管理所有事件和事件处理器的注册、订阅、触发等操作
    使用单例模式确保全局只有一个事件管理实例
    """
    
    _instance: Optional['EventManager'] = None
    _lock = Lock()
    
    def __new__(cls) -> 'EventManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
            
        self._events: Dict[str, BaseEvent] = {}
        self._event_handlers: Dict[str, Type[BaseEventHandler]] = {}
        self._initialized = True
        logger.info("EventManager 单例初始化完成")
    
    def register_event(self, event_name: str) -> bool:
        """注册一个新的事件
        
        Args:
            event_name (str): 事件名称
            
        Returns:
            bool: 注册成功返回True，已存在返回False
        """
        if event_name in self._events:
            logger.warning(f"事件 {event_name} 已存在，跳过注册")
            return False
            
        event = BaseEvent(event_name)
        self._events[event_name] = event
        logger.info(f"事件 {event_name} 注册成功")
        return True
    
    def get_event(self, event_name: str) -> Optional[BaseEvent]:
        """获取指定事件实例
        
        Args:
            event_name (str): 事件名称
            
        Returns:
            BaseEvent: 事件实例，不存在返回None
        """
        return self._events.get(event_name)
    
    def get_all_events(self) -> Dict[str, BaseEvent]:
        """获取所有已注册的事件
        
        Returns:
            Dict[str, BaseEvent]: 所有事件的字典
        """
        return self._events.copy()
    
    def get_enabled_events(self) -> Dict[str, BaseEvent]:
        """获取所有已启用的事件
        
        Returns:
            Dict[str, BaseEvent]: 已启用事件的字典
        """
        return {name: event for name, event in self._events.items() if event.enabled}
    
    def get_disabled_events(self) -> Dict[str, BaseEvent]:
        """获取所有已禁用的事件
        
        Returns:
            Dict[str, BaseEvent]: 已禁用事件的字典
        """
        return {name: event for name, event in self._events.items() if not event.enabled}
    
    def enable_event(self, event_name: str) -> bool:
        """启用指定事件
        
        Args:
            event_name (str): 事件名称
            
        Returns:
            bool: 成功返回True，事件不存在返回False
        """
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法启用")
            return False
            
        event.enabled = True
        logger.info(f"事件 {event_name} 已启用")
        return True
    
    def disable_event(self, event_name: str) -> bool:
        """禁用指定事件
        
        Args:
            event_name (str): 事件名称
            
        Returns:
            bool: 成功返回True，事件不存在返回False
        """
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法禁用")
            return False
            
        event.enabled = False
        logger.info(f"事件 {event_name} 已禁用")
        return True
    
    def register_event_handler(self, handler_class: Type[BaseEventHandler]) -> bool:
        """注册事件处理器
        
        Args:
            handler_class (Type[BaseEventHandler]): 事件处理器类
            
        Returns:
            bool: 注册成功返回True，已存在返回False
        """
        handler_name = handler_class.handler_name or handler_class.__name__.lower().replace("handler", "")
        
        if handler_name in self._event_handlers:
            logger.warning(f"事件处理器 {handler_name} 已存在，跳过注册")
            return False
            
        self._event_handlers[handler_name] = handler_class()
        if self._event_handlers[handler_name].init_subcribe:
            for event_name in self._event_handlers[handler_name].init_subcribe:
                self._event_handlers[handler_name].subcribe(event_name)

        logger.info(f"事件处理器 {handler_name} 注册成功")
        return True
    
    def get_event_handler(self, handler_name: str) -> Optional[Type[BaseEventHandler]]:
        """获取指定事件处理器类
        
        Args:
            handler_name (str): 处理器名称
            
        Returns:
            Type[BaseEventHandler]: 处理器类，不存在返回None
        """
        return self._event_handlers.get(handler_name)
    
    def get_all_event_handlers(self) -> Dict[str, Type[BaseEventHandler]]:
        """获取所有已注册的事件处理器
        
        Returns:
            Dict[str, Type[BaseEventHandler]]: 所有处理器的字典
        """
        return self._event_handlers.copy()
    
    def subscribe_handler_to_event(self, handler_name: str, event_name: str) -> bool:
        """订阅事件处理器到指定事件
        
        Args:
            handler_name (str): 处理器名称
            event_name (str): 事件名称
            
        Returns:
            bool: 订阅成功返回True
        """
        handler_instance = self.get_event_handler(handler_name)
        if handler_instance is None:
            logger.error(f"事件处理器 {handler_name} 不存在，无法订阅到事件 {event_name}")
            return False
            
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法订阅事件处理器 {handler_name}")
            return False
            
        if handler_instance in event.subcribers:
            logger.warning(f"事件处理器 {handler_name} 已经订阅了事件 {event_name}，跳过重复订阅")
            return True
            
        event.subcribers.append(handler_instance)
        
        # 按权重从高到低排序订阅者
        event.subcribers.sort(key=lambda h: getattr(h, 'weight', 0), reverse=True)
        
        logger.info(f"事件处理器 {handler_name} 成功订阅到事件 {event_name}，当前权重排序完成")
        return True
    
    def unsubscribe_handler_from_event(self, handler_name: str, event_name: str) -> bool:
        """从指定事件取消订阅事件处理器
        
        Args:
            handler_name (str): 处理器名称
            event_name (str): 事件名称
            
        Returns:
            bool: 取消订阅成功返回True
        """
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法取消订阅")
            return False
            
        # 查找并移除处理器实例
        removed = False
        for subscriber in event.subcribers[:]:
            if hasattr(subscriber, 'handler_name') and subscriber.handler_name == handler_name:
                event.subcribers.remove(subscriber)
                removed = True
                break
                
        if removed:
            logger.info(f"事件处理器 {handler_name} 成功从事件 {event_name} 取消订阅")
        else:
            logger.warning(f"事件处理器 {handler_name} 未订阅事件 {event_name}")
            
        return removed
    
    def get_event_subscribers(self, event_name: str) -> Dict[str, BaseEventHandler]:
        """获取订阅指定事件的所有事件处理器
        
        Args:
            event_name (str): 事件名称
            
        Returns:
            Dict[str, BaseEventHandler]: 处理器字典，键为处理器名称，值为处理器实例
        """
        event = self.get_event(event_name)
        if event is None:
            return {}
            
        return {handler.handler_name: handler for handler in event.subcribers}
    
    async def trigger_event(self, event_name: str, params: Dict[str, Any] = None) -> Optional[HandlerResultsCollection]:
        """触发指定事件
        
        Args:
            event_name (str): 事件名称
            params (Dict[str, Any]): 传递给处理器的参数
            
        Returns:
            HandlerResultsCollection: 所有处理器的执行结果，事件不存在返回None
        """
        if params is None:
            params = {}
            
        event = self.get_event(event_name)
        if event is None:
            logger.error(f"事件 {event_name} 不存在，无法触发")
            return None
            
        return await event.activate(params)
    
    def init_default_events(self) -> None:
        """初始化默认事件"""
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
            self.register_event(event_name)
        
        logger.info("默认事件初始化完成")
    
    def clear_all_events(self) -> None:
        """清除所有事件和处理器（主要用于测试）"""
        self._events.clear()
        self._event_handlers.clear()
        logger.info("所有事件和处理器已清除")
    
    def get_event_summary(self) -> Dict[str, Any]:
        """获取事件系统摘要
        
        Returns:
            Dict[str, Any]: 包含事件系统统计信息的字典
        """
        enabled_events = self.get_enabled_events()
        disabled_events = self.get_disabled_events()
        
        return {
            "total_events": len(self._events),
            "enabled_events": len(enabled_events),
            "disabled_events": len(disabled_events),
            "total_handlers": len(self._event_handlers),
            "event_names": list(self._events.keys()),
            "handler_names": list(self._event_handlers.keys())
        }


# 创建全局事件管理器实例
event_manager = EventManager()