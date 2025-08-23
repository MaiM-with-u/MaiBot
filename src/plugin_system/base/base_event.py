from typing import List, Dict, Any, Optional
import asyncio

class HandlerResult:
    """事件处理器执行结果
    
    所有事件处理器必须返回此类的实例
    """
    def __init__(self, success: bool, continue_process: bool, message: str = "", handler_name: str = ""):
        self.success = success
        self.continue_process = continue_process
        self.message = message
        self.handler_name = handler_name
    
    def __repr__(self):
        return f"HandlerResult(success={self.success}, continue_process={self.continue_process}, message='{self.message}', handler_name='{self.handler_name}')"

class HandlerResultsCollection:
    """HandlerResult集合，提供便捷的查询方法"""
    
    def __init__(self, results: List[HandlerResult]):
        self.results = results
    
    def all_continue_process(self) -> bool:
        """检查是否所有handler的continue_process都为True"""
        return all(result.continue_process for result in self.results)
    
    def get_all_results(self) -> List[HandlerResult]:
        """获取所有HandlerResult"""
        return self.results
    
    def get_failed_handlers(self) -> List[HandlerResult]:
        """获取执行失败的handler结果"""
        return [result for result in self.results if not result.success]
    
    def get_stopped_handlers(self) -> List[HandlerResult]:
        """获取continue_process为False的handler结果"""
        return [result for result in self.results if not result.continue_process]
    
    def get_handler_result(self, handler_name: str) -> Optional[HandlerResult]:
        """获取指定handler的结果"""
        for result in self.results:
            if result.handler_name == handler_name:
                return result
        return None
    
    def get_success_count(self) -> int:
        """获取成功执行的handler数量"""
        return sum(1 for result in self.results if result.success)
    
    def get_failure_count(self) -> int:
        """获取执行失败的handler数量"""
        return sum(1 for result in self.results if not result.success)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        return {
            "total_handlers": len(self.results),
            "success_count": self.get_success_count(),
            "failure_count": self.get_failure_count(),
            "continue_process": self.all_continue_process(),
            "failed_handlers": [r.handler_name for r in self.get_failed_handlers()],
            "stopped_handlers": [r.handler_name for r in self.get_stopped_handlers()]
        }

class BaseEvent:
    def __init__(self, name: str):
        self.name = name
        self.enabled = True

        from src.plugin_system.base.base_events_handler import BaseEventHandler
        self.subscribers: List["BaseEventHandler"] = [] # 订阅该事件的事件处理器列表

    def __name__(self):
        return self.name
    
    async def activate(self, params: dict) -> HandlerResultsCollection:
        """激活事件，执行所有订阅的处理器
        
        Args:
            params: 传递给处理器的参数
            
        Returns:
            HandlerResultsCollection: 所有处理器的执行结果集合
        """
        if not self.enabled:
            return HandlerResultsCollection([])
        
        # 按权重从高到低排序订阅者
        sorted_subscribers = sorted(self.subscribers, key=lambda h: getattr(h, 'weight', 0), reverse=True)
        
        results = []
        for subscriber in sorted_subscribers:
            try:
                result = await subscriber.execute(params)
                if not isinstance(result, HandlerResult):
                    # 兼容旧版本，将元组转换为HandlerResult
                    success, continue_process, message = result
                    result = HandlerResult(success, continue_process, message or "", getattr(subscriber, 'handler_name', 'unknown_handler'))
                elif not result.handler_name:
                    # 补充handler_name
                    result.handler_name = getattr(subscriber, 'handler_name', 'unknown_handler')
                results.append(result)
            except Exception as e:
                # 处理执行异常
                handler_name = getattr(subscriber, 'handler_name', str(subscriber))
                logger = __import__('src.common.logger', fromlist=['get_logger']).get_logger("base_event")
                logger.error(f"事件处理器 {handler_name} 执行失败: {e}")
                results.append(HandlerResult(False, True, str(e), handler_name))
        
        return HandlerResultsCollection(results)