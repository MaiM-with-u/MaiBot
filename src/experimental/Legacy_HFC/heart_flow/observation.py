# 定义了来自外部世界的信息
# 外部世界可以是某个聊天 不同平台的聊天 也可以是任意媒体
from datetime import datetime
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from src.common.logger_manager import get_logger
import traceback
from src.chat.utils.chat_message_builder import (
    get_raw_msg_before_timestamp_with_chat,
    build_readable_messages,
    get_raw_msg_by_timestamp_with_chat,
    num_new_messages_since,
    get_person_id_list,
)
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from typing import Optional
import difflib
from src.chat.message_receive.message import MessageRecv  # 添加 MessageRecv 导入

# Import the new utility function
from .utils_chat import get_chat_type_and_target_info

logger = get_logger("observation")

# --- Define Prompt Templates for Chat Summary ---
Prompt(
    """这是qq群聊的聊天记录，请总结以下聊天记录的主题：
{chat_logs}
请用一句话概括，包括人物、事件和主要信息，不要分点。""",
    "chat_summary_group_prompt",  # Template for group chat
)

Prompt(
    """这是你和{chat_target}的私聊记录，请总结以下聊天记录的主题：
{chat_logs}
请用一句话概括，包括事件，时间，和主要信息，不要分点。""",
    "chat_summary_private_prompt",  # Template for private chat
)
# --- End Prompt Template Definition ---


# 所有观察的基类
class Observation:
    def __init__(self, observe_type, observe_id):
        self.observe_info = ""
        self.observe_type = observe_type
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间

    async def observe(self):
        pass


