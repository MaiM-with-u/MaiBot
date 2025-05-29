#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.base_action import BaseAction, register_action
from typing import Tuple, List
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message

logger = get_logger("action_taken")


@register_action
class ReplyAction(BaseAction):
    """回复动作处理类

    处理构建和发送消息回复的动作。
    """

    action_name: str = "reply"
    action_description: str = "表达想法，可以只包含文本、表情或两者都有"
    action_parameters: dict[str:str] = {
        "text": "你想要表达的内容（可选）",
        "emojis": "描述当前使用表情包的场景，一段话描述（可选）",
        "target": "你想要回复的原始文本内容（非必须，仅文本，不包含发送者)（可选）",
    }
    action_require: list[str] = [
        "有实质性内容需要表达",
        "有人提到你，但你还没有回应他",
        "在合适的时候添加表情（不要总是添加），表情描述要详细，描述当前场景，一段话描述",
        "如果你有明确的,要回复特定某人的某句话，或者你想回复较早的消息，请在target中指定那句话的原始文本",
        "一次只回复一个人，一次只回复一个话题,突出重点",
        "如果是自己发的消息想继续，需自然衔接",
        "避免重复或评价自己的发言,不要和自己聊天",
        "注意：回复尽量简短一些。可以参考贴吧，知乎和微博的回复风格，回复不要浮夸，不要用夸张修辞，平淡一些。不要有额外的符号，尽量简单简短",
    ]

    associated_types: list[str] = ["text", "emoji"]

    default = True

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        observations: List[Observation],
        expressor: DefaultExpressor,
        chat_stream: ChatStream,
        log_prefix: str,
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
            expressor: 表达器
            chat_stream: 聊天流
            log_prefix: 日志前缀
        """
        super().__init__(action_data, reasoning, cycle_timers, thinking_id)
        self.observations = observations
        self.expressor = expressor
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
            "text": "你好啊"  # 文本内容列表（可选）
            "target": "锚定消息",  # 锚定消息的文本内容
            "emojis": "微笑"  # 表情关键词列表（可选）
        }
        """
        logger.info(f"{self.log_prefix} 决定回复: {self.reasoning}")

        # 从聊天观察获取锚定消息
        chatting_observation: ChattingObservation = next(
            obs for obs in self.observations if isinstance(obs, ChattingObservation)
        )
        if reply_data.get("target"):
            anchor_message = chatting_observation.search_message_by_text(reply_data["target"])
        else:
            anchor_message = None

        # 如果没有找到锚点消息，创建一个占位符
        if not anchor_message:
            logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
            anchor_message = await create_empty_anchor_message(
                self.chat_stream.platform, self.chat_stream.group_info, self.chat_stream
            )
        else:
            anchor_message.update_chat_stream(self.chat_stream)

        success, reply_set = await self.expressor.deal_reply(
            cycle_timers=cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            reasoning=reasoning,
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
