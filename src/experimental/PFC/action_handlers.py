from abc import ABC, abstractmethod
import time
import asyncio
import traceback
import json
import random
from typing import Optional, Set, TYPE_CHECKING, List, Tuple, Dict  # 确保导入 Dict

from src.chat.emoji_system.emoji_manager import emoji_manager
from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.chat.utils.chat_message_builder import build_readable_messages
from .pfc_types import ConversationState
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from src.chat.utils.utils_image import image_path_to_base64
from maim_message import Seg

if TYPE_CHECKING:
    from .conversation import Conversation

logger = get_logger("pfc_action_handlers")


class ActionHandler(ABC):
    """
    处理动作的抽象基类。
    每个具体的动作处理器都应继承此类并实现 execute 方法。
    """

    def __init__(self, conversation: "Conversation"):
        """
        初始化动作处理器。

        Args:
            conversation (Conversation): 当前对话实例。
        """
        self.conversation = conversation
        self.logger = logger

    @abstractmethod
    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """
        执行具体的动作逻辑。

        Args:
            reason (str): 执行此动作的规划原因。
            observation_info (Optional[ObservationInfo]): 当前的观察信息。
            conversation_info (Optional[ConversationInfo]): 当前的对话信息。
            action_start_time (float): 动作开始的时间戳。
            current_action_record (dict): 用于记录此动作执行情况的字典。

        Returns:
            tuple[bool, str, str]: 一个元组，包含:
                - action_successful (bool): 动作是否成功执行。
                - final_status (str): 动作的最终状态 (如 "done", "recall", "error")。
                - final_reason (str): 动作最终状态的原因或描述。
        """
        pass

    async def _send_reply_or_segments(self, segments_data: list[Seg], content_for_log: str) -> bool:
        """
        内部辅助函数，用于将构造好的消息段发送出去。

        Args:
            segments_data (list[Seg]): 包含待发送内容的 Seg 对象列表。
            content_for_log (str): 用于日志记录的消息内容的简短描述。

        Returns:
            bool: 消息是否发送成功。
        """
        if not self.conversation.direct_sender:
            self.logger.error(f"[私聊][{self.conversation.private_name}] DirectMessageSender 未初始化，无法发送。")
            return False
        if not self.conversation.chat_stream:
            self.logger.error(f"[私聊][{self.conversation.private_name}] ChatStream 未初始化，无法发送。")
            return False

        try:
            # 将 Seg 对象列表包装在 type="seglist" 的 Seg 对象中
            final_segments = Seg(type="seglist", data=segments_data)
            # 调用实际的发送方法
            await self.conversation.direct_sender.send_message(
                chat_stream=self.conversation.chat_stream,
                segments=final_segments,
                reply_to_message=None,  # 私聊通常不引用回复
                content=content_for_log,  # 用于发送器内部的日志记录
            )
            # 注意: my_message_count 的增加现在由具体的发送逻辑（文本或表情）处理后决定
            return True
        except Exception as e:
            self.logger.error(f"[私聊][{self.conversation.private_name}] 发送消息时失败: {str(e)}")
            self.logger.error(f"[私聊][{self.conversation.private_name}] {traceback.format_exc()}")
            self.conversation.state = ConversationState.ERROR  # 发送失败则标记错误状态
            return False

    async def _update_bot_message_in_history(
        self,
        send_time: float,
        message_content: str,
        observation_info: ObservationInfo,
        message_id_prefix: str = "bot_sent_",
    ):
        """
        在机器人成功发送消息后，将该消息添加到 ObservationInfo 的聊天历史中。

        Args:
            send_time (float): 消息发送成功的时间戳。
            message_content (str): 发送的消息内容（对于文本是其本身，对于表情是其描述）。
            observation_info (ObservationInfo): 当前的观察信息实例。
            message_id_prefix (str, optional): 生成消息ID时使用的前缀。默认为 "bot_sent_"。
        """
        if not self.conversation.bot_qq_str:
            self.logger.warning(f"[私聊][{self.conversation.private_name}] Bot QQ ID 未知，无法更新机器人消息历史。")
            return

        # 构造机器人发送的消息字典
        bot_message_dict: Dict[str, any] = {
            "message_id": f"{message_id_prefix}{send_time:.3f}",  # 使用更精确的时间戳
            "time": send_time,
            "user_info": {
                "user_id": self.conversation.bot_qq_str,
                "user_nickname": global_config.BOT_NICKNAME,
                "platform": self.conversation.chat_stream.platform
                if self.conversation.chat_stream
                else "unknown_platform",
            },
            "processed_plain_text": message_content,  # 历史记录中的纯文本使用传入的 message_content
            "detailed_plain_text": message_content,  # 详细文本也使用相同内容
        }
        observation_info.chat_history.append(bot_message_dict)
        observation_info.chat_history_count = len(observation_info.chat_history)
        self.logger.debug(
            f"[私聊][{self.conversation.private_name}] {global_config.BOT_NICKNAME}发送的消息 ('{message_content[:30]}...')已添加到 chat_history。当前历史数: {observation_info.chat_history_count}"
        )

        # 限制历史记录长度
        max_history_len = getattr(global_config, "pfc_max_chat_history_for_checker", 50)
        if len(observation_info.chat_history) > max_history_len:
            observation_info.chat_history = observation_info.chat_history[-max_history_len:]
            observation_info.chat_history_count = len(observation_info.chat_history)

        # 更新用于 Prompt 的历史记录字符串
        history_slice_for_str = observation_info.chat_history[-30:]  # 例如最近30条
        try:
            observation_info.chat_history_str = await build_readable_messages(
                history_slice_for_str,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
            )
        except Exception as e_build_hist:
            self.logger.error(f"[私聊][{self.conversation.private_name}] 更新 chat_history_str 时出错: {e_build_hist}")
            observation_info.chat_history_str = "[构建聊天记录出错]"

    async def _update_post_send_states(
        self,
        observation_info: ObservationInfo,
        conversation_info: ConversationInfo,
        action_type: str,  # 例如 "direct_reply", "send_memes"
        event_description_for_emotion: str,
    ):
        """
        在成功发送一条或多条消息（文本/表情）后，处理通用的状态更新。
        这包括更新 IdleChat、清理未处理消息、更新追问状态以及关系/情绪。

        Args:
            observation_info (ObservationInfo): 当前观察信息。
            conversation_info (ConversationInfo): 当前对话信息。
            action_type (str): 执行的动作类型，用于决定追问逻辑。
            event_description_for_emotion (str): 用于情绪更新的事件描述。
        """
        current_event_time = time.time()  # 获取当前时间作为事件发生时间

        # 更新 IdleChat 的最后消息时间
        if self.conversation.idle_chat:
            await self.conversation.idle_chat.update_last_message_time(current_event_time)

        # 清理在本次交互完成（即此函数被调用时）之前的、来自他人的未处理消息
        current_unprocessed_messages = getattr(observation_info, "unprocessed_messages", [])
        message_ids_to_clear: Set[str] = set()
        timestamp_before_current_interaction_completion = current_event_time - 0.001  # 确保是严格之前

        for msg in current_unprocessed_messages:
            msg_time = msg.get("time")
            msg_id = msg.get("message_id")
            sender_id_info = msg.get("user_info", {})
            sender_id = str(sender_id_info.get("user_id")) if sender_id_info else None

            if (
                msg_id
                and msg_time
                and sender_id != self.conversation.bot_qq_str  # 是对方的消息
                and msg_time < timestamp_before_current_interaction_completion  # 在本次交互完成前
            ):
                message_ids_to_clear.add(msg_id)

        if message_ids_to_clear:
            self.logger.debug(
                f"[私聊][{self.conversation.private_name}] 准备清理 {len(message_ids_to_clear)} 条交互完成前(他人)消息: {message_ids_to_clear}"
            )
            await observation_info.clear_processed_messages(message_ids_to_clear)

        # 更新追问状态 (last_successful_reply_action)
        other_new_msg_count_during_planning = getattr(conversation_info, "other_new_messages_during_planning_count", 0)
        if action_type in ["direct_reply", "send_new_message", "send_memes"]:
            if other_new_msg_count_during_planning > 0 and action_type == "direct_reply":
                # 如果是直接回复，且规划期间有新消息，则下次不应追问
                conversation_info.last_successful_reply_action = None
            else:
                # 否则，记录本次成功的回复/表情动作为下次追问的依据
                conversation_info.last_successful_reply_action = action_type

        # 更新关系和情绪状态
        await self._update_relationship_and_emotion(observation_info, conversation_info, event_description_for_emotion)

    async def _update_relationship_and_emotion(
        self, observation_info: ObservationInfo, conversation_info: ConversationInfo, event_description: str
    ):
        """
        辅助方法：调用关系更新器和情绪更新器。

        Args:
            observation_info (ObservationInfo): 当前观察信息。
            conversation_info (ConversationInfo): 当前对话信息。
            event_description (str): 触发更新的事件描述。
        """
        # 更新关系值（增量）
        if self.conversation.relationship_updater and self.conversation.chat_observer:
            await self.conversation.relationship_updater.update_relationship_incremental(
                conversation_info=conversation_info,
                observation_info=observation_info,
                chat_observer_for_history=self.conversation.chat_observer,
            )
        # 更新情绪状态
        if self.conversation.emotion_updater and self.conversation.chat_observer:
            await self.conversation.emotion_updater.update_emotion_based_on_context(
                conversation_info=conversation_info,
                observation_info=observation_info,
                chat_observer_for_history=self.conversation.chat_observer,
                event_description=event_description,
            )

    async def _fetch_and_prepare_emoji_segment(self, emoji_query: str) -> Optional[Tuple[Seg, str, str]]:
        """
        根据表情查询字符串获取表情图片，将其转换为 Base64 编码，
        并准备好发送所需的 Seg 对象和相关描述文本。

        Args:
            emoji_query (str): 用于搜索表情的查询字符串。

        Returns:
            Optional[Tuple[Seg, str, str]]: 如果成功，返回一个元组包含：
                - emoji_segment (Seg): 构造好的用于发送的表情 Seg 对象。
                - full_emoji_description (str): 表情的完整描述。
                - log_content_for_emoji (str): 用于日志记录的表情描述（可能是截断的）。
            如果失败，则返回 None。
        """
        self.logger.info(f"[私聊][{self.conversation.private_name}] 尝试获取表情，查询: '{emoji_query}'")
        try:
            emoji_result = await emoji_manager.get_emoji_for_text(emoji_query)
            if emoji_result:
                emoji_path, full_emoji_description = emoji_result
                self.logger.info(f"获取到表情包: {emoji_path}, 描述: {full_emoji_description}")

                # 将图片路径转换为纯 Base64 字符串
                emoji_b64_content = image_path_to_base64(emoji_path)
                if not emoji_b64_content:
                    self.logger.error(f"无法将图片 {emoji_path} 转换为Base64。")
                    return None

                # 根据用户提供的片段，Seg type="emoji" data 为纯 Base64 字符串
                emoji_segment = Seg(type="emoji", data=emoji_b64_content)
                # 用于发送器日志的截断描述
                log_content_for_emoji = full_emoji_description[-20:] + "..."

                return emoji_segment, full_emoji_description, log_content_for_emoji
            else:
                self.logger.warning(f"未能根据查询 '{emoji_query}' 获取到合适的表情包。")
                return None
        except Exception as e:
            self.logger.error(f"获取或准备表情图片时出错: {e}", exc_info=True)
            return None


