"""
测试事件处理器权重排序功能
"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.plugin_system.core.event_manager import EventManager
from plugins.test_weight_sorting_plugin.plugin import TestWeightSortingPlugin

async def test_weight_sorting():
    """测试权重排序功能"""
    print("开始测试事件处理器权重排序功能...")
    
    # 获取事件管理器实例
    event_manager = EventManager()
    
    # 初始化测试事件
    event_manager.register_event("test_weight_event")
    
    # 创建并初始化测试插件
    plugin = TestWeightSortingPlugin()
    await plugin.initialize()
    
    # 检查事件订阅者是否按权重排序
    event = event_manager.get_event("test_weight_event")
    if event:
        print(f"\n事件 {event.name} 的订阅者列表:")
        for i, subscriber in enumerate(event.subcribers):
            weight = getattr(subscriber, 'weight', 0)
            handler_name = getattr(subscriber, 'handler_name', 'unknown')
            print(f"  {i+1}. {handler_name} (权重: {weight})")
        
        # 验证排序是否正确
        weights = [getattr(h, 'weight', 0) for h in event.subcribers]
        is_sorted = all(weights[i] >= weights[i+1] for i in range(len(weights)-1))
        
        if is_sorted:
            print("\n✅ 权重排序正确：订阅者已按权重从高到低排序")
        else:
            print("\n❌ 权重排序错误：订阅者未按权重排序")
            print(f"当前权重顺序: {weights}")
        
        # 测试事件触发
        print("\n触发测试事件...")
        results = await event_manager.trigger_event("test_weight_event", {"test": "data"})
        
        if results:
            print(f"\n事件触发完成，共执行了 {len(results.results)} 个处理器")
            for result in results.results:
                print(f"  - {result.handler_name}: {'成功' if result.success else '失败'}")
    
    else:
        print("❌ 测试事件不存在")

if __name__ == "__main__":
    asyncio.run(test_weight_sorting())