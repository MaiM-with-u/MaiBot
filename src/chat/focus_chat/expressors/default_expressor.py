import traceback
from typing import List, Optional, Dict, Any, Tuple
from src.chat.message_receive.message import MessageRecv, MessageThinking, MessageSending
from src.chat.message_receive.message import Seg  # Local import needed after move
from src.chat.message_receive.message import UserInfo
from src.chat.message_receive.chat_stream import chat_manager
from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.utils_image import image_path_to_base64  # Local import needed after move
from src.chat.utils.timer_calculator import Timer  # <--- Import Timer
from src.chat.emoji_system.emoji_manager import emoji_manager
from src.chat.focus_chat.heartflow_prompt_builder import prompt_builder
from src.chat.focus_chat.heartFC_sender import HeartFCSender
from src.chat.utils.utils import process_llm_response
from src.chat.utils.info_catcher import info_catcher_manager
from src.manager.mood_manager import mood_manager
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.focus_chat.hfc_utils import parse_thinking_id_to_timestamp
from src.plugins.group_nickname.nickname_manager import nickname_manager

logger = get_logger("expressor")


class DefaultExpressor:
    def __init__(self, chat_id: str):
        self.log_prefix = "expressor"
        self.express_model = LLMRequest(
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=256,
            request_type="response_heartflow",
        )
        self.heart_fc_sender = HeartFCSender()

        self.chat_id = chat_id
        self.chat_stream: Optional[ChatStream] = None
        self.is_group_chat = True
        self.chat_target_info = None

    async def initialize(self):
        self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.chat_id)

    async def _create_thinking_message(self, anchor_message: Optional[MessageRecv], thinking_id: str):
        """创建思考消息 (尝试锚定到 anchor_message)"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self.log_prefix} 无法创建思考消息，缺少有效的锚点消息或聊天流。")
            return None

        chat = anchor_message.chat_stream
        messageinfo = anchor_message.message_info
        thinking_time_point = parse_thinking_id_to_timestamp(thinking_id)
        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=messageinfo.platform,
        )
        # logger.debug(f"创建思考消息：{anchor_message}")
        # logger.debug(f"创建思考消息chat：{chat}")
        # logger.debug(f"创建思考消息bot_user_info：{bot_user_info}")
        # logger.debug(f"创建思考消息messageinfo：{messageinfo}")
        thinking_message = MessageThinking(
            message_id=thinking_id,
            chat_stream=chat,
            bot_user_info=bot_user_info,
            reply=anchor_message,  # 回复的是锚点消息
            thinking_start_time=thinking_time_point,
        )
        logger.debug(f"创建思考消息thinking_message：{thinking_message}")

        await self.heart_fc_sender.register_thinking(thinking_message)

    async def deal_reply(
        self,
        cycle_timers: dict,
        action_data: Dict[str, Any],
        reasoning: str,
        anchor_message: MessageRecv,
        thinking_id: str,
    ) -> tuple[bool, Optional[List[Tuple[str, str]]]]:
        # 创建思考消息
        await self._create_thinking_message(anchor_message, thinking_id)

        reply = None  # 初始化 reply，防止未定义
        try:
            has_sent_something = False

            # 处理文本部分
            text_part = action_data.get("text", [])
            if text_part:
                with Timer("生成回复", cycle_timers):
                    # 可以保留原有的文本处理逻辑或进行适当调整
                    reply = await self.express(
                        in_mind_reply=text_part,
                        anchor_message=anchor_message,
                        thinking_id=thinking_id,
                        reason=reasoning,
                        action_data=action_data,
                    )

                with Timer("选择表情", cycle_timers):
                    emoji_keyword = action_data.get("emojis", [])
                    emoji_base64 = await self._choose_emoji(emoji_keyword)
                    if emoji_base64:
                        reply.append(("emoji", emoji_base64))

                if reply:
                    with Timer("发送消息", cycle_timers):
                        sent_msg_list = await self._send_response_messages(
                            anchor_message=anchor_message,
                            thinking_id=thinking_id,
                            response_set=reply,
                        )
                    has_sent_something = True

                    # 为 trigger_nickname_analysis 准备 bot_reply 参数
                    bot_reply_for_analysis = []
                    if reply: # reply 是 List[Tuple[str, str]]
                        for seg_type, seg_data in reply:
                            if seg_type == "text": # 只取文本类型的数据
                                bot_reply_for_analysis.append(seg_data)

                    await nickname_manager.trigger_nickname_analysis(anchor_message, bot_reply_for_analysis, self.chat_stream)
                else:
                    logger.warning(f"{self.log_prefix} 文本回复生成失败")

            if not has_sent_something:
                logger.warning(f"{self.log_prefix} 回复动作未包含任何有效内容")

            return has_sent_something, sent_msg_list

        except Exception as e:
            logger.error(f"回复失败: {e}")
            return False, None

        # --- 回复器 (Replier) 的定义 --- #

    async def express(
        self,
        in_mind_reply: str,
        reason: str,
        anchor_message: MessageRecv,
        thinking_id: str,
        action_data: Dict[str, Any],
    ) -> Optional[List[str]]:
        """
        回复器 (Replier): 核心逻辑，负责生成回复文本。
        (已整合原 HeartFCGenerator 的功能)
        """
        try:
            # 1. 获取情绪影响因子并调整模型温度
            arousal_multiplier = mood_manager.get_arousal_multiplier()
            current_temp = float(global_config.llm_normal["temp"]) * arousal_multiplier
            self.express_model.params["temperature"] = current_temp  # 动态调整温度

            # 2. 获取信息捕捉器
            info_catcher = info_catcher_manager.get_info_catcher(thinking_id)

            # --- Determine sender_name for private chat ---
            sender_name_for_prompt = "某人"  # Default for group or if info unavailable
            if not self.is_group_chat and self.chat_target_info:
                # Prioritize person_name, then nickname
                sender_name_for_prompt = (
                    self.chat_target_info.get("person_name")
                    or self.chat_target_info.get("user_nickname")
                    or sender_name_for_prompt
                )
            # --- End determining sender_name ---

            target_message = action_data.get("target", "")

            # 3. 构建 Prompt
            with Timer("构建Prompt", {}):  # 内部计时器，可选保留
                prompt = await prompt_builder.build_prompt(
                    build_mode="focus",
                    chat_stream=self.chat_stream,  # Pass the stream object
                    in_mind_reply=in_mind_reply,
                    reason=reason,
                    current_mind_info="",
                    structured_info="",
                    sender_name=sender_name_for_prompt,  # Pass determined name
                    target_message=target_message,
                )

            # 4. 调用 LLM 生成回复
            content = None
            reasoning_content = None
            model_name = "unknown_model"
            if not prompt:
                logger.error(f"{self.log_prefix}[Replier-{thinking_id}] Prompt 构建失败，无法生成回复。")
                return None

            try:
                with Timer("LLM生成", {}):  # 内部计时器，可选保留
                    # logger.info(f"{self.log_prefix}[Replier-{thinking_id}]\nPrompt:\n{prompt}\n")
                    content, reasoning_content, model_name = await self.express_model.generate_response(prompt)

                    logger.info(f"{self.log_prefix}\nPrompt:\n{prompt}\n---------------------------\n")

                    logger.info(f"想要表达：{in_mind_reply}")
                    logger.info(f"理由：{reason}")
                    logger.info(f"生成回复: {content}\n")

                info_catcher.catch_after_llm_generated(
                    prompt=prompt, response=content, reasoning_content=reasoning_content, model_name=model_name
                )

            except Exception as llm_e:
                # 精简报错信息
                logger.error(f"{self.log_prefix}LLM 生成失败: {llm_e}")
                return None  # LLM 调用失败则无法生成回复

            processed_response = process_llm_response(content)

            # 5. 处理 LLM 响应
            if not content:
                logger.warning(f"{self.log_prefix}LLM 生成了空内容。")
                return None
            if not processed_response:
                logger.warning(f"{self.log_prefix}处理后的回复为空。")
                return None

            reply_set = []
            for str in processed_response:
                reply_seg = ("text", str)
                reply_set.append(reply_seg)

            return reply_set

        except Exception as e:
            logger.error(f"{self.log_prefix}回复生成意外失败: {e}")
            traceback.print_exc()
            return None

        # --- 发送器 (Sender) --- #

    async def _send_response_messages(
        self, anchor_message: Optional[MessageRecv], response_set: List[Tuple[str, str]], thinking_id: str
    ) -> Optional[MessageSending]:
        """发送回复消息 (尝试锚定到 anchor_message)，使用 HeartFCSender"""
        chat = self.chat_stream
        chat_id = self.chat_id
        if chat is None:
            logger.error(f"{self.log_prefix} 无法发送回复，chat_stream 为空。")
            return None
        if not anchor_message:
            logger.error(f"{self.log_prefix} 无法发送回复，anchor_message 为空。")
            return None

        stream_name = chat_manager.get_stream_name(chat_id) or chat_id  # 获取流名称用于日志

        # 检查思考过程是否仍在进行，并获取开始时间
        thinking_start_time = await self.heart_fc_sender.get_thinking_start_time(chat_id, thinking_id)

        if thinking_start_time is None:
            logger.error(f"[{stream_name}]思考过程未找到或已结束，无法发送回复。")
            return None

        mark_head = False
        first_bot_msg: Optional[MessageSending] = None
        reply_message_ids = []  # 记录实际发送的消息ID
        
        sent_msg_list = []

        for i, msg_text in enumerate(response_set):
            # 为每个消息片段生成唯一ID
            type = msg_text[0]
            data = msg_text[1]

            part_message_id = f"{thinking_id}_{i}"
            message_segment = Seg(type=type, data=data)

            if type == "emoji":
                is_emoji = True
            else:
                is_emoji = False
            reply_to = not mark_head

            bot_message = await self._build_single_sending_message(
                anchor_message=anchor_message,
                message_id=part_message_id,
                message_segment=message_segment,
                reply_to=reply_to,
                is_emoji=is_emoji,
                thinking_id=thinking_id,
            )

            try:
                if not mark_head:
                    mark_head = True
                    first_bot_msg = bot_message  # 保存第一个成功发送的消息对象
                    typing = False
                else:
                    typing = True

                if type == "emoji":
                    typing = False

                sent_msg = await self.heart_fc_sender.send_message(bot_message, has_thinking=True, typing=typing)

                reply_message_ids.append(part_message_id)  # 记录我们生成的ID
                
                sent_msg_list.append((type, sent_msg))

            except Exception as e:
                logger.error(f"{self.log_prefix}发送回复片段 {i} ({part_message_id}) 时失败: {e}")
                # 这里可以选择是继续发送下一个片段还是中止

        # 在尝试发送完所有片段后，完成原始的 thinking_id 状态
        try:
            await self.heart_fc_sender.complete_thinking(chat_id, thinking_id)
        
        except Exception as e:
            logger.error(f"{self.log_prefix}完成思考状态 {thinking_id} 时出错: {e}")

        return sent_msg_list

    async def _choose_emoji(self, send_emoji: str):
        """
        选择表情，根据send_emoji文本选择表情，返回表情base64
        """
        emoji_base64 = ""
        emoji_raw = await emoji_manager.get_emoji_for_text(send_emoji)
        if emoji_raw:
            emoji_path, _description = emoji_raw
            emoji_base64 = image_path_to_base64(emoji_path)
        return emoji_base64

    async def _build_single_sending_message(
        self,
        anchor_message: MessageRecv,
        message_id: str,
        message_segment: Seg,
        reply_to: bool,
        is_emoji: bool,
        thinking_id: str,
    ) -> MessageSending:
        """构建单个发送消息"""

        thinking_start_time = await self.heart_fc_sender.get_thinking_start_time(self.chat_id, thinking_id)
        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=self.chat_stream.platform,
        )

        bot_message = MessageSending(
            message_id=message_id,  # 使用片段的唯一ID
            chat_stream=self.chat_stream,
            bot_user_info=bot_user_info,
            sender_info=anchor_message.message_info.user_info,
            message_segment=message_segment,
            reply=anchor_message,  # 回复原始锚点
            is_head=reply_to,
            is_emoji=is_emoji,
            thinking_start_time=thinking_start_time,  # 传递原始思考开始时间
        )

        return bot_message
