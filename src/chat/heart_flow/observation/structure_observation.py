from datetime import datetime
from src.common.logger_manager import get_logger

# Import the new utility function

logger = get_logger("observation")


# 所有观察的基类
class StructureObservation:
    def __init__(self, observe_id):
        self.observe_info = ""
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间
        self.history_loop = []
        self.structured_info = []

    def to_dict(self) -> dict:
        """将观察对象转换为可序列化的字典"""
        return {
            "observe_info": self.observe_info,
            "observe_id": self.observe_id,
            "last_observe_time": self.last_observe_time,
            "history_loop": self.history_loop,
            "structured_info": self.structured_info,
        }

    def get_observe_info(self):
        return self.structured_info

    def add_structured_info(self, structured_info: dict):
        self.structured_info.append(structured_info)

    async def observe(self):
        observed_structured_infos = []
        for structured_info in self.structured_info:
            if structured_info.get("ttl") > 0:
                structured_info["ttl"] -= 1
                observed_structured_infos.append(structured_info)
                logger.debug(f"观察到结构化信息仍旧在: {structured_info}")

        self.structured_info = observed_structured_infos