class BaseTextReplyHandler(ActionHandler):
    """
    处理基于文本的回复动作的基类，包含生成-检查-重试的循环。
    适用于 DirectReplyHandler 和 SendNewMessageHandler。
    """

    async def _generate_and_check_text_reply_loop(
        self,
        action_type: str,  # "direct_reply" or "send_new_message"
        observation_info: ObservationInfo,
        conversation_info: ConversationInfo,
        max_attempts: int,
    ) -> Tuple[bool, Optional[str], str, bool, bool]:
        """
        管理生成文本回复并检查其适用性的循环。
        对于 send_new_message，它还处理来自 ReplyGenerator 的初始 JSON 决策。

        Args:
            action_type (str): 当前动作类型 ("direct_reply" 或 "send_new_message")。
            observation_info (ObservationInfo): 当前观察信息。
            conversation_info (ConversationInfo): 当前对话信息。
            max_attempts (int): 最大尝试次数。

        Returns:
            Tuple[bool, Optional[str], str, bool, bool]:
                - is_suitable (bool): 是否找到了合适的回复或作出了发送决策。
                - generated_content_to_send (Optional[str]): 检查通过后要发送的文本内容；
                    如果 ReplyGenerator 决定不发送 (仅对 send_new_message)，则为 None。
                - final_check_reason (str): 检查器或生成失败的原因。
                - need_replan (bool): 如果检查器明确要求重新规划。
                - should_send_reply_for_new_message (bool): 特定于 send_new_message，
                    如果 ReplyGenerator 决定发送则为 True，否则为 False。对于 direct_reply，此值恒为 True。
        """
        reply_attempt_count = 0
        is_suitable = False  # 标记内容是否通过检查
        generated_content_to_send: Optional[str] = None  # 最终要发送的文本
        final_check_reason = "未开始检查"  # 最终检查原因
        need_replan = False  # 是否需要重新规划
        # direct_reply 总是尝试发送；send_new_message 的初始值取决于RG
        should_send_reply_for_new_message = True if action_type == "direct_reply" else False

        while reply_attempt_count < max_attempts and not is_suitable and not need_replan:
            reply_attempt_count += 1
            log_prefix = f"[私聊][{self.conversation.private_name}] 尝试生成/检查 '{action_type}' (第 {reply_attempt_count}/{max_attempts} 次)..."
            self.logger.info(log_prefix)

            self.conversation.state = ConversationState.GENERATING  # 设置状态为生成中
            if not self.conversation.reply_generator:
                raise RuntimeError(f"ReplyGenerator 未为 {self.conversation.private_name} 初始化")

            # 调用 ReplyGenerator 生成原始回复
            raw_llm_output = await self.conversation.reply_generator.generate(
                observation_info, conversation_info, action_type=action_type
            )
            self.logger.debug(f"{log_prefix} ReplyGenerator.generate 返回: '{raw_llm_output}'")
            current_content_for_check = raw_llm_output  # 当前待检查的内容

            # 如果是 send_new_message 动作，需要解析 JSON 判断是否发送
            if action_type == "send_new_message":
                parsed_json = None
                try:
                    parsed_json = json.loads(raw_llm_output)
                except json.JSONDecodeError:  # JSON 解析失败
                    self.logger.error(f"{log_prefix} ReplyGenerator 返回的不是有效的JSON: {raw_llm_output}")
                    conversation_info.last_reply_rejection_reason = "回复生成器未返回有效JSON"
                    conversation_info.last_rejected_reply_content = raw_llm_output
                    should_send_reply_for_new_message = False  # 标记不发送
                    is_suitable = True  # 决策已做出（不发送），所以认为是 "suitable" 以跳出循环
                    final_check_reason = "回复生成器JSON解析失败，决定不发送"
                    generated_content_to_send = None  # 明确不发送内容
                    break  # 跳出重试循环

                if parsed_json:  # JSON 解析成功
                    send_decision = parsed_json.get("send", "no").lower()
                    generated_text_from_json = parsed_json.get("txt", "")  # 如果不发送，txt可能是"no"

                    if send_decision == "yes":  # ReplyGenerator 决定发送
                        should_send_reply_for_new_message = True
                        current_content_for_check = generated_text_from_json
                        self.logger.info(
                            f"{log_prefix} ReplyGenerator 决定发送消息。内容初步为: '{current_content_for_check[:100]}...'"
                        )
                    else:  # ReplyGenerator 决定不发送
                        should_send_reply_for_new_message = False
                        is_suitable = True  # 决策已做出（不发送）
                        final_check_reason = "回复生成器决定不发送"
                        generated_content_to_send = None
                        self.logger.info(f"{log_prefix} ReplyGenerator 决定不发送消息。")
                        break  # 跳出重试循环

            # 检查生成的内容是否有效（适用于 direct_reply 或 send_new_message 且决定发送的情况）
            if (
                not current_content_for_check
                or current_content_for_check.startswith("抱歉")
                or current_content_for_check.strip() == ""
                or (
                    action_type == "send_new_message"
                    and current_content_for_check == "no"
                    and should_send_reply_for_new_message
                )
            ):
                warning_msg = f"{log_prefix} 生成内容无效或为错误提示"
                if (
                    action_type == "send_new_message"
                    and current_content_for_check == "no"
                    and should_send_reply_for_new_message
                ):
                    warning_msg += " (ReplyGenerator决定发送但文本为'no')"
                self.logger.warning(warning_msg + "，将进行下一次尝试 (如果适用)。")
                final_check_reason = "生成内容无效"  # 更新检查原因
                conversation_info.last_reply_rejection_reason = final_check_reason
                conversation_info.last_rejected_reply_content = current_content_for_check
                await asyncio.sleep(0.5)  # 暂停后重试
                continue  # 进入下一次循环

            # --- 内容检查 ---
            self.conversation.state = ConversationState.CHECKING  # 设置状态为检查中
            if not self.conversation.reply_checker:
                raise RuntimeError(f"ReplyChecker 未为 {self.conversation.private_name} 初始化")

            # 准备检查器所需参数
            current_goal_str = ""
            if conversation_info.goal_list:
                goal_item = conversation_info.goal_list[-1]
                current_goal_str = goal_item.get("goal", "") if isinstance(goal_item, dict) else str(goal_item)

            chat_history_for_check = getattr(observation_info, "chat_history", [])
            chat_history_text_for_check = getattr(observation_info, "chat_history_str", "")
            current_time_value_for_check = observation_info.current_time_str or "获取时间失败"

            # 调用 ReplyChecker
            if global_config.enable_pfc_reply_checker:
                self.logger.debug(f"{log_prefix} 调用 ReplyChecker 检查 (配置已启用)...")
                is_suitable_check, reason_check, need_replan_check = await self.conversation.reply_checker.check(
                    reply=current_content_for_check,
                    goal=current_goal_str,
                    chat_history=chat_history_for_check,
                    chat_history_text=chat_history_text_for_check,
                    current_time_str=current_time_value_for_check,
                    retry_count=(reply_attempt_count - 1),
                )
                self.logger.info(
                    f"{log_prefix} ReplyChecker 结果: 合适={is_suitable_check}, 原因='{reason_check}', 需重规划={need_replan_check}"
                )
            else:  # ReplyChecker 未启用
                is_suitable_check, reason_check, need_replan_check = True, "ReplyChecker 已通过配置关闭", False
                self.logger.debug(f"{log_prefix} [配置关闭] ReplyChecker 已跳过，默认回复为合适。")

            is_suitable = is_suitable_check  # 更新内容是否合适
            final_check_reason = reason_check  # 更新检查原因
            need_replan = need_replan_check  # 更新是否需要重规划

            if not is_suitable:  # 如果内容不合适
                conversation_info.last_reply_rejection_reason = final_check_reason
                conversation_info.last_rejected_reply_content = current_content_for_check
                if final_check_reason == "机器人尝试发送重复消息" and not need_replan:
                    self.logger.warning(f"{log_prefix} 回复因自身重复被拒绝。将重试。")
                elif not need_replan and reply_attempt_count < max_attempts:  # 如果不需要重规划且还有尝试次数
                    self.logger.warning(f"{log_prefix} 回复不合适: {final_check_reason}。将重试。")
                else:  # 需要重规划或已达到最大尝试次数
                    self.logger.warning(f"{log_prefix} 回复不合适且(需要重规划或已达最大次数): {final_check_reason}")
                    break  # 结束循环
                await asyncio.sleep(0.5)  # 重试前暂停
            else:  # 内容合适
                generated_content_to_send = current_content_for_check  # 设置最终要发送的内容
                conversation_info.last_reply_rejection_reason = None  # 清除上次拒绝原因
                conversation_info.last_rejected_reply_content = None  # 清除上次拒绝内容
                break  # 成功，跳出循环

        # 确保 send_new_message 在 RG 决定不发送时，is_suitable 为 True，generated_content_to_send 为 None
        if action_type == "send_new_message" and not should_send_reply_for_new_message:
            is_suitable = True  # 决策已完成（不发送）
            generated_content_to_send = None  # 确认不发送任何内容

        return (
            is_suitable,
            generated_content_to_send,
            final_check_reason,
            need_replan,
            should_send_reply_for_new_message,
        )

    async def _process_and_send_reply_with_optional_emoji(
        self,
        action_type: str,  # "direct_reply" or "send_new_message"
        observation_info: ObservationInfo,
        conversation_info: ConversationInfo,
        max_reply_attempts: int,
    ) -> Tuple[bool, bool, List[str], Optional[str], bool, str, bool]:
        """
        核心共享方法：处理文本生成/检查，获取表情，并按顺序发送。

        Args:
            action_type (str): "direct_reply" 或 "send_new_message"。
            observation_info (ObservationInfo): 当前观察信息。
            conversation_info (ConversationInfo): 当前对话信息。
            max_reply_attempts (int): 文本生成的最大尝试次数。

        Returns:
            Tuple[bool, bool, List[str], Optional[str], bool, str, bool]:
                - sent_text_successfully (bool): 文本是否成功发送。
                - sent_emoji_successfully (bool): 表情是否成功发送。
                - final_reason_parts (List[str]): 描述发送结果的字符串列表。
                - full_emoji_description_if_sent (Optional[str]): 如果表情发送成功，其完整描述。
                - need_replan_from_text_check (bool): 文本检查是否要求重规划。
                - text_check_failure_reason (str): 文本检查失败的原因（如果适用）。
                - rg_decided_not_to_send_text (bool): ReplyGenerator是否决定不发送文本 (仅send_new_message)。
        """
        sent_text_successfully = False
        sent_emoji_successfully = False
        final_reason_parts: List[str] = []
        full_emoji_description_if_sent: Optional[str] = None

        # 1. 处理文本部分
        (
            is_suitable_text,
            generated_text_content,
            text_check_reason,
            need_replan_text,
            rg_decided_to_send_text,
        ) = await self._generate_and_check_text_reply_loop(
            action_type=action_type,
            observation_info=observation_info,
            conversation_info=conversation_info,
            max_attempts=max_reply_attempts,
        )

        text_to_send: Optional[str] = None
        # 对于 send_new_message，只有当 RG 决定发送且内容合适时才有文本
        if action_type == "send_new_message":
            if rg_decided_to_send_text and is_suitable_text and generated_text_content:
                text_to_send = generated_text_content
        # 对于 direct_reply，只要内容合适就有文本
        elif action_type == "direct_reply":
            if is_suitable_text and generated_text_content:
                text_to_send = generated_text_content

        rg_decided_not_to_send_text = action_type == "send_new_message" and not rg_decided_to_send_text

        # 2. 处理表情部分
        emoji_prepared_info: Optional[Tuple[Seg, str, str]] = None  # (segment, full_description, log_description)
        emoji_query = conversation_info.current_emoji_query
        if emoji_query:
            emoji_prepared_info = await self._fetch_and_prepare_emoji_segment(emoji_query)
            # 清理查询，无论是否成功获取，避免重复使用
            conversation_info.current_emoji_query = None  # 重要：在这里清理

        # 3. 决定发送顺序并发送
        send_order: List[str] = []
        if text_to_send and emoji_prepared_info:  # 文本和表情都有
            send_order = ["text", "emoji"] if random.random() < 0.5 else ["emoji", "text"]
        elif text_to_send:  # 只有文本
            send_order = ["text"]
        elif emoji_prepared_info:  # 只有表情 (可能是 direct_reply 带表情，或 send_new_message 时 RG 不发文本但有表情)
            send_order = ["emoji"]

        for item_type in send_order:
            current_send_time = time.time()  # 每次发送前获取精确时间
            if item_type == "text" and text_to_send:
                self.conversation.generated_reply = text_to_send  # 用于日志和历史记录
                text_segment = Seg(type="text", data=text_to_send)
                if await self._send_reply_or_segments([text_segment], text_to_send):
                    sent_text_successfully = True
                    await self._update_bot_message_in_history(current_send_time, text_to_send, observation_info)
                    if self.conversation.conversation_info:
                        self.conversation.conversation_info.current_instance_message_count += 1
                        self.conversation.conversation_info.my_message_count += 1  # 文本发送成功，增加计数
                    final_reason_parts.append(f"成功发送文本 ('{text_to_send[:20]}...')")
                else:
                    final_reason_parts.append("发送文本失败")
                    # 如果文本发送失败，通常不应继续发送表情，除非有特殊需求
                    break
            elif item_type == "emoji" and emoji_prepared_info:
                emoji_segment, full_emoji_desc, log_emoji_desc = emoji_prepared_info
                if await self._send_reply_or_segments([emoji_segment], log_emoji_desc):
                    sent_emoji_successfully = True
                    full_emoji_description_if_sent = full_emoji_desc
                    await self._update_bot_message_in_history(
                        current_send_time, full_emoji_desc, observation_info, "bot_emoji_"
                    )
                    if self.conversation.conversation_info:
                        self.conversation.conversation_info.current_instance_message_count += 1
                        self.conversation.conversation_info.my_message_count += 1  # 表情发送成功，增加计数
                    final_reason_parts.append(f"成功发送表情 ({full_emoji_desc})")
                else:
                    final_reason_parts.append("发送表情失败")
                    # 如果表情发送失败，但文本已成功，也应记录
                    if not text_to_send:  # 如果只有表情且表情失败
                        break

        return (
            sent_text_successfully,
            sent_emoji_successfully,
            final_reason_parts,
            full_emoji_description_if_sent,
            need_replan_text,
            text_check_reason if not is_suitable_text else "文本检查通过或未执行",  # 返回文本检查失败的原因
            rg_decided_not_to_send_text,
        )


