# 定义了来自外部世界的信息
# 外部世界可以是某个聊天 不同平台的聊天 也可以是任意媒体
from datetime import datetime
from src.common.logger_manager import get_logger

logger = get_logger("observation")


# 所有观察的基类
class Observation:
    def __init__(self, observe_id):
        self.observe_info = ""
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间

    def to_dict(self) -> dict:
        """将观察对象转换为可序列化的字典"""
        return {
            "observe_info": self.observe_info,
            "observe_id": self.observe_id,
            "last_observe_time": self.last_observe_time,
        }

    async def observe(self):
        pass
