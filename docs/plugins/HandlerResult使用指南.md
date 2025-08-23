# HandlerResult 使用指南

## 概述
HandlerResult类和HandlerResultsCollection类已完全实现，事件处理器继续使用Tuple[bool, bool, str]格式返回，但event.activate会自动转换为HandlerResult实例，提供统一的接口和便捷的结果处理方法。

## 新特性

### 1. HandlerResult类
```python
from src.plugin_system.base.base_event import HandlerResult

# 内部自动创建的HandlerResult实例
# 包含字段：
# - success: bool - 是否执行成功
# - continue_process: bool - 是否继续处理后续处理器
# - message: str - 返回消息
# - handler_name: str - 处理器名称
```

### 2. HandlerResultsCollection类
事件激活方法现在返回HandlerResultsCollection，提供以下便捷方法：

```python
from src.plugin_system.base.base_event import HandlerResultsCollection

# 获取事件执行结果
results = await event.activate(params)

# 检查是否所有处理器都允许继续处理
if results.all_continue_process():
    print("所有处理器都允许继续")

# 获取失败的处理器
failed_handlers = results.get_failed_handlers()
for handler in failed_handlers:
    print(f"处理器 {handler.handler_name} 失败: {handler.message}")

# 获取停止处理的处理器
stopped_handlers = results.get_stopped_handlers()
for handler in stopped_handlers:
    print(f"处理器 {handler.handler_name} 阻止继续处理")

# 获取执行摘要
summary = results.get_summary()
print(f"总处理器数: {summary['total_handlers']}")
print(f"成功数: {summary['success_count']}")
print(f"失败数: {summary['failure_count']}")
print(f"是否全部继续: {summary['continue_process']}")
```

## 使用示例

### 1. 创建事件处理器（保持原有格式）
```python
from src.plugin_system.base.base_events_handler import BaseEventHandler
from typing import Tuple, Optional

class MyHandler(BaseEventHandler):
    handler_name = "my_handler"
    handler_description = "我的事件处理器"
    
    async def execute(self, message) -> Tuple[bool, bool, Optional[str]]:
        try:
            # 处理逻辑
            return True, True, "处理成功"
        except Exception as e:
            return False, True, str(e)
```

### 2. 激活事件并处理结果
```python
from src.plugin_system.apis.event_api import get_event

# 获取事件
event = get_event("on_message")

# 激活事件并获取结果（自动转换为HandlerResultsCollection）
results = await event.activate({"message": "Hello"})

# 使用便捷方法处理结果
if not results.all_continue_process():
    stopped = results.get_stopped_handlers()
    print(f"被阻止的处理器: {[h.handler_name for h in stopped]}")

# 检查失败情况
failed = results.get_failed_handlers()
if failed:
    print("失败的处理器:")
    for handler in failed:
        print(f"  - {handler.handler_name}: {handler.message}")
```

## 保持兼容性

事件处理器继续使用原有的Tuple返回格式，无需任何修改：

```python
# 保持原有格式
async def execute(self, message) -> Tuple[bool, bool, Optional[str]]:
    return True, True, "处理成功"
```

## 注意事项

- 事件处理器**无需修改**，继续使用Tuple[bool, bool, str]格式
- 系统会自动将Tuple转换为HandlerResult实例
- HandlerResultsCollection提供了丰富的查询和统计功能
- 异常处理已内置到事件激活流程中
- 完全向后兼容，现有代码无需任何改动