class DirectReplyHandler(BaseTextReplyHandler):
    """处理直接回复动作（direct_reply）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """
        执行直接回复动作。
        会尝试生成文本回复，并根据 current_emoji_query 发送附带表情。
        """
        if not observation_info or not conversation_info:
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] DirectReplyHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法执行直接回复"

        action_successful = False  # 整体动作是否成功
        final_status = "recall"  # 默认最终状态
        final_reason = "直接回复动作未成功执行"  # 默认最终原因
        max_reply_attempts: int = getattr(global_config, "pfc_max_reply_attempts", 3)

        (
            sent_text_successfully,
            sent_emoji_successfully,
            reason_parts,
            full_emoji_desc,
            need_replan_from_text_check,
            text_check_failure_reason,
            _,  # rg_decided_not_to_send_text, direct_reply 不关心这个
        ) = await self._process_and_send_reply_with_optional_emoji(
            action_type="direct_reply",
            observation_info=observation_info,
            conversation_info=conversation_info,
            max_reply_attempts=max_reply_attempts,
        )

        # 根据发送结果决定最终状态
        if sent_text_successfully or sent_emoji_successfully:
            action_successful = True
            final_status = "done"
            final_reason = "; ".join(reason_parts) if reason_parts else "成功完成操作"

            # 统一调用发送后状态更新
            event_desc_parts = []
            if sent_text_successfully and self.conversation.generated_reply:
                event_desc_parts.append(f"你回复了: '{self.conversation.generated_reply[:30]}...'")
            if sent_emoji_successfully and full_emoji_desc:
                event_desc_parts.append(f"并发送了表情: '{full_emoji_desc}'")
            event_desc = " ".join(event_desc_parts) if event_desc_parts else "机器人发送了消息"
            await self._update_post_send_states(observation_info, conversation_info, "direct_reply", event_desc)

        elif need_replan_from_text_check:  # 文本检查要求重规划
            final_status = "recall"
            final_reason = f"文本回复检查要求重新规划: {text_check_failure_reason}"
            conversation_info.last_successful_reply_action = None  # 重置追问状态
        else:  # 文本和表情都未能发送，或者文本检查失败且不需重规划（已达最大尝试）
            final_status = "max_checker_attempts_failed" if not need_replan_from_text_check else "recall"
            final_reason = f"直接回复失败。文本检查: {text_check_failure_reason}. " + (
                "; ".join(reason_parts) if reason_parts else ""
            )
            action_successful = False
            conversation_info.last_successful_reply_action = None  # 重置追问状态

        # 清理 my_message_count (如果动作整体不成功，但部分发送了，需要调整)
        if not action_successful and conversation_info:
            # _process_and_send_reply_with_optional_emoji 内部会增加 my_message_count
            # 如果这里 action_successful 为 False，说明可能部分发送了但整体认为是失败
            # 这种情况下 my_message_count 可能需要调整，但目前逻辑是每次成功发送都加1，
            # 如果 action_successful 为 False，则 last_successful_reply_action 会被清空，
            # 避免了不成功的追问。my_message_count 的精确回滚比较复杂，暂时依赖 last_successful_reply_action。
            pass

        return action_successful, final_status, final_reason.strip()


class SendNewMessageHandler(BaseTextReplyHandler):
    """处理发送新消息动作（send_new_message）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """
        执行发送新消息动作。
        会先通过 ReplyGenerator 判断是否要发送文本，如果发送，则生成并检查文本。
        同时，也可能根据 current_emoji_query 发送附带表情。
        """
        if not observation_info or not conversation_info:
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] SendNewMessageHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法执行发送新消息"

        action_successful = False  # 整体动作是否成功
        final_status = "recall"  # 默认最终状态
        final_reason = "发送新消息动作未成功执行"  # 默认最终原因
        max_reply_attempts: int = getattr(global_config, "pfc_max_reply_attempts", 3)

        (
            sent_text_successfully,
            sent_emoji_successfully,
            reason_parts,
            full_emoji_desc,
            need_replan_from_text_check,
            text_check_failure_reason,
            rg_decided_not_to_send_text,  # 重要：获取RG是否决定不发文本
        ) = await self._process_and_send_reply_with_optional_emoji(
            action_type="send_new_message",
            observation_info=observation_info,
            conversation_info=conversation_info,
            max_reply_attempts=max_reply_attempts,
        )

        # 根据发送结果和RG的决策决定最终状态
        if rg_decided_not_to_send_text:  # ReplyGenerator 明确决定不发送文本
            if sent_emoji_successfully:  # 但表情成功发送了
                action_successful = True
                final_status = "done"  # 整体算完成，因为有内容发出
                final_reason = f"回复生成器决定不发送文本，但成功发送了附带表情 ({full_emoji_desc or '未知表情'})"
                # 即使只发了表情，也算一次交互，可以更新post_send_states
                event_desc = f"你发送了表情: '{full_emoji_desc or '未知表情'}' (文本未发送)"
                await self._update_post_send_states(observation_info, conversation_info, "send_new_message", event_desc)
            else:  # RG不发文本，表情也没发出去或失败
                action_successful = True  # 决策本身是成功的（决定不发）
                final_status = "done_no_reply"  # 标记为完成但无回复
                final_reason = (
                    text_check_failure_reason
                    if text_check_failure_reason and text_check_failure_reason != "文本检查通过或未执行"
                    else "回复生成器决定不发送消息，且无表情或表情发送失败"
                )
            conversation_info.last_successful_reply_action = None  # 因为没有文本发出
            if self.conversation.conversation_info:  # 确保 my_message_count 被重置
                self.conversation.conversation_info.my_message_count = 0
        elif sent_text_successfully or sent_emoji_successfully:  # RG决定发文本（或未明确反对），且至少有一个发出去了
            action_successful = True
            final_status = "done"
            final_reason = "; ".join(reason_parts) if reason_parts else "成功完成操作"

            event_desc_parts = []
            if sent_text_successfully and self.conversation.generated_reply:
                event_desc_parts.append(f"你发送了新消息: '{self.conversation.generated_reply[:30]}...'")
            if sent_emoji_successfully and full_emoji_desc:
                event_desc_parts.append(f"并发送了表情: '{full_emoji_desc}'")
            event_desc = " ".join(event_desc_parts) if event_desc_parts else "机器人发送了消息"
            await self._update_post_send_states(observation_info, conversation_info, "send_new_message", event_desc)

        elif need_replan_from_text_check:  # 文本检查要求重规划
            final_status = "recall"
            final_reason = f"文本回复检查要求重新规划: {text_check_failure_reason}"
            conversation_info.last_successful_reply_action = None
        else:  # 文本和表情都未能发送（且RG没有明确说不发文本），或者文本检查失败且不需重规划
            final_status = "max_checker_attempts_failed" if not need_replan_from_text_check else "recall"
            final_reason = f"发送新消息失败。文本检查: {text_check_failure_reason}. " + (
                "; ".join(reason_parts) if reason_parts else ""
            )
            action_successful = False
            conversation_info.last_successful_reply_action = None

        if not action_successful and conversation_info:
            # 同 DirectReplyHandler，my_message_count 的精确回滚依赖 last_successful_reply_action 的清除
            pass

        return action_successful, final_status, final_reason.strip()


