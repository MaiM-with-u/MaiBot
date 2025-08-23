import asyncio
import re
import math
import traceback

from typing import Tuple, TYPE_CHECKING

from src.config.config import global_config
from src.chat.memory_system.Hippocampus import hippocampus_manager
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.storage import MessageStorage
from src.chat.heart_flow.heartflow import heartflow
from src.chat.utils.utils import is_mentioned_bot_in_message
from src.chat.utils.timer_calculator import Timer
from src.chat.utils.chat_message_builder import replace_user_references
from src.common.logger import get_logger
from src.mood.mood_manager import mood_manager
from src.person_info.person_info import Person

if TYPE_CHECKING:
    from src.chat.heart_flow.heartFC_chat import HeartFChatting

logger = get_logger("chat")

async def _calculate_interest(message: MessageRecv) -> Tuple[float, list[str]]:
    """计算消息的兴趣度

    Args:
        message: 待处理的消息对象

    Returns:
        Tuple[float, bool, list[str]]: (兴趣度, 是否被提及, 关键词)
    """
    is_mentioned, _ = is_mentioned_bot_in_message(message)
    interested_rate = 0.0

    with Timer("记忆激活"):
        interested_rate, keywords,keywords_lite = await hippocampus_manager.get_activate_from_text(
            message.processed_plain_text,
            max_depth= 4,
            fast_retrieval=global_config.chat.interest_rate_mode == "fast",
        )
        message.key_words = keywords
        message.key_words_lite = keywords_lite
        logger.debug(f"记忆激活率: {interested_rate:.2f}, 关键词: {keywords}")

    text_len = len(message.processed_plain_text)
    # 根据文本长度分布调整兴趣度，采用分段函数实现更精确的兴趣度计算
    # 基于实际分布：0-5字符(26.57%), 6-10字符(27.18%), 11-20字符(22.76%), 21-30字符(10.33%), 31+字符(13.86%)
    
    if text_len == 0:
        base_interest = 0.01  # 空消息最低兴趣度
    elif text_len <= 5:
        # 1-5字符：线性增长 0.01 -> 0.03
        base_interest = 0.01 + (text_len - 1) * (0.03 - 0.01) / 4
    elif text_len <= 10:
        # 6-10字符：线性增长 0.03 -> 0.06
        base_interest = 0.03 + (text_len - 5) * (0.06 - 0.03) / 5
    elif text_len <= 20:
        # 11-20字符：线性增长 0.06 -> 0.12
        base_interest = 0.06 + (text_len - 10) * (0.12 - 0.06) / 10
    elif text_len <= 30:
        # 21-30字符：线性增长 0.12 -> 0.18
        base_interest = 0.12 + (text_len - 20) * (0.18 - 0.12) / 10
    elif text_len <= 50:
        # 31-50字符：线性增长 0.18 -> 0.22
        base_interest = 0.18 + (text_len - 30) * (0.22 - 0.18) / 20
    elif text_len <= 100:
        # 51-100字符：线性增长 0.22 -> 0.26
        base_interest = 0.22 + (text_len - 50) * (0.26 - 0.22) / 50
    else:
        # 100+字符：对数增长 0.26 -> 0.3，增长率递减
        base_interest = 0.26 + (0.3 - 0.26) * (math.log10(text_len - 99) / math.log10(901))  # 1000-99=901
    
    # 确保在范围内
    base_interest = min(max(base_interest, 0.01), 0.3)

    interested_rate += base_interest

    if is_mentioned:
        interest_increase_on_mention = 2
        interested_rate += interest_increase_on_mention
        
        
    message.interest_value = interested_rate
    message.is_mentioned = is_mentioned

    return interested_rate, keywords


class HeartFCMessageReceiver:
    """心流处理器，负责处理接收到的消息并计算兴趣度"""

    def __init__(self):
        """初始化心流处理器，创建消息存储实例"""
        self.storage = MessageStorage()

    async def process_message(self, message: MessageRecv) -> None:
        """处理接收到的原始消息数据

        主要流程:
        1. 消息解析与初始化
        2. 消息缓冲处理
        3. 过滤检查
        4. 兴趣度计算
        5. 关系处理

        Args:
            message_data: 原始消息字符串
        """
        try:
            # 1. 消息解析与初始化
            userinfo = message.message_info.user_info
            chat = message.chat_stream

            # 2. 兴趣度计算与更新
            interested_rate, keywords = await _calculate_interest(message)
            

            await self.storage.store_message(message, chat)

            heartflow_chat: HeartFChatting = await heartflow.get_or_create_heartflow_chat(chat.stream_id)  # type: ignore

            # subheartflow.add_message_to_normal_chat_cache(message, interested_rate, is_mentioned)
            if global_config.mood.enable_mood:  
                chat_mood = mood_manager.get_mood_by_chat_id(heartflow_chat.stream_id)
                asyncio.create_task(chat_mood.update_mood_by_message(message, interested_rate))

            # 3. 日志记录
            mes_name = chat.group_info.group_name if chat.group_info else "私聊"

            # 如果消息中包含图片标识，则将 [picid:...] 替换为 [图片]
            picid_pattern = r"\[picid:([^\]]+)\]"
            processed_plain_text = re.sub(picid_pattern, "[图片]", message.processed_plain_text)
            
            # 应用用户引用格式替换，将回复<aaa:bbb>和@<aaa:bbb>格式转换为可读格式
            processed_plain_text = replace_user_references(
                processed_plain_text,
                message.message_info.platform, # type: ignore
                replace_bot_name=True
            )

            if keywords:
                logger.info(f"[{mes_name}]{userinfo.user_nickname}:{processed_plain_text}[兴趣度：{interested_rate:.2f}][关键词：{keywords}]")  # type: ignore
            else:
                logger.info(f"[{mes_name}]{userinfo.user_nickname}:{processed_plain_text}[兴趣度：{interested_rate:.2f}]")  # type: ignore

            _ = Person.register_person(platform=message.message_info.platform, user_id=message.message_info.user_info.user_id,nickname=userinfo.user_nickname) # type: ignore

        except Exception as e:
            logger.error(f"消息处理失败: {e}")
            print(traceback.format_exc())
