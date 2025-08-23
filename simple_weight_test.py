"""
简单的事件处理器权重排序测试
"""

import asyncio
from typing import Dict, Any, List
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 简化的测试类，避免复杂依赖
class SimpleHandler:
    """简化的事件处理器"""
    def __init__(self, name: str, weight: int):
        self.handler_name = name
        self.weight = weight
    
    async def execute(self, params: Dict[str, Any]):
        print(f"执行处理器: {self.handler_name} (权重: {self.weight})")
        return True, True, f"{self.handler_name} 执行完成"

class SimpleEvent:
    """简化的事件类"""
    def __init__(self, name: str):
        self.name = name
        self.enabled = True
        self.subcribers: List[SimpleHandler] = []
    
    def add_subscriber(self, handler: SimpleHandler):
        """添加订阅者并排序"""
        if handler not in self.subcribers:
            self.subcribers.append(handler)
            # 按权重从高到低排序
            self.subcribers.sort(key=lambda h: h.weight, reverse=True)
            print(f"添加处理器 {handler.handler_name} (权重: {handler.weight})")
    
    async def activate(self, params: Dict[str, Any] = None):
        """激活事件"""
        if params is None:
            params = {}
        
        print(f"\n激活事件: {self.name}")
        print("执行顺序:")
        
        for i, subscriber in enumerate(self.subcribers, 1):
            print(f"  {i}. {subscriber.handler_name} (权重: {subscriber.weight})")
            await subscriber.execute(params)

async def test_weight_sorting():
    """测试权重排序功能"""
    print("=== 事件处理器权重排序测试 ===")
    
    # 创建测试事件
    event = SimpleEvent("test_event")
    
    # 创建不同权重的处理器
    handlers = [
        SimpleHandler("低权重处理器", 10),
        SimpleHandler("高权重处理器", 100),
        SimpleHandler("中等权重处理器", 50),
        SimpleHandler("最高权重处理器", 200),
        SimpleHandler("最低权重处理器", 5),
    ]
    
    # 随机顺序添加处理器
    import random
    random.shuffle(handlers)
    
    print("\n按随机顺序添加处理器:")
    for handler in handlers:
        event.add_subscriber(handler)
    
    print("\n当前订阅者列表:")
    for i, handler in enumerate(event.subcribers, 1):
        print(f"  {i}. {handler.handler_name} (权重: {handler.weight})")
    
    # 验证排序
    weights = [h.weight for h in event.subcribers]
    is_sorted = all(weights[i] >= weights[i+1] for i in range(len(weights)-1))
    
    if is_sorted:
        print("\n[PASS] 权重排序验证通过：处理器已按权重从高到低排序")
    else:
        print("\n[FAIL] 权重排序验证失败")
    
    # 测试事件触发
    await event.activate({"test": "data"})

if __name__ == "__main__":
    asyncio.run(test_weight_sorting())
