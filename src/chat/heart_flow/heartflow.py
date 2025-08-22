import traceback
from typing import Any, Optional, Dict

from src.common.logger import get_logger
from src.chat.heart_flow.heartFC_chat import HeartFChatting
logger = get_logger("heartflow")

class Heartflow:
    """主心流协调器，负责初始化并协调聊天"""

    def __init__(self):
        self.heartflow_chat_list: Dict[Any, HeartFChatting] = {}
        
    async def get_or_create_heartflow_chat(self, chat_id: Any) -> Optional[HeartFChatting]:
        """获取或创建一个新的HeartFChatting实例"""
        try:
            if chat_id in self.heartflow_chat_list:
                if chat := self.heartflow_chat_list.get(chat_id):
                    return chat
            else:
                new_chat = HeartFChatting(chat_id = chat_id)
                await new_chat.start()
                self.heartflow_chat_list[chat_id] = new_chat
                return new_chat
        except Exception as e:
            logger.error(f"创建心流聊天 {chat_id} 失败: {e}", exc_info=True)
            traceback.print_exc()
            return None

heartflow = Heartflow()
