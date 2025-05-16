from abc import ABC, abstractmethod
from typing import Type, TYPE_CHECKING

# 从 action_handlers.py 导入具体的处理器类
from .action_handlers import (  # 调整导入路径
    ActionHandler,
    DirectReplyHandler,
    SendNewMessageHandler,
    SayGoodbyeHandler,
    SendMemesHandler,
    RethinkGoalHandler,
    ListeningHandler,
    EndConversationHandler,
    BlockAndIgnoreHandler,
    WaitHandler,
    UnknownActionHandler,
)

if TYPE_CHECKING:
    from PFC.conversation import Conversation  # 调整导入路径


class AbstractActionFactory(ABC):
    """抽象动作工厂接口。"""

    @abstractmethod
    def create_action_handler(self, action_type: str, conversation: "Conversation") -> ActionHandler:
        """
        根据动作类型创建并返回相应的动作处理器。

        参数:
            action_type (str): 动作的类型字符串。
            conversation (Conversation): 当前对话实例。

        返回:
            ActionHandler: 对应动作类型的处理器实例。
        """
        pass


class StandardActionFactory(AbstractActionFactory):
    """标准的动作工厂实现。"""

    def create_action_handler(self, action_type: str, conversation: "Conversation") -> ActionHandler:
        """
        根据动作类型创建并返回具体的动作处理器实例。
        """
        # 动作类型到处理器类的映射
        handler_map: dict[str, Type[ActionHandler]] = {
            "direct_reply": DirectReplyHandler,
            "send_new_message": SendNewMessageHandler,
            "say_goodbye": SayGoodbyeHandler,
            "send_memes": SendMemesHandler,
            "rethink_goal": RethinkGoalHandler,
            "listening": ListeningHandler,
            "end_conversation": EndConversationHandler,
            "block_and_ignore": BlockAndIgnoreHandler,
            "wait": WaitHandler,
        }
        handler_class = handler_map.get(action_type)  # 获取对应的处理器类
        # 如果找到对应的处理器类
        if handler_class:
            return handler_class(conversation)  # 创建并返回处理器实例
        else:
            # 如果未找到，返回处理未知动作的默认处理器
            return UnknownActionHandler(conversation)
