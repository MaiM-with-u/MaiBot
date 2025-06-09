#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from src.common.logger_manager import get_logger
from src.chat.actions.base_action import BaseAction, register_action, ActionActivationType, ChatMode
from typing import Tuple, List
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.replyer.default_replyer import DefaultReplyer
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message
import time
import traceback
from src.common.database.database_model import ActionRecords
import re

logger = get_logger("action_taken")


@register_action
class ReplyAction(BaseAction):
    """回复动作处理类

    处理构建和发送消息回复的动作。
    """

    action_name: str = "reply"
    action_description: str = "当你想要参与回复或者聊天"
    action_parameters: dict[str:str] = {
        "reply_to": "如果是明确回复某个人的发言，请在reply_to参数中指定，格式：（用户名:发言内容），如果不是，reply_to的值设为none"
    }
    action_require: list[str] = [
        "你想要闲聊或者随便附和",
        "有人提到你",
        "如果你刚刚进行了回复，不要对同一个话题重复回应"
    ]

    associated_types: list[str] = ["text"]

    enable_plugin = True
    
    # 激活类型设置
    focus_activation_type = ActionActivationType.ALWAYS
    
    # 模式启用设置 - 回复动作只在Focus模式下使用
    mode_enable = ChatMode.FOCUS

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
        success, reply_text = await self._handle_reply(
            reasoning=self.reasoning,
            reply_data=self.action_data,
            cycle_timers=self.cycle_timers,
            thinking_id=self.thinking_id,
        )
        
        await self.store_action_info(
            action_build_into_prompt=False,
            action_prompt_display=f"{reply_text}",
        )
        
        return success, reply_text

    async def _handle_reply(
        self, reasoning: str, reply_data: dict, cycle_timers: dict, thinking_id: str
    ) -> tuple[bool, str]:
        """
        处理统一的回复动作 - 可包含文本和表情，顺序任意

        reply_data格式:
        {
            "text": "你好啊"  # 文本内容列表（可选）
            "target": "锚定消息",  # 锚定消息的文本内容
        }
        """
        logger.info(f"{self.log_prefix} 决定回复: {self.reasoning}")

        # 从聊天观察获取锚定消息
        chatting_observation: ChattingObservation = next(
            obs for obs in self.observations if isinstance(obs, ChattingObservation)
        )
        
        reply_to = reply_data.get("reply_to", "none")
            
        # sender = ""
        target = ""
        if ":" in reply_to or "：" in reply_to:
            # 使用正则表达式匹配中文或英文冒号
            parts = re.split(pattern=r'[:：]', string=reply_to, maxsplit=1)
            if len(parts) == 2:
                # sender = parts[0].strip()
                target = parts[1].strip()
                anchor_message = chatting_observation.search_message_by_text(target)
        else:
            anchor_message = None
        
        if anchor_message:  
            anchor_message.update_chat_stream(self.chat_stream)
        else:
            logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
            anchor_message = await create_empty_anchor_message(
                self.chat_stream.platform, self.chat_stream.group_info, self.chat_stream
            )


        success, reply_set = await self.replyer.deal_reply(
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


    async def store_action_info(self, action_build_into_prompt: bool = False, action_prompt_display: str = "", action_done: bool = True) -> None:
        """存储action执行信息到数据库

        Args:
            action_build_into_prompt: 是否构建到提示中
            action_prompt_display: 动作显示内容
        """
        try:
            chat_stream = self.chat_stream
            if not chat_stream:
                logger.error(f"{self.log_prefix} 无法存储action信息：缺少chat_stream服务")
                return

            action_time = time.time()
            action_id = f"{action_time}_{self.thinking_id}"

            ActionRecords.create(
                action_id=action_id,
                time=action_time,
                action_name=self.__class__.__name__,
                action_data=str(self.action_data),
                action_done=action_done,
                action_build_into_prompt=action_build_into_prompt,
                action_prompt_display=action_prompt_display,
                chat_id=chat_stream.stream_id,
                chat_info_stream_id=chat_stream.stream_id,
                chat_info_platform=chat_stream.platform,
                user_id=chat_stream.user_info.user_id if chat_stream.user_info else "",
                user_nickname=chat_stream.user_info.user_nickname if chat_stream.user_info else "",
                user_cardname=chat_stream.user_info.user_cardname if chat_stream.user_info else ""
            )
            logger.debug(f"{self.log_prefix} 已存储action信息: {action_prompt_display}")
        except Exception as e:
            logger.error(f"{self.log_prefix} 存储action信息时出错: {e}")
            traceback.print_exc()