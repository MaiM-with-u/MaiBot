# 定义了来自外部世界的信息
# 外部世界可以是某个聊天 不同平台的聊天 也可以是任意媒体
from datetime import datetime
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.common.logger import get_module_logger
import traceback
from src.plugins.utils.chat_message_builder import (
    get_raw_msg_before_timestamp_with_chat,
    build_readable_messages,
    get_raw_msg_by_timestamp_with_chat,
    num_new_messages_since,
)

logger = get_module_logger("observation")


# 所有观察的基类
class Observation:
    def __init__(self, observe_type, observe_id):
        self.observe_info = ""
        self.observe_type = observe_type
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间


# 聊天观察
class ChattingObservation(Observation):
    def __init__(self, chat_id):
        super().__init__("chat", chat_id)
        self.chat_id = chat_id

        self.talking_message = []
        self.talking_message_str = ""

        self.name = global_config.BOT_NICKNAME
        self.nick_name = global_config.BOT_ALIAS_NAMES

        self.max_now_obs_len = global_config.observation_context_size
        self.overlap_len = global_config.compressed_length
        self.mid_memorys = []
        self.max_mid_memory_len = global_config.compress_length_limit
        self.mid_memory_info = ""

        self.llm_summary = LLMRequest(
            model=global_config.llm_observation, temperature=0.7, max_tokens=300, request_type="chat_observation"
        )

    async def initialize(self):
        initial_messages = get_raw_msg_before_timestamp_with_chat(self.chat_id, self.last_observe_time, 10)
        self.talking_message = initial_messages  # 将这些消息设为初始上下文
        self.talking_message_str = await build_readable_messages(self.talking_message)

    # 进行一次观察 返回观察结果observe_info
    def get_observe_info(self, ids=None):
        if ids:
            mid_memory_str = ""
            for id in ids:
                print(f"id：{id}")
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
        # 查找新消息，最多获取 self.max_now_obs_len 条
        print("2222222222222222221111111111111111开始观察")
        new_messages_list = get_raw_msg_by_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp_start=self.last_observe_time,
            timestamp_end=datetime.now().timestamp(),  # 使用当前时间作为结束时间戳
            limit=self.max_now_obs_len,
            limit_mode="latest",
        )
        print(f"2222222222222222221111111111111111获取到新消息{len(new_messages_list)}条")
        if new_messages_list:  # 检查列表是否为空
            self.last_observe_time = new_messages_list[-1]["time"]
            self.talking_message.extend(new_messages_list)

        if len(self.talking_message) > self.max_now_obs_len:
            # 计算需要移除的消息数量，保留最新的 max_now_obs_len 条
            messages_to_remove_count = len(self.talking_message) - self.max_now_obs_len
            oldest_messages = self.talking_message[:messages_to_remove_count]
            self.talking_message = self.talking_message[messages_to_remove_count:]  # 保留后半部分，即最新的

            oldest_messages_str = await build_readable_messages(oldest_messages)

            # 调用 LLM 总结主题
            prompt = (
                f"请总结以下聊天记录的主题：\n{oldest_messages_str}\n用一句话概括包括人物事件和主要信息，不要分点："
            )
            summary = "没有主题的闲聊"  # 默认值
            try:
                summary_result, _ = await self.llm_summary.generate_response_async(prompt)
                if summary_result:  # 确保结果不为空
                    summary = summary_result
            except Exception as e:
                logger.error(f"总结主题失败 for chat {self.chat_id}: {e}")
                # 保留默认总结 "没有主题的闲聊"

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
        # except Exception as e:  # 将异常处理移至此处以覆盖整个总结过程
        #     logger.error(f"处理和总结旧消息时出错 for chat {self.chat_id}: {e}")
        #     traceback.print_exc()  # 记录详细堆栈
        # print(f"处理后self.talking_message：{self.talking_message}")

        self.talking_message_str = await build_readable_messages(self.talking_message)

        logger.trace(
            f"Chat {self.chat_id} - 压缩早期记忆：{self.mid_memory_info}\n现在聊天内容：{self.talking_message_str}"
        )

    async def has_new_messages_since(self, timestamp: float) -> bool:
        """检查指定时间戳之后是否有新消息"""
        count = num_new_messages_since(chat_id=self.chat_id, timestamp_start=timestamp)
        return count > 0
