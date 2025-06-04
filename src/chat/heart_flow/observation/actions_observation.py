# 定义了来自外部世界的信息
# 外部世界可以是某个聊天 不同平台的聊天 也可以是任意媒体
from datetime import datetime
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.action_manager import ActionManager

logger = get_logger("observation")


# 特殊的观察，专门用于观察动作
# 所有观察的基类
class ActionObservation:
    def __init__(self, observe_id):
        self.observe_info = ""
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间
        self.action_manager: ActionManager = None

        self.all_actions = {}
        self.all_using_actions = {}

    def get_observe_info(self):
        return self.observe_info

    def set_action_manager(self, action_manager: ActionManager):
        self.action_manager = action_manager
        self.all_actions = self.action_manager.get_registered_actions()

    async def observe(self):
        action_info_block = ""
        self.all_using_actions = self.action_manager.get_using_actions()
        for action_name, action_info in self.all_using_actions.items():
            action_info_block += f"\n{action_name}: {action_info.get('description', '')}"
        action_info_block += "\n注意，除了上面动作选项之外，你在群聊里不能做其他任何事情，这是你能力的边界\n"

        self.observe_info = action_info_block

    def to_dict(self) -> dict:
        """将观察对象转换为可序列化的字典"""
        return {
            "observe_info": self.observe_info,
            "observe_id": self.observe_id,
            "last_observe_time": self.last_observe_time,
            "all_actions": self.all_actions,
            "all_using_actions": self.all_using_actions,
        }
