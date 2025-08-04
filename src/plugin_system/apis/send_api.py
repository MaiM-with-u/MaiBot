"""
发送API模块

专门负责发送各种类型的消息，采用标准Python包设计模式

使用方式：
    from src.plugin_system.apis import send_api

    # 方式1：直接使用stream_id（推荐）
    await send_api.text_to_stream("hello", stream_id)
    await send_api.emoji_to_stream(emoji_base64, stream_id)
    await send_api.custom_to_stream("video", video_data, stream_id)

    # 方式2：使用群聊/私聊指定函数
    await send_api.text_to_group("hello", "123456")
    await send_api.text_to_user("hello", "987654")

    # 方式3：使用通用custom_message函数
    await send_api.custom_message("video", video_data, "123456", True)
"""

import traceback
import time
import difflib
from typing import Optional, Union
from src.common.logger import get_logger

# 导入依赖
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.message_receive.uni_message_sender import HeartFCSender
from src.chat.message_receive.message import MessageSending, MessageRecv
from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat, replace_user_references_async
from src.person_info.person_info import get_person_info_manager
from maim_message import Seg, UserInfo
from src.config.config import global_config

logger = get_logger("send_api")


# =============================================================================
# 内部实现函数（不暴露给外部）
# =============================================================================


async def _send_to_target(
    message_type: str,
    content: Union[str, dict],
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_to: str = "",
    reply_to_platform_id: Optional[str] = None,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定目标发送消息的内部实现

    Args:
        message_type: 消息类型，如"text"、"image"、"emoji"等
        content: 消息内容
        stream_id: 目标流ID
        display_message: 显示消息
        typing: 是否模拟打字等待。
        reply_to: 回复消息，格式为"发送者:消息内容"
        reply_to_platform_id: 回复消息，格式为"平台:用户ID"，如果不提供则自动查找（插件开发者禁用！）
        storage_message: 是否存储消息到数据库
        show_log: 发送是否显示日志

    Returns:
        bool: 是否发送成功
    """
    try:
        if show_log:
            logger.debug(f"[SendAPI] 发送{message_type}消息到 {stream_id}")

        # 查找目标聊天流
        target_stream = get_chat_manager().get_stream(stream_id)
        if not target_stream:
            logger.error(f"[SendAPI] 未找到聊天流: {stream_id}")
            return False

        # 创建发送器
        heart_fc_sender = HeartFCSender()

        # 生成消息ID
        current_time = time.time()
        message_id = f"send_api_{int(current_time * 1000)}"

        # 构建机器人用户信息
        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=target_stream.platform,
        )

        # 创建消息段
        message_segment = Seg(type=message_type, data=content)  # type: ignore

        # 处理回复消息
        anchor_message = None
        if reply_to:
            anchor_message = await _find_reply_message(target_stream, reply_to)
            if anchor_message and anchor_message.message_info.user_info and not reply_to_platform_id:
                reply_to_platform_id = (
                    f"{anchor_message.message_info.platform}:{anchor_message.message_info.user_info.user_id}"
                )

        # 构建发送消息对象
        bot_message = MessageSending(
            message_id=message_id,
            chat_stream=target_stream,
            bot_user_info=bot_user_info,
            sender_info=target_stream.user_info,
            message_segment=message_segment,
            display_message=display_message,
            reply=anchor_message,
            is_head=True,
            is_emoji=(message_type == "emoji"),
            thinking_start_time=current_time,
            reply_to=reply_to_platform_id,
        )

        # 发送消息
        sent_msg = await heart_fc_sender.send_message(
            bot_message,
            typing=typing,
            set_reply=(anchor_message is not None),
            storage_message=storage_message,
            show_log=show_log,
        )

        if sent_msg:
            logger.debug(f"[SendAPI] 成功发送消息到 {stream_id}")
            return True
        else:
            logger.error("[SendAPI] 发送消息失败")
            return False

    except Exception as e:
        logger.error(f"[SendAPI] 发送消息时出错: {e}")
        traceback.print_exc()
        return False


async def _find_reply_message(target_stream, reply_to: str) -> Optional[MessageRecv]:
    # sourcery skip: inline-variable, use-named-expression
    """查找要回复的消息

    Args:
        target_stream: 目标聊天流
        reply_to: 回复格式，如"发送者:消息内容"或"发送者：消息内容"

    Returns:
        Optional[MessageRecv]: 找到的消息，如果没找到则返回None
    """
    try:
        # 解析reply_to参数
        if ":" in reply_to:
            parts = reply_to.split(":", 1)
        elif "：" in reply_to:
            parts = reply_to.split("：", 1)
        else:
            logger.warning(f"[SendAPI] reply_to格式不正确: {reply_to}")
            return None

        if len(parts) != 2:
            logger.warning(f"[SendAPI] reply_to格式不正确: {reply_to}")
            return None

        sender = parts[0].strip()
        text = parts[1].strip()

        # 获取聊天流的最新20条消息
        reverse_talking_message = get_raw_msg_before_timestamp_with_chat(
            target_stream.stream_id,
            time.time(),  # 当前时间之前的消息
            20,  # 最新的20条消息
        )

        # 反转列表，使最新的消息在前面
        reverse_talking_message = list(reversed(reverse_talking_message))

        find_msg = None
        for message in reverse_talking_message:
            user_id = message["user_id"]
            platform = message["chat_info_platform"]
            person_id = get_person_info_manager().get_person_id(platform, user_id)
            person_name = await get_person_info_manager().get_value(person_id, "person_name")
            if person_name == sender:
                translate_text = message["processed_plain_text"]

                # 使用独立函数处理用户引用格式
                translate_text = await replace_user_references_async(translate_text, platform)

                similarity = difflib.SequenceMatcher(None, text, translate_text).ratio()
                if similarity >= 0.9:
                    find_msg = message
                    break

        if not find_msg:
            logger.info("[SendAPI] 未找到匹配的回复消息")
            return None

        # 构建MessageRecv对象
        user_info = {
            "platform": find_msg.get("user_platform", ""),
            "user_id": find_msg.get("user_id", ""),
            "user_nickname": find_msg.get("user_nickname", ""),
            "user_cardname": find_msg.get("user_cardname", ""),
        }

        group_info = {}
        if find_msg.get("chat_info_group_id"):
            group_info = {
                "platform": find_msg.get("chat_info_group_platform", ""),
                "group_id": find_msg.get("chat_info_group_id", ""),
                "group_name": find_msg.get("chat_info_group_name", ""),
            }

        format_info = {"content_format": "", "accept_format": ""}
        template_info = {"template_items": {}}

        message_info = {
            "platform": target_stream.platform,
            "message_id": find_msg.get("message_id"),
            "time": find_msg.get("time"),
            "group_info": group_info,
            "user_info": user_info,
            "additional_config": find_msg.get("additional_config"),
            "format_info": format_info,
            "template_info": template_info,
        }

        message_dict = {
            "message_info": message_info,
            "raw_message": find_msg.get("processed_plain_text"),
            "processed_plain_text": find_msg.get("processed_plain_text"),
        }

        find_rec_msg = MessageRecv(message_dict)
        find_rec_msg.update_chat_stream(target_stream)

        logger.info(f"[SendAPI] 找到匹配的回复消息，发送者: {sender}")
        return find_rec_msg

    except Exception as e:
        logger.error(f"[SendAPI] 查找回复消息时出错: {e}")
        traceback.print_exc()
        return None


# =============================================================================
# 公共API函数 - 预定义类型的发送函数
# =============================================================================


async def text_to_stream(
    text: str,
    stream_id: str,
    typing: bool = False,
    reply_to: str = "",
    reply_to_platform_id: str = "",
    storage_message: bool = True,
) -> bool:
    """向指定流发送文本消息

    Args:
        text: 要发送的文本内容
        stream_id: 聊天流ID
        typing: 是否显示正在输入
        reply_to: 回复消息，格式为"发送者:消息内容"
        reply_to_platform_id: 回复消息，格式为"平台:用户ID"，如果不提供则自动查找（插件开发者禁用！）
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        "text",
        text,
        stream_id,
        "",
        typing,
        reply_to,
        reply_to_platform_id=reply_to_platform_id,
        storage_message=storage_message,
    )


