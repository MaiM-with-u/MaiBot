from src.person_info.person_info import person_info_manager
from src.common.logger_manager import get_logger
import asyncio
from dataclasses import dataclass, field
from .message import MessageRecv
from maim_message import BaseMessageInfo, GroupInfo
import hashlib
from typing import Dict
from collections import OrderedDict
import random
import time
from ...config.config import global_config

logger = get_logger("message_buffer")


@dataclass
class CacheMessages:
    message: MessageRecv
    cache_determination: asyncio.Event = field(default_factory=asyncio.Event)  # 判断缓冲是否产生结果
    result: str = "U"


class MessageBuffer:
    def __init__(self):
        self.buffer_pool: Dict[str, OrderedDict[str, CacheMessages]] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def get_person_id_(platform: str, user_id: str, group_info: GroupInfo):
        """获取唯一id"""
        if group_info:
            group_id = group_info.group_id
        else:
            group_id = "私聊"
        key = f"{platform}_{user_id}_{group_id}"
        return hashlib.md5(key.encode()).hexdigest()

    async def start_caching_messages(self, message: MessageRecv):
        """添加消息，启动缓冲"""
        if not global_config.chat.message_buffer:
            person_id = person_info_manager.get_person_id(
                message.message_info.user_info.platform, message.message_info.user_info.user_id
            )
            asyncio.create_task(self.save_message_interval(person_id, message.message_info))
            return
        person_id_ = self.get_person_id_(
            message.message_info.platform, message.message_info.user_info.user_id, message.message_info.group_info
        )

        async with self.lock:
            if person_id_ not in self.buffer_pool:
                self.buffer_pool[person_id_] = OrderedDict()

            # 标记该用户之前的未处理消息
            for cache_msg in self.buffer_pool[person_id_].values():
                if cache_msg.result == "U":
                    cache_msg.result = "F"
                    cache_msg.cache_determination.set()
                    logger.debug(f"被新消息覆盖信息id: {cache_msg.message.message_info.message_id}")

            # 查找最近的处理成功消息(T)
            recent_f_count = 0
            for msg_id in reversed(self.buffer_pool[person_id_]):
                msg = self.buffer_pool[person_id_][msg_id]
                if msg.result == "T":
                    break
                elif msg.result == "F":
                    recent_f_count += 1

            # 判断条件：最近T之后有超过3-5条F
            if recent_f_count >= random.randint(3, 5):
                new_msg = CacheMessages(message=message, result="T")
                new_msg.cache_determination.set()
                self.buffer_pool[person_id_][message.message_info.message_id] = new_msg
                logger.debug(f"快速处理消息(已堆积{recent_f_count}条F): {message.message_info.message_id}")
                return

            # 添加新消息
            self.buffer_pool[person_id_][message.message_info.message_id] = CacheMessages(message=message)

        # 启动3秒缓冲计时器
        person_id = person_info_manager.get_person_id(
            message.message_info.user_info.platform, message.message_info.user_info.user_id
        )
        asyncio.create_task(self.save_message_interval(person_id, message.message_info))
        asyncio.create_task(self._debounce_processor(person_id_, message.message_info.message_id, person_id))

    async def _debounce_processor(self, person_id_: str, message_id: str, person_id: str):
        """等待3秒无新消息"""
        interval_time = await person_info_manager.get_value(person_id, "msg_interval")
        if not isinstance(interval_time, (int, str)) or not str(interval_time).isdigit():
            logger.debug("debounce_processor无效的时间")
            return
        interval_time = max(0.5, int(interval_time) / 1000)
        await asyncio.sleep(interval_time)

        async with self.lock:
            if person_id_ not in self.buffer_pool or message_id not in self.buffer_pool[person_id_]:
                logger.debug(f"消息已被清理，msgid: {message_id}")
                return

            cache_msg = self.buffer_pool[person_id_][message_id]
            if cache_msg.result == "U":
                cache_msg.result = "T"
                cache_msg.cache_determination.set()

    async def query_buffer_result(self, message: MessageRecv) -> bool:
        """查询缓冲结果，并清理"""
        if not global_config.chat.message_buffer:
            return True
        person_id_ = self.get_person_id_(
            message.message_info.platform, message.message_info.user_info.user_id, message.message_info.group_info
        )

        async with self.lock:
            user_msgs = self.buffer_pool.get(person_id_, {})
            cache_msg = user_msgs.get(message.message_info.message_id)

            if not cache_msg:
                logger.debug(f"查询异常，消息不存在，msgid: {message.message_info.message_id}")
                return False  # 消息不存在或已清理

        try:
            await asyncio.wait_for(cache_msg.cache_determination.wait(), timeout=10)
            result = cache_msg.result == "T"

            if result:
                async with self.lock:  # 再次加锁
                    # 清理所有早于当前消息的已处理消息， 收集所有早于当前消息的F消息的processed_plain_text
                    keep_msgs = OrderedDict()  # 用于存放 T 消息之后的消息
                    collected_texts = []  # 用于收集 T 消息及之前 F 消息的文本
                    process_target_found = False

                    # 遍历当前用户的所有缓冲消息
                    for msg_id, cache_msg in self.buffer_pool[person_id_].items():
                        # 如果找到了目标处理消息 (T 状态)
                        if msg_id == message.message_info.message_id:
                            process_target_found = True
                            # 收集这条 T 消息的文本 (如果有)
                            if (
                                hasattr(cache_msg.message, "processed_plain_text")
                                and cache_msg.message.processed_plain_text
                            ):
                                collected_texts.append(cache_msg.message.processed_plain_text)
                            # 不立即放入 keep_msgs，因为它之前的 F 消息也处理完了

                        # 如果已经找到了目标 T 消息，之后的消息需要保留
                        elif process_target_found:
                            keep_msgs[msg_id] = cache_msg

                        # 如果还没找到目标 T 消息，说明是之前的消息 (F 或 U)
                        else:
                            if cache_msg.result == "F":
                                # 收集这条 F 消息的文本 (如果有)
                                if (
                                    hasattr(cache_msg.message, "processed_plain_text")
                                    and cache_msg.message.processed_plain_text
                                ):
                                    collected_texts.append(cache_msg.message.processed_plain_text)
                            elif cache_msg.result == "U":
                                # 理论上不应该在 T 消息之前还有 U 消息，记录日志
                                logger.warning(
                                    f"异常状态：在目标 T 消息 {message.message_info.message_id} 之前发现未处理的 U 消息 {cache_msg.message.message_info.message_id}"
                                )
                                # 也可以选择收集其文本
                                if (
                                    hasattr(cache_msg.message, "processed_plain_text")
                                    and cache_msg.message.processed_plain_text
                                ):
                                    collected_texts.append(cache_msg.message.processed_plain_text)

                    # 更新当前消息 (message) 的 processed_plain_text
                    # 只有在收集到的文本多于一条，或者只有一条但与原始文本不同时才合并
                    if collected_texts:
                        # 使用 OrderedDict 去重，同时保留原始顺序
                        unique_texts = list(OrderedDict.fromkeys(collected_texts))
                        merged_text = "，".join(unique_texts)

                        # 只有在合并后的文本与原始文本不同时才更新
                        # 并且确保不是空合并
                        if merged_text and merged_text != message.processed_plain_text:
                            message.processed_plain_text = merged_text
                            # 如果合并了文本，原消息不再视为纯 emoji
                            if hasattr(message, "is_emoji"):
                                message.is_emoji = False
                            logger.debug(
                                f"合并了 {len(unique_texts)} 条消息的文本内容到当前消息 {message.message_info.message_id}"
                            )

                    # 更新缓冲池，只保留 T 消息之后的消息
                    self.buffer_pool[person_id_] = keep_msgs
            return result
        except asyncio.TimeoutError:
            logger.debug(f"查询超时消息id： {message.message_info.message_id}")
            return False

    @staticmethod
    async def save_message_interval(person_id: str, message: BaseMessageInfo):
        message_interval_list = await person_info_manager.get_value(person_id, "msg_interval_list")
        now_time_ms = int(round(time.time() * 1000))
        if len(message_interval_list) < 1000:
            message_interval_list.append(now_time_ms)
        else:
            message_interval_list.pop(0)
            message_interval_list.append(now_time_ms)
        data = {
            "platform": message.platform,
            "user_id": message.user_info.user_id,
            "nickname": message.user_info.user_nickname,
            "konw_time": int(time.time()),
        }
        await person_info_manager.update_one_field(person_id, "msg_interval_list", message_interval_list, data)


message_buffer = MessageBuffer()
