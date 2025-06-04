#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.base_action import BaseAction, register_action
from typing import Tuple, List
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.replyer.default_replyer import DefaultReplyer
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message

logger = get_logger("action_taken")


@register_action
class EmojiAction(BaseAction):
    """表情动作处理类

    处理构建和发送消息表情的动作。
    """

    action_name: str = "emoji"
    action_description: str = "当你想单独发送一个表情包辅助你的回复表达"
    action_parameters: dict[str:str] = {
        "description": "文字描述你想要发送的表情包内容",
    }
    action_require: list[str] = [
        "你想要发送一个表情",
        "表达情绪时可以选择使用",
        "一般在你回复之后可以选择性使用",
        "重点：不要连续发，不要发太多[表情包]"]

    associated_types: list[str] = ["emoji"]

    default = True

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        observations: List[Observation],
        chat_stream: ChatStream,
        log_prefix: str,
        replyer: DefaultReplyer,
        **kwargs,
    ):
        """初始化回复动作处理器

        Args:
            action_name: 动作名称
            action_data: 动作数据，包含 message, emojis, target 等
            reasoning: 执行该动作的理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            observations: 观察列表
            replyer: 回复器
            chat_stream: 聊天流
            log_prefix: 日志前缀
        """
        super().__init__(action_data, reasoning, cycle_timers, thinking_id)
        self.observations = observations
        self.replyer = replyer
        self.chat_stream = chat_stream
        self.log_prefix = log_prefix

    async def handle_action(self) -> Tuple[bool, str]:
        """
        处理回复动作

        Returns:
            Tuple[bool, str]: (是否执行成功, 回复文本)
        """
        # 注意: 此处可能会使用不同的expressor实现根据任务类型切换不同的回复策略
        return await self._handle_reply(
            reasoning=self.reasoning,
            reply_data=self.action_data,
            cycle_timers=self.cycle_timers,
            thinking_id=self.thinking_id,
        )

    async def _handle_reply(
        self, reasoning: str, reply_data: dict, cycle_timers: dict, thinking_id: str
    ) -> tuple[bool, str]:
        """
        处理统一的回复动作 - 可包含文本和表情，顺序任意

        reply_data格式:
        {
            "description": "描述你想要发送的表情"
        }
        """
        logger.info(f"{self.log_prefix} 决定发送表情")
        # 从聊天观察获取锚定消息
        # chatting_observation: ChattingObservation = next(
        #     obs for obs in self.observations if isinstance(obs, ChattingObservation)
        # )
        # if reply_data.get("target"):
        #     anchor_message = chatting_observation.search_message_by_text(reply_data["target"])
        # else:
        #     anchor_message = None

        # 如果没有找到锚点消息，创建一个占位符
        # if not anchor_message:
        #     logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
        #     anchor_message = await create_empty_anchor_message(
        #         self.chat_stream.platform, self.chat_stream.group_info, self.chat_stream
        #     )
        # else:
        #     anchor_message.update_chat_stream(self.chat_stream)

        logger.info(f"{self.log_prefix} 为了表情包创建占位符")
        anchor_message = await create_empty_anchor_message(
            self.chat_stream.platform, self.chat_stream.group_info, self.chat_stream
        )

        success, reply_set = await self.replyer.deal_emoji(
            cycle_timers=cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            # reasoning=reasoning,
            thinking_id=thinking_id,
        )

        reply_text = ""
        for reply in reply_set:
            type = reply[0]
            data = reply[1]
            if type == "text":
                reply_text += data
            elif type == "emoji":
                reply_text += data

        return success, reply_text