async def emoji_to_stream(emoji_base64: str, stream_id: str, storage_message: bool = True) -> bool:
    """向指定流发送表情包

    Args:
        emoji_base64: 表情包的base64编码
        stream_id: 聊天流ID
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target("emoji", emoji_base64, stream_id, "", typing=False, storage_message=storage_message)


async def image_to_stream(image_base64: str, stream_id: str, storage_message: bool = True) -> bool:
    """向指定流发送图片

    Args:
        image_base64: 图片的base64编码
        stream_id: 聊天流ID
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target("image", image_base64, stream_id, "", typing=False, storage_message=storage_message)


async def command_to_stream(
    command: Union[str, dict], stream_id: str, storage_message: bool = True, display_message: str = ""
) -> bool:
    """向指定流发送命令

    Args:
        command: 命令
        stream_id: 聊天流ID
        storage_message: 是否存储消息到数据库

    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        "command", command, stream_id, display_message, typing=False, storage_message=storage_message
    )


async def custom_to_stream(
    message_type: str,
    content: str | dict,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_to: str = "",
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送自定义类型消息

    Args:
        message_type: 消息类型，如"text"、"image"、"emoji"、"video"、"file"等
        content: 消息内容（通常是base64编码或文本）
        stream_id: 聊天流ID
        display_message: 显示消息
        typing: 是否显示正在输入
        reply_to: 回复消息，格式为"发送者:消息内容"
        storage_message: 是否存储消息到数据库
        show_log: 是否显示日志
    Returns:
        bool: 是否发送成功
    """
    return await _send_to_target(
        message_type=message_type,
        content=content,
        stream_id=stream_id,
        display_message=display_message,
        typing=typing,
        reply_to=reply_to,
        storage_message=storage_message,
        show_log=show_log,
    )
