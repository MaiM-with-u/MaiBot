"""
Normal Chat Expressor

为Normal Chat专门设计的表达器，不需要经过LLM风格化处理，
直接发送消息，主要用于插件动作中需要发送消息的场景。
"""

import time
from typing import List, Optional, Tuple, Dict, Any
from src.chat.message_receive.message import MessageRecv, MessageSending, MessageThinking, Seg
from src.chat.message_receive.message import UserInfo
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.message_receive.message_sender import message_manager
from src.config.config import global_config
from src.common.logger_manager import get_logger

logger = get_logger("normal_chat_expressor")


class NormalChatExpressor:
    """Normal Chat专用表达器

    特点：
    1. 不经过LLM风格化，直接发送消息
    2. 支持文本和表情包发送
    3. 为插件动作提供简化的消息发送接口
    4. 保持与focus_chat expressor相似的API，但去掉复杂的风格化流程
    """

    def __init__(self, chat_stream: ChatStream, stream_name: str):
        """初始化Normal Chat表达器

        Args:
            chat_stream: 聊天流对象
            stream_name: 流名称
        """
        self.chat_stream = chat_stream
        self.stream_name = stream_name
        self.log_prefix = f"[{stream_name}]Normal表达器"
        logger.debug(f"{self.log_prefix} 初始化完成")

    async def create_thinking_message(
        self, anchor_message: Optional[MessageRecv], thinking_id: str
    ) -> Optional[MessageThinking]:
        """创建思考消息

        Args:
            anchor_message: 锚点消息
            thinking_id: 思考ID

        Returns:
            MessageThinking: 创建的思考消息，如果失败返回None
        """
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self.log_prefix} 无法创建思考消息，缺少有效的锚点消息或聊天流")
            return None

        messageinfo = anchor_message.message_info
        thinking_time_point = time.time()

        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=messageinfo.platform,
        )

        thinking_message = MessageThinking(
            message_id=thinking_id,
            chat_stream=self.chat_stream,
            bot_user_info=bot_user_info,
            reply=anchor_message,
            thinking_start_time=thinking_time_point,
        )

        await message_manager.add_message(thinking_message)
        logger.debug(f"{self.log_prefix} 创建思考消息: {thinking_id}")
        return thinking_message

    async def send_response_messages(
        self,
        anchor_message: Optional[MessageRecv],
        response_set: List[Tuple[str, str]],
        thinking_id: str = "",
        display_message: str = "",
    ) -> Optional[MessageSending]:
        """发送回复消息

        Args:
            anchor_message: 锚点消息
            response_set: 回复内容集合，格式为 [(type, content), ...]
            thinking_id: 思考ID
            display_message: 显示消息

        Returns:
            MessageSending: 发送的第一条消息，如果失败返回None
        """
        try:
            if not response_set:
                logger.warning(f"{self.log_prefix} 回复内容为空")
                return None

            # 如果没有thinking_id，生成一个
            if not thinking_id:
                thinking_time_point = round(time.time(), 2)
                thinking_id = "mt" + str(thinking_time_point)

            # 创建思考消息
            if anchor_message:
                await self.create_thinking_message(anchor_message, thinking_id)

            # 创建消息集

            first_bot_msg = None
            mark_head = False
            is_emoji = False
            if len(response_set) == 0:
                return None
            message_id = f"{thinking_id}_{len(response_set)}"
            response_type, content = response_set[0]
            if len(response_set) > 1:
                message_segment = Seg(type="seglist", data=[Seg(type=t, data=c) for t, c in response_set])
            else:
                message_segment = Seg(type=response_type, data=content)
                if response_type == "emoji":
                    is_emoji = True

            bot_msg = await self._build_sending_message(
                message_id=message_id,
                message_segment=message_segment,
                thinking_id=thinking_id,
                anchor_message=anchor_message,
                thinking_start_time=time.time(),
                reply_to=mark_head,
                is_emoji=is_emoji,
            )
            logger.debug(f"{self.log_prefix} 添加{response_type}类型消息: {content}")

            # 提交消息集
            if bot_msg:
                await message_manager.add_message(bot_msg)
                logger.info(f"{self.log_prefix} 成功发送 {response_type}类型消息: {content}")
                container = await message_manager.get_container(self.chat_stream.stream_id)  # 使用 self.stream_id
                for msg in container.messages[:]:
                    if isinstance(msg, MessageThinking) and msg.message_info.message_id == thinking_id:
                        container.messages.remove(msg)
                        logger.debug(f"[{self.stream_name}] 已移除未产生回复的思考消息 {thinking_id}")
                        break
                return first_bot_msg
            else:
                logger.warning(f"{self.log_prefix} 没有有效的消息被创建")
                return None

        except Exception as e:
            logger.error(f"{self.log_prefix} 发送消息失败: {e}")
            import traceback

            traceback.print_exc()
            return None

    async def _build_sending_message(
        self,
        message_id: str,
        message_segment: Seg,
        thinking_id: str,
        anchor_message: Optional[MessageRecv],
        thinking_start_time: float,
        reply_to: bool = False,
        is_emoji: bool = False,
    ) -> MessageSending:
        """构建发送消息

        Args:
            message_id: 消息ID
            message_segment: 消息段
            thinking_id: 思考ID
            anchor_message: 锚点消息
            thinking_start_time: 思考开始时间
            reply_to: 是否回复
            is_emoji: 是否为表情包

        Returns:
            MessageSending: 构建的发送消息
        """
        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=anchor_message.message_info.platform if anchor_message else "unknown",
        )

        message_sending = MessageSending(
            message_id=message_id,
            chat_stream=self.chat_stream,
            bot_user_info=bot_user_info,
            message_segment=message_segment,
            sender_info=self.chat_stream.user_info,
            reply=anchor_message if reply_to else None,
            thinking_start_time=thinking_start_time,
            is_emoji=is_emoji,
        )

        return message_sending

    async def deal_reply(
        self,
        cycle_timers: dict,
        action_data: Dict[str, Any],
        reasoning: str,
        anchor_message: MessageRecv,
        thinking_id: str,
    ) -> Tuple[bool, Optional[str]]:
        """处理回复动作 - 兼容focus_chat expressor API

        Args:
            cycle_timers: 周期计时器（normal_chat中不使用）
            action_data: 动作数据，包含text、target、emojis等
            reasoning: 推理说明
            anchor_message: 锚点消息
            thinking_id: 思考ID

        Returns:
            Tuple[bool, Optional[str]]: (是否成功, 回复文本)
        """
        try:
            response_set = []

            # 处理文本内容
            text_content = action_data.get("text", "")
            if text_content:
                response_set.append(("text", text_content))

            # 处理表情包
            emoji_content = action_data.get("emojis", "")
            if emoji_content:
                response_set.append(("emoji", emoji_content))

            if not response_set:
                logger.warning(f"{self.log_prefix} deal_reply: 没有有效的回复内容")
                return False, None

            # 发送消息
            result = await self.send_response_messages(
                anchor_message=anchor_message,
                response_set=response_set,
                thinking_id=thinking_id,
            )

            if result:
                return True, text_content if text_content else "发送成功"
            else:
                return False, None

        except Exception as e:
            logger.error(f"{self.log_prefix} deal_reply执行失败: {e}")
            import traceback

            traceback.print_exc()
            return False, None