# 聊天观察
class ChattingObservation(Observation):
    def __init__(self, chat_id):
        super().__init__("chat", chat_id)
        self.chat_id = chat_id

        # --- Initialize attributes (defaults) ---
        self.is_group_chat: bool = False
        self.chat_target_info: Optional[dict] = None
        # --- End Initialization ---

        # --- Other attributes initialized in __init__ ---
        self.talking_message = []
        self.talking_message_str = ""
        self.talking_message_str_truncate = ""
        self.name = global_config.BOT_NICKNAME
        self.nick_name = global_config.BOT_ALIAS_NAMES
        self.max_now_obs_len = global_config.observation_context_size
        self.overlap_len = global_config.compressed_length
        self.mid_memorys = []
        self.max_mid_memory_len = global_config.compress_length_limit
        self.mid_memory_info = ""
        self.person_list = []
        self.llm_summary = LLMRequest(
            model=global_config.llm_observation, temperature=0.7, max_tokens=300, request_type="chat_observation"
        )

    async def initialize(self):
        # --- Use utility function to determine chat type and fetch info ---
        self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.chat_id)
        # logger.debug(f"is_group_chat: {self.is_group_chat}")
        # logger.debug(f"chat_target_info: {self.chat_target_info}")
        # --- End using utility function ---

        # Fetch initial messages (existing logic)
        initial_messages = get_raw_msg_before_timestamp_with_chat(self.chat_id, self.last_observe_time, 10)
        self.talking_message = initial_messages
        self.talking_message_str = await build_readable_messages(self.talking_message)

    # 进行一次观察 返回观察结果observe_info
    def get_observe_info(self, ids=None):
        if ids:
            mid_memory_str = ""
            for id in ids:
                # print(f"id：{id}")
                try:
                    for mid_memory in self.mid_memorys:
                        if mid_memory["id"] == id:
                            mid_memory_by_id = mid_memory
                            msg_str = ""
                            for msg in mid_memory_by_id["messages"]:
                                msg_str += f"{msg['detailed_plain_text']}"
                            # time_diff = int((datetime.now().timestamp() - mid_memory_by_id["created_at"]) / 60)
                            # mid_memory_str += f"距离现在{time_diff}分钟前：\n{msg_str}\n"
                            mid_memory_str += f"{msg_str}\n"
                except Exception as e:
                    logger.error(f"获取mid_memory_id失败: {e}")
                    traceback.print_exc()
                    return self.talking_message_str

            return mid_memory_str + "现在群里正在聊：\n" + self.talking_message_str

        else:
            return self.talking_message_str

    async def observe(self):
        # 自上一次观察的新消息
        new_messages_list = get_raw_msg_by_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp_start=self.last_observe_time,
            timestamp_end=datetime.now().timestamp(),
            limit=self.max_now_obs_len,
            limit_mode="latest",
        )

        last_obs_time_mark = self.last_observe_time
        if new_messages_list:
            self.last_observe_time = new_messages_list[-1]["time"]
            self.talking_message.extend(new_messages_list)

        if len(self.talking_message) > self.max_now_obs_len:
            # 计算需要移除的消息数量，保留最新的 max_now_obs_len 条
            messages_to_remove_count = len(self.talking_message) - self.max_now_obs_len
            oldest_messages = self.talking_message[:messages_to_remove_count]
            self.talking_message = self.talking_message[messages_to_remove_count:]  # 保留后半部分，即最新的

            oldest_messages_str = await build_readable_messages(
                messages=oldest_messages, timestamp_mode="normal", read_mark=0
            )

            # --- Build prompt using template ---
            prompt = None  # Initialize prompt as None
            try:
                # 构建 Prompt - 根据 is_group_chat 选择模板
                if self.is_group_chat:
                    prompt_template_name = "chat_summary_group_prompt"
                    prompt = await global_prompt_manager.format_prompt(
                        prompt_template_name, chat_logs=oldest_messages_str
                    )
                else:
                    # For private chat, add chat_target to the prompt variables
                    prompt_template_name = "chat_summary_private_prompt"
                    # Determine the target name for the prompt
                    chat_target_name = "对方"  # Default fallback
                    if self.chat_target_info:
                        # Prioritize person_name, then nickname
                        chat_target_name = (
                            self.chat_target_info.get("person_name")
                            or self.chat_target_info.get("user_nickname")
                            or chat_target_name
                        )

                    # Format the private chat prompt
                    prompt = await global_prompt_manager.format_prompt(
                        prompt_template_name,
                        # Assuming the private prompt template uses {chat_target}
                        chat_target=chat_target_name,
                        chat_logs=oldest_messages_str,
                    )
            except Exception as e:
                logger.error(f"构建总结 Prompt 失败 for chat {self.chat_id}: {e}")
                # prompt remains None

            summary = "没有主题的闲聊"  # 默认值

            if prompt:  # Check if prompt was built successfully
                try:
                    summary_result, _, _ = await self.llm_summary.generate_response(prompt)
                    if summary_result:  # 确保结果不为空
                        summary = summary_result
                except Exception as e:
                    logger.error(f"总结主题失败 for chat {self.chat_id}: {e}")
                    # 保留默认总结 "没有主题的闲聊"
            else:
                logger.warning(f"因 Prompt 构建失败，跳过 LLM 总结 for chat {self.chat_id}")

            mid_memory = {
                "id": str(int(datetime.now().timestamp())),
                "theme": summary,
                "messages": oldest_messages,  # 存储原始消息对象
                "readable_messages": oldest_messages_str,
                # "timestamps": oldest_timestamps,
                "chat_id": self.chat_id,
                "created_at": datetime.now().timestamp(),
            }

            self.mid_memorys.append(mid_memory)
            if len(self.mid_memorys) > self.max_mid_memory_len:
                self.mid_memorys.pop(0)  # 移除最旧的

            mid_memory_str = "之前聊天的内容概述是：\n"
            for mid_memory_item in self.mid_memorys:  # 重命名循环变量以示区分
                time_diff = int((datetime.now().timestamp() - mid_memory_item["created_at"]) / 60)
                mid_memory_str += (
                    f"距离现在{time_diff}分钟前(聊天记录id:{mid_memory_item['id']})：{mid_memory_item['theme']}\n"
                )
            self.mid_memory_info = mid_memory_str

        self.talking_message_str = await build_readable_messages(
            messages=self.talking_message,
            timestamp_mode="lite",
            read_mark=last_obs_time_mark,
        )
        self.talking_message_str_truncate = await build_readable_messages(
            messages=self.talking_message,
            timestamp_mode="normal",
            read_mark=last_obs_time_mark,
            truncate=True,
        )

        self.person_list = await get_person_id_list(self.talking_message)

        # print(f"self.11111person_list: {self.person_list}")

        logger.trace(
            f"Chat {self.chat_id} - 压缩早期记忆：{self.mid_memory_info}\n现在聊天内容：{self.talking_message_str}"
        )

    async def find_best_matching_message(self, search_str: str, min_similarity: float = 0.6) -> Optional[MessageRecv]:
        """
        在 talking_message 中查找与 search_str 最匹配的消息。

        Args:
            search_str: 要搜索的字符串。
            min_similarity: 要求的最低相似度（0到1之间）。

        Returns:
            匹配的 MessageRecv 实例，如果找不到则返回 None。
        """
        best_match_score = -1.0
        best_match_dict = None

        if not self.talking_message:
            logger.debug(f"Chat {self.chat_id}: talking_message is empty, cannot find match for '{search_str}'")
            return None

        for message_dict in self.talking_message:
            try:
                # 临时创建 MessageRecv 以处理文本
                temp_msg = MessageRecv(message_dict)
                await temp_msg.process()  # 处理消息以获取 processed_plain_text
                current_text = temp_msg.processed_plain_text

                if not current_text:  # 跳过没有文本内容的消息
                    continue

                # 计算相似度
                matcher = difflib.SequenceMatcher(None, search_str, current_text)
                score = matcher.ratio()

                # logger.debug(f"Comparing '{search_str}' with '{current_text}', score: {score}") # 可选：用于调试

                if score > best_match_score:
                    best_match_score = score
                    best_match_dict = message_dict

            except Exception as e:
                logger.error(f"Error processing message for matching in chat {self.chat_id}: {e}", exc_info=True)
                continue  # 继续处理下一条消息

        if best_match_dict is not None and best_match_score >= min_similarity:
            logger.debug(f"Found best match for '{search_str}' with score {best_match_score:.2f}")
            try:
                final_msg = MessageRecv(best_match_dict)
                await final_msg.process()
                # 确保 MessageRecv 实例有关联的 chat_stream
                if hasattr(self, "chat_stream"):
                    final_msg.update_chat_stream(self.chat_stream)
                else:
                    logger.warning(
                        f"ChattingObservation instance for chat {self.chat_id} does not have a chat_stream attribute set."
                    )
                return final_msg
            except Exception as e:
                logger.error(f"Error creating final MessageRecv for chat {self.chat_id}: {e}", exc_info=True)
                return None
        else:
            logger.debug(
                f"No suitable match found for '{search_str}' in chat {self.chat_id} (best score: {best_match_score:.2f}, threshold: {min_similarity})"
            )
            return None

    async def has_new_messages_since(self, timestamp: float) -> bool:
        """检查指定时间戳之后是否有新消息"""
        count = num_new_messages_since(chat_id=self.chat_id, timestamp_start=timestamp)
        return count > 0
