import traceback
from typing import Optional, List, Dict, Any
from src.common.logger_manager import get_logger
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message

# 以下为类型注解需要
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor
from src.chat.focus_chat.replyer.default_replyer import DefaultReplyer
from src.chat.focus_chat.info.obs_info import ObsInfo

logger = get_logger("message_api")

class MessageAPI:
    """消息API模块
    
    提供了发送消息、获取消息历史等功能
    """
    
    async def send_message(self, type: str, data: str, target: Optional[str] = "", display_message: str = "") -> bool:
        """发送消息的简化方法

        Args:
            type: 消息类型，如"text"、"image"等
            data: 消息内容
            target: 目标消息（可选）
            display_message: 显示的消息内容（可选）

        Returns:
            bool: 是否发送成功
        """
        try:
            expressor: DefaultExpressor = self._services.get("expressor")
            chat_stream: ChatStream = self._services.get("chat_stream")

            if not expressor or not chat_stream:
                logger.error(f"{self.log_prefix} 无法发送消息：缺少必要的内部服务")
                return False

            # 获取锚定消息（如果有）
            observations = self._services.get("observations", [])

            if len(observations) > 0:
                chatting_observation: ChattingObservation = next(
                    (obs for obs in observations if isinstance(obs, ChattingObservation)), None
                )

                if chatting_observation:
                    anchor_message = chatting_observation.search_message_by_text(target)
                else:
                    anchor_message = None
            else:
                anchor_message = None

            # 如果没有找到锚点消息，创建一个占位符
            if not anchor_message:
                logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
                anchor_message = await create_empty_anchor_message(
                    chat_stream.platform, chat_stream.group_info, chat_stream
                )
            else:
                anchor_message.update_chat_stream(chat_stream)

            response_set = [
                (type, data),
            ]

            # 调用内部方法发送消息
            success = await expressor.send_response_messages(
                anchor_message=anchor_message,
                response_set=response_set,
                display_message=display_message,
            )

            return success
        except Exception as e:
            logger.error(f"{self.log_prefix} 发送消息时出错: {e}")
            traceback.print_exc()
            return False

    async def send_message_by_expressor(self, text: str, target: Optional[str] = None) -> bool:
        """通过expressor发送文本消息的简化方法

        Args:
            text: 要发送的消息文本
            target: 目标消息（可选）

        Returns:
            bool: 是否发送成功
        """
        expressor: DefaultExpressor = self._services.get("expressor")
        chat_stream: ChatStream = self._services.get("chat_stream")

        if not expressor or not chat_stream:
            logger.error(f"{self.log_prefix} 无法发送消息：缺少必要的内部服务")
            return False

        # 构造简化的动作数据
        reply_data = {"text": text, "target": target or "", "emojis": []}

        # 获取锚定消息（如果有）
        observations = self._services.get("observations", [])

        # 查找 ChattingObservation 实例
        chatting_observation = None
        for obs in observations:
            if isinstance(obs, ChattingObservation):
                chatting_observation = obs
                break

        if not chatting_observation:
            logger.warning(f"{self.log_prefix} 未找到 ChattingObservation 实例，创建占位符")
            anchor_message = await create_empty_anchor_message(
                chat_stream.platform, chat_stream.group_info, chat_stream
            )
        else:
            anchor_message = chatting_observation.search_message_by_text(reply_data["target"])
            if not anchor_message:
                logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
                anchor_message = await create_empty_anchor_message(
                    chat_stream.platform, chat_stream.group_info, chat_stream
                )
            else:
                anchor_message.update_chat_stream(chat_stream)

        # 调用内部方法发送消息
        success, _ = await expressor.deal_reply(
            cycle_timers=self.cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            reasoning=self.reasoning,
            thinking_id=self.thinking_id,
        )

        return success

    async def send_message_by_replyer(self, target: Optional[str] = None, extra_info_block: Optional[str] = None) -> bool:
        """通过replyer发送消息的简化方法

        Args:
            target: 目标消息（可选）
            extra_info_block: 额外信息块（可选）

        Returns:
            bool: 是否发送成功
        """
        replyer: DefaultReplyer = self._services.get("replyer")
        chat_stream: ChatStream = self._services.get("chat_stream")

        if not replyer or not chat_stream:
            logger.error(f"{self.log_prefix} 无法发送消息：缺少必要的内部服务")
            return False

        # 构造简化的动作数据
        reply_data = {"target": target or "", "extra_info_block": extra_info_block}

        # 获取锚定消息（如果有）
        observations = self._services.get("observations", [])

        # 查找 ChattingObservation 实例
        chatting_observation = None
        for obs in observations:
            if isinstance(obs, ChattingObservation):
                chatting_observation = obs
                break

        if not chatting_observation:
            logger.warning(f"{self.log_prefix} 未找到 ChattingObservation 实例，创建占位符")
            anchor_message = await create_empty_anchor_message(
                chat_stream.platform, chat_stream.group_info, chat_stream
            )
        else:
            anchor_message = chatting_observation.search_message_by_text(reply_data["target"])
            if not anchor_message:
                logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
                anchor_message = await create_empty_anchor_message(
                    chat_stream.platform, chat_stream.group_info, chat_stream
                )
            else:
                anchor_message.update_chat_stream(chat_stream)

        # 调用内部方法发送消息
        success, _ = await replyer.deal_reply(
            cycle_timers=self.cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            reasoning=self.reasoning,
            thinking_id=self.thinking_id,
        )

        return success

    def get_chat_type(self) -> str:
        """获取当前聊天类型

        Returns:
            str: 聊天类型 ("group" 或 "private")
        """
        chat_stream: ChatStream = self._services.get("chat_stream")
        if chat_stream and hasattr(chat_stream, "group_info"):
            return "group" if chat_stream.group_info else "private"
        return "unknown"

    def get_recent_messages(self, count: int = 5) -> List[Dict[str, Any]]:
        """获取最近的消息

        Args:
            count: 要获取的消息数量

        Returns:
            List[Dict]: 消息列表，每个消息包含发送者、内容等信息
        """
        messages = []
        observations = self._services.get("observations", [])

        if observations and len(observations) > 0:
            obs = observations[0]
            if hasattr(obs, "get_talking_message"):
                obs: ObsInfo
                raw_messages = obs.get_talking_message()
                # 转换为简化格式
                for msg in raw_messages[-count:]:
                    simple_msg = {
                        "sender": msg.get("sender", "未知"),
                        "content": msg.get("content", ""),
                        "timestamp": msg.get("timestamp", 0),
                    }
                    messages.append(simple_msg)

        return messages 