class SayGoodbyeHandler(ActionHandler):
    """处理发送告别语动作（say_goodbye）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """
        执行发送告别语的动作。
        会生成告别文本并发送，然后标记对话结束。
        """
        if not observation_info or not conversation_info:
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] SayGoodbyeHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法执行告别"

        action_successful = False
        final_status = "recall"  # 默认状态
        final_reason = "告别语动作未成功执行"  # 默认原因

        self.conversation.state = ConversationState.GENERATING  # 设置状态为生成中
        if not self.conversation.reply_generator:
            raise RuntimeError(f"ReplyGenerator 未为 {self.conversation.private_name} 初始化")

        # 生成告别语内容
        generated_content = await self.conversation.reply_generator.generate(
            observation_info, conversation_info, action_type="say_goodbye"
        )
        self.logger.info(
            f"[私聊][{self.conversation.private_name}] 动作 'say_goodbye': 生成内容: '{generated_content[:100]}...'"
        )

        if not generated_content or generated_content.startswith("抱歉"):  # 如果生成内容无效
            self.logger.warning(
                f"[私聊][{self.conversation.private_name}] 动作 'say_goodbye': 生成内容为空或为错误提示，取消发送。"
            )
            final_reason = "生成告别内容无效"
            final_status = "done"  # 即使不发送，结束对话的决策也算完成
            self.conversation.should_continue = False  # 标记对话结束
            action_successful = True  # 动作（决策结束）本身算成功
        else:  # 如果生成内容有效
            self.conversation.generated_reply = generated_content
            self.conversation.state = ConversationState.SENDING  # 设置状态为发送中
            text_segment = Seg(type="text", data=self.conversation.generated_reply)
            send_success = await self._send_reply_or_segments([text_segment], self.conversation.generated_reply)
            send_end_time = time.time()

            if send_success:  # 如果发送成功
                action_successful = True
                final_status = "done"
                final_reason = "成功发送告别语"
                self.conversation.should_continue = False  # 标记对话结束
                if self.conversation.conversation_info:
                    self.conversation.conversation_info.current_instance_message_count += 1
                    self.conversation.conversation_info.my_message_count += 1  # 告别语也算一次发言
                await self._update_bot_message_in_history(
                    send_end_time, self.conversation.generated_reply, observation_info
                )
                event_desc = f"你发送了告别消息: '{self.conversation.generated_reply[:50]}...'"
                # 注意：由于 should_continue 已设为 False，后续的 idle chat 更新可能意义不大，但情绪更新仍可进行
                await self._update_post_send_states(observation_info, conversation_info, "say_goodbye", event_desc)
            else:  # 如果发送失败
                final_status = "recall"
                final_reason = "发送告别语失败"
                action_successful = False
                self.conversation.should_continue = True  # 发送失败则不立即结束对话，让其自然流转

        return action_successful, final_status, final_reason


class SendMemesHandler(ActionHandler):
    """处理发送表情包动作（send_memes）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """
        执行发送表情包的动作。
        会根据 current_emoji_query 获取并发送表情。
        """
        if not observation_info or not conversation_info:
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] SendMemesHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法发送表情包"

        action_successful = False
        final_status = "recall"  # 默认状态
        final_reason_prefix = "发送表情包"
        final_reason = f"{final_reason_prefix}失败：未知原因"  # 默认原因
        self.conversation.state = ConversationState.GENERATING  # 或 SENDING_MEME

        emoji_query = conversation_info.current_emoji_query
        if not emoji_query:  # 如果没有表情查询
            final_reason = f"{final_reason_prefix}失败：缺少表情包查询语句"
            # 此动作不依赖文本回复的追问状态，所以不修改 last_successful_reply_action
            return False, "recall", final_reason

        # 清理表情查询，因为我们要处理它了
        conversation_info.current_emoji_query = None

        emoji_prepared_info = await self._fetch_and_prepare_emoji_segment(emoji_query)

        if emoji_prepared_info:  # 如果成功获取并准备了表情
            emoji_segment, full_emoji_description, log_emoji_description = emoji_prepared_info
            send_success = await self._send_reply_or_segments([emoji_segment], log_emoji_description)
            send_end_time = time.time()

            if send_success:  # 如果发送成功
                action_successful = True
                final_status = "done"
                final_reason = f"{final_reason_prefix}成功发送 ({full_emoji_description})"
                if self.conversation.conversation_info:
                    self.conversation.conversation_info.current_instance_message_count += 1
                    self.conversation.conversation_info.my_message_count += 1  # 表情也算一次发言
                await self._update_bot_message_in_history(
                    send_end_time, full_emoji_description, observation_info, "bot_meme_"
                )
                event_desc = f"你发送了一个表情包 ({full_emoji_description})"
                await self._update_post_send_states(observation_info, conversation_info, "send_memes", event_desc)
            else:  # 如果发送失败
                final_status = "recall"
                final_reason = f"{final_reason_prefix}失败：发送时出错"
        else:  # 如果未能获取或准备表情
            final_reason = f"{final_reason_prefix}失败：未找到或准备表情失败 ({emoji_query})"
            # last_successful_reply_action 保持不变

        return action_successful, final_status, final_reason


