from datetime import datetime
from src.config.config import global_config
import traceback
from src.chat.utils.chat_message_builder import (
    get_raw_msg_before_timestamp_with_chat,
    build_readable_messages,
    get_raw_msg_by_timestamp_with_chat,
    num_new_messages_since,
    get_person_id_list,
)
from src.chat.utils.prompt_builder import global_prompt_manager
from typing import Optional
import difflib
from src.chat.message_receive.message import MessageRecv  # 添加 MessageRecv 导入
from src.chat.heart_flow.observation.observation import Observation

from src.common.logger_manager import get_logger
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.chat.utils.prompt_builder import Prompt


logger = get_logger("observation")


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


# 聊天观察
class ChattingObservation(Observation):
    def __init__(self, chat_id):
        super().__init__(chat_id)
        self.chat_id = chat_id
        self.platform = "qq"

        # --- Initialize attributes (defaults) ---
        self.is_group_chat: bool = False
        self.chat_target_info: Optional[dict] = None
        # --- End Initialization ---

        # --- Other attributes initialized in __init__ ---
        self.talking_message = []
        self.talking_message_str = ""
        self.talking_message_str_truncate = ""
        self.name = global_config.bot.nickname
        self.nick_name = global_config.bot.alias_names
        self.max_now_obs_len = global_config.focus_chat.observation_context_size
        self.overlap_len = global_config.focus_chat.compressed_length
        self.mid_memories = []
        self.max_mid_memory_len = global_config.focus_chat.compress_length_limit
        self.mid_memory_info = ""
        self.person_list = []
        self.oldest_messages = []
        self.oldest_messages_str = ""
        self.compressor_prompt = ""

    async def initialize(self):
        self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.chat_id)
        logger.debug(f"初始化observation: self.is_group_chat: {self.is_group_chat}")
        logger.debug(f"初始化observation: self.chat_target_info: {self.chat_target_info}")
        initial_messages = get_raw_msg_before_timestamp_with_chat(self.chat_id, self.last_observe_time, 10)
        self.talking_message = initial_messages
        self.talking_message_str = await build_readable_messages(self.talking_message)

    # 进行一次观察 返回观察结果observe_info
    def get_observe_info(self, ids=None):
        mid_memory_str = ""
        if ids:
            for id in ids:
                print(f"id：{id}")
                try:
                    for mid_memory in self.mid_memories:
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
            mid_memory_str = "之前的聊天内容：\n"
            for mid_memory in self.mid_memories:
                mid_memory_str += f"{mid_memory['theme']}\n"
            return mid_memory_str + "现在群里正在聊：\n" + self.talking_message_str

    def search_message_by_text(self, text: str) -> Optional[MessageRecv]:
        """
        根据回复的纯文本
        1. 在talking_message中查找最新的，最匹配的消息
        2. 如果找到，则返回消息
        """
        msg_list = []
        find_msg = None
        reverse_talking_message = list(reversed(self.talking_message))

        for message in reverse_talking_message:
            if message["processed_plain_text"] == text:
                find_msg = message
                # logger.debug(f"找到的锚定消息：find_msg: {find_msg}")
                break
            else:
                similarity = difflib.SequenceMatcher(None, text, message["processed_plain_text"]).ratio()
                msg_list.append({"message": message, "similarity": similarity})
            # logger.debug(f"对锚定消息检查：message: {message['processed_plain_text']},similarity: {similarity}")
        if not find_msg:
            if msg_list:
                msg_list.sort(key=lambda x: x["similarity"], reverse=True)
                if msg_list[0]["similarity"] >= 0.5:  # 只返回相似度大于等于0.5的消息
                    find_msg = msg_list[0]["message"]
                else:
                    logger.debug("没有找到锚定消息,相似度低")
                    return None
            else:
                logger.debug("没有找到锚定消息，没有消息捕获")
                return None

        # logger.debug(f"找到的锚定消息：find_msg: {find_msg}")

        # 创建所需的user_info字段
        user_info = {
            "platform": find_msg.get("user_platform", ""),
            "user_id": find_msg.get("user_id", ""),
            "user_nickname": find_msg.get("user_nickname", ""),
            "user_cardname": find_msg.get("user_cardname", ""),
        }

        # 创建所需的group_info字段，如果是群聊的话
        group_info = {}
        if find_msg.get("chat_info_group_id"):
            group_info = {
                "platform": find_msg.get("chat_info_group_platform", ""),
                "group_id": find_msg.get("chat_info_group_id", ""),
                "group_name": find_msg.get("chat_info_group_name", ""),
            }

        content_format = ""
        accept_format = ""
        template_items = {}

        format_info = {"content_format": content_format, "accept_format": accept_format}
        template_info = {
            "template_items": template_items,
        }

        message_info = {
            "platform": self.platform,
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
            "detailed_plain_text": find_msg.get("processed_plain_text"),
            "processed_plain_text": find_msg.get("processed_plain_text"),
        }
        find_rec_msg = MessageRecv(message_dict)
        logger.debug(f"锚定消息处理后：find_rec_msg: {find_rec_msg}")
        return find_rec_msg

    async def observe(self):
        # 自上一次观察的新消息
        new_messages_list = get_raw_msg_by_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp_start=self.last_observe_time,
            timestamp_end=datetime.now().timestamp(),
            limit=self.max_now_obs_len,
            limit_mode="latest",
        )

        # print(f"new_messages_list: {new_messages_list}")

        last_obs_time_mark = self.last_observe_time
        if new_messages_list:
            self.last_observe_time = new_messages_list[-1]["time"]
            self.talking_message.extend(new_messages_list)

        if len(self.talking_message) > self.max_now_obs_len:
            # 计算需要移除的消息数量，保留最新的 max_now_obs_len 条
            messages_to_remove_count = len(self.talking_message) - self.max_now_obs_len
            oldest_messages = self.talking_message[:messages_to_remove_count]
            self.talking_message = self.talking_message[messages_to_remove_count:]  # 保留后半部分，即最新的

            # print(f"压缩中：oldest_messages: {oldest_messages}")
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

            if prompt:  # Check if prompt was built successfully
                self.compressor_prompt = prompt
                self.oldest_messages = oldest_messages
                self.oldest_messages_str = oldest_messages_str

        # 构建中
        # print(f"构建中：self.talking_message: {self.talking_message}")
        self.talking_message_str = await build_readable_messages(
            messages=self.talking_message,
            timestamp_mode="lite",
            read_mark=last_obs_time_mark,
        )
        # print(f"构建中：self.talking_message_str: {self.talking_message_str}")
        self.talking_message_str_truncate = await build_readable_messages(
            messages=self.talking_message,
            timestamp_mode="normal",
            read_mark=last_obs_time_mark,
            truncate=True,
        )
        # print(f"构建中：self.talking_message_str_truncate: {self.talking_message_str_truncate}")

        self.person_list = await get_person_id_list(self.talking_message)
        # print(f"构建中：self.person_list: {self.person_list}")

        logger.trace(
            f"Chat {self.chat_id} - 压缩早期记忆：{self.mid_memory_info}\n现在聊天内容：{self.talking_message_str}"
        )

    async def has_new_messages_since(self, timestamp: float) -> bool:
        """检查指定时间戳之后是否有新消息"""
        count = num_new_messages_since(chat_id=self.chat_id, timestamp_start=timestamp)
        return count > 0
