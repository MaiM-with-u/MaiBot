from typing import List
from src.plugin_system.base.base_events_handler import BaseEventHandler


class BaseEvent:
    def __init__(self, name: str):
        self.name = name
        self.enabled = True
        self.subcribers: List["BaseEventHandler"] = [] # 订阅该事件的事件处理器列表

    def __name__(self):
        return self.name
    
    async def activate(self, params: dict = {}) -> None:
        for subscriber in self.subcribers:
            return await subscriber.execute(params)