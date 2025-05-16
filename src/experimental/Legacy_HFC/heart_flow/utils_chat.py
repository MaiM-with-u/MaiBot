import asyncio
from typing import Optional, Tuple, Dict
from src.common.logger_manager import get_logger
from src.chat.message_receive.chat_stream import chat_manager
from src.chat.person_info.person_info import person_info_manager

logger = get_logger("heartflow_utils")


async def get_chat_type_and_target_info(chat_id: str) -> Tuple[bool, Optional[Dict]]:
    """
    获取聊天类型（是否群聊）和私聊对象信息。

    Args:
        chat_id: 聊天流ID

    Returns:
        Tuple[bool, Optional[Dict]]:
            - bool: 是否为群聊 (True 是群聊, False 是私聊或未知)
            - Optional[Dict]: 如果是私聊，包含对方信息的字典；否则为 None。
            字典包含: platform, user_id, user_nickname, person_id, person_name
    """
    is_group_chat = False  # Default to private/unknown
    chat_target_info = None

    try:
        chat_stream = await asyncio.to_thread(chat_manager.get_stream, chat_id)  # Use to_thread if get_stream is sync
        # If get_stream is already async, just use: chat_stream = await chat_manager.get_stream(chat_id)

        if chat_stream:
            if chat_stream.group_info:
                is_group_chat = True
                chat_target_info = None  # Explicitly None for group chat
            elif chat_stream.user_info:  # It's a private chat
                is_group_chat = False
                user_info = chat_stream.user_info
                platform = chat_stream.platform
                user_id = user_info.user_id

                # Initialize target_info with basic info
                target_info = {
                    "platform": platform,
                    "user_id": user_id,
                    "user_nickname": user_info.user_nickname,
                    "person_id": None,
                    "person_name": None,
                }

                # Try to fetch person info
                try:
                    # Assume get_person_id is sync (as per original code), keep using to_thread
                    person_id = await asyncio.to_thread(person_info_manager.get_person_id, platform, user_id)
                    person_name = None
                    if person_id:
                        # get_value is async, so await it directly
                        person_name = await person_info_manager.get_value(person_id, "person_name")

                    target_info["person_id"] = person_id
                    target_info["person_name"] = person_name
                except Exception as person_e:
                    logger.warning(
                        f"获取 person_id 或 person_name 时出错 for {platform}:{user_id} in utils: {person_e}"
                    )

                chat_target_info = target_info
        else:
            logger.warning(f"无法获取 chat_stream for {chat_id} in utils")
            # Keep defaults: is_group_chat=False, chat_target_info=None

    except Exception as e:
        logger.error(f"获取聊天类型和目标信息时出错 for {chat_id}: {e}", exc_info=True)
        # Keep defaults on error

    return is_group_chat, chat_target_info