class RethinkGoalHandler(ActionHandler):
    """处理重新思考目标动作（rethink_goal）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """执行重新思考对话目标的动作。"""
        if not conversation_info or not observation_info:
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] RethinkGoalHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法重新思考目标"
        self.conversation.state = ConversationState.RETHINKING  # 设置状态为重新思考中
        if not self.conversation.goal_analyzer:
            raise RuntimeError(f"GoalAnalyzer 未为 {self.conversation.private_name} 初始化")
        await self.conversation.goal_analyzer.analyze_goal(conversation_info, observation_info)  # 调用目标分析器
        event_desc = "你重新思考了对话目标和方向"
        await self._update_relationship_and_emotion(observation_info, conversation_info, event_desc)  # 更新关系和情绪
        return True, "done", "成功重新思考目标"


class ListeningHandler(ActionHandler):
    """处理倾听动作（listening）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """执行倾听对方发言的动作。"""
        if not conversation_info or not observation_info:
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] ListeningHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法执行倾听"
        self.conversation.state = ConversationState.LISTENING  # 设置状态为倾听中
        if not self.conversation.waiter:
            raise RuntimeError(f"Waiter 未为 {self.conversation.private_name} 初始化")
        await self.conversation.waiter.wait_listening(conversation_info)  # 调用等待器的倾听方法
        event_desc = "你决定耐心倾听对方的发言"
        await self._update_relationship_and_emotion(observation_info, conversation_info, event_desc)  # 更新关系和情绪
        # listening 动作完成后，状态会由新消息或超时驱动，最终回到 ANALYZING
        return True, "done", "进入倾听状态"


class EndConversationHandler(ActionHandler):
    """处理结束对话动作（end_conversation）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """执行结束当前对话的动作。"""
        self.logger.info(
            f"[私聊][{self.conversation.private_name}] 动作 'end_conversation': 收到最终结束指令，停止对话..."
        )
        self.conversation.should_continue = False  # 标记对话不应继续，主循环会因此退出
        # 注意：最终的关系评估通常在 Conversation.stop() 方法中进行
        return True, "done", "对话结束指令已执行"


class BlockAndIgnoreHandler(ActionHandler):
    """处理屏蔽并忽略对话动作（block_and_ignore）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """执行屏蔽并忽略当前对话一段时间的动作。"""
        if not conversation_info or not observation_info:  # 防御性检查
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] BlockAndIgnoreHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法执行屏蔽"
        self.logger.info(f"[私聊][{self.conversation.private_name}] 动作 'block_and_ignore': 不想再理你了...")
        ignore_duration_seconds = 10 * 60  # 例如忽略10分钟，可以配置
        self.conversation.ignore_until_timestamp = time.time() + ignore_duration_seconds  # 设置忽略截止时间
        self.conversation.state = ConversationState.IGNORED  # 设置状态为已忽略
        event_desc = "当前对话让你感到不适，你决定暂时不再理会对方"
        await self._update_relationship_and_emotion(observation_info, conversation_info, event_desc)  # 更新关系和情绪
        # should_continue 仍为 True，但主循环会检查 ignore_until_timestamp
        return True, "done", f"已屏蔽并忽略对话 {ignore_duration_seconds // 60} 分钟"


class WaitHandler(ActionHandler):
    """处理等待动作（wait）的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """执行等待对方回复的动作。"""
        if not conversation_info or not observation_info:  # 防御性检查
            self.logger.error(
                f"[私聊][{self.conversation.private_name}] WaitHandler: ObservationInfo 或 ConversationInfo 为空。"
            )
            return False, "error", "内部信息缺失，无法执行等待"
        self.conversation.state = ConversationState.WAITING  # 设置状态为等待中
        if not self.conversation.waiter:
            raise RuntimeError(f"Waiter 未为 {self.conversation.private_name} 初始化")
        timeout_occurred = await self.conversation.waiter.wait(conversation_info)  # 调用等待器的常规等待方法
        event_desc = "你等待对方回复，但对方长时间没有回应" if timeout_occurred else "你选择等待对方的回复"
        await self._update_relationship_and_emotion(observation_info, conversation_info, event_desc)  # 更新关系和情绪
        # wait 动作完成后，状态会由新消息或超时驱动，最终回到 ANALYZING
        return True, "done", "等待动作完成"


class UnknownActionHandler(ActionHandler):
    """处理未知或无效动作的处理器。"""

    async def execute(
        self,
        reason: str,
        observation_info: Optional[ObservationInfo],
        conversation_info: Optional[ConversationInfo],
        action_start_time: float,
        current_action_record: dict,
    ) -> tuple[bool, str, str]:
        """处理无法识别的动作类型。"""
        action_name = current_action_record.get("action", "未知动作类型")  # 从记录中获取动作名
        self.logger.warning(f"[私聊][{self.conversation.private_name}] 接收到未知的动作类型: {action_name}")
        return False, "recall", f"未知的动作类型: {action_name}"  # 标记为需要重新规划
