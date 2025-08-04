"""
Mxp 模式：梦溪畔独家赞助
此模式的一些参数不会在配置文件中显示，要修改请在可变参数下修改
同时一些全局设置对此模式无效
此模式的可变参数暂时比较草率，需要调参仙人的大手
此模式的特点：
1.每个聊天流的每个用户的意愿是独立的
2.接入关系系统，关系会影响意愿值（已移除，因为关系系统重构）
3.会根据群聊的热度来调整基础意愿值
4.限制同时思考的消息数量，防止喷射
5.拥有单聊增益，无论在群里还是私聊，只要bot一直和你聊，就会增加意愿值
6.意愿分为衰减意愿+临时意愿
7.疲劳机制

如果你发现本模式出现了bug
上上策是询问智慧的小草神（）
上策是询问万能的千石可乐
中策是发issue
下下策是询问一个菜鸟（@梦溪畔）
"""

from typing import Dict
import asyncio
import time
import math

from src.chat.message_receive.chat_stream import ChatStream
from .willing_manager import BaseWillingManager


class MxpWillingManager(BaseWillingManager):
    """Mxp意愿管理器"""

    def __init__(self):
        super().__init__()
        self.chat_person_reply_willing: Dict[str, Dict[str, float]] = {}  # chat_id: {person_id: 意愿值}
        self.chat_new_message_time: Dict[str, list[float]] = {}  # 聊天流ID: 消息时间
        self.last_response_person: Dict[str, tuple[str, int]] = {}  # 上次回复的用户信息
        self.temporary_willing: float = 0  # 临时意愿值
        self.chat_bot_message_time: Dict[str, list[float]] = {}  # 聊天流ID: bot已回复消息时间
        self.chat_fatigue_punishment_list: Dict[
            str, list[tuple[float, float]]
        ] = {}  # 聊天流疲劳惩罚列, 聊天流ID: 惩罚时间列(开始时间，持续时间)
        self.chat_fatigue_willing_attenuation: Dict[str, float] = {}  # 聊天流疲劳意愿衰减值

        # 可变参数
        self.intention_decay_rate = 0.93  # 意愿衰减率

        self.number_of_message_storage = 12  # 消息存储数量
        self.expected_replies_per_min = 3  # 每分钟预期回复数
        self.basic_maximum_willing = 0.5  # 基础最大意愿值

        self.mention_willing_gain = 0.6  # 提及意愿增益
        self.interest_willing_gain = 0.3  # 兴趣意愿增益
        self.single_chat_gain = 0.12  # 单聊增益

        self.fatigue_messages_triggered_num = self.expected_replies_per_min  # 疲劳消息触发数量(int)
        self.fatigue_coefficient = 1.0  # 疲劳系数

        self.is_debug = False  # 是否开启调试模式

    async def async_task_starter(self) -> None:
        """异步任务启动器"""
        asyncio.create_task(self._return_to_basic_willing())
        asyncio.create_task(self._chat_new_message_to_change_basic_willing())
        asyncio.create_task(self._fatigue_attenuation())

    async def before_generate_reply_handle(self, message_id: str):
        """回复前处理"""
        current_time = time.time()
        async with self.lock:
            w_info = self.ongoing_messages[message_id]
            if w_info.chat_id not in self.chat_bot_message_time:
                self.chat_bot_message_time[w_info.chat_id] = []
            self.chat_bot_message_time[w_info.chat_id] = [
                t for t in self.chat_bot_message_time[w_info.chat_id] if current_time - t < 60
            ]
            self.chat_bot_message_time[w_info.chat_id].append(current_time)
            if len(self.chat_bot_message_time[w_info.chat_id]) == int(self.fatigue_messages_triggered_num):
                time_interval = 60 - (current_time - self.chat_bot_message_time[w_info.chat_id].pop(0))
                self.chat_fatigue_punishment_list[w_info.chat_id].append((current_time, time_interval * 2))

    async def after_generate_reply_handle(self, message_id: str):
        """回复后处理"""
        async with self.lock:
            w_info = self.ongoing_messages[message_id]
            # 移除关系值相关代码
            # rel_value = await w_info.person_info_manager.get_value(w_info.person_id, "relationship_value")
            # rel_level = self._get_relationship_level_num(rel_value)
            # self.chat_person_reply_willing[w_info.chat_id][w_info.person_id] += rel_level * 0.05

            now_chat_new_person = self.last_response_person.get(w_info.chat_id, (w_info.person_id, 0))
            if now_chat_new_person[0] == w_info.person_id:
                if now_chat_new_person[1] < 3:
                    tmp_list = list(now_chat_new_person)
                    tmp_list[1] += 1  # type: ignore
                    self.last_response_person[w_info.chat_id] = tuple(tmp_list)  # type: ignore
            else:
                self.last_response_person[w_info.chat_id] = (w_info.person_id, 0)

    async def not_reply_handle(self, message_id: str):
        """不回复处理"""
        async with self.lock:
            w_info = self.ongoing_messages[message_id]
            if w_info.is_mentioned_bot:
                self.chat_person_reply_willing[w_info.chat_id][w_info.person_id] += self.mention_willing_gain / 2.5
            if (
                w_info.chat_id in self.last_response_person
                and self.last_response_person[w_info.chat_id][0] == w_info.person_id
                and self.last_response_person[w_info.chat_id][1]
            ):
                self.chat_person_reply_willing[w_info.chat_id][w_info.person_id] += self.single_chat_gain * (
                    2 * self.last_response_person[w_info.chat_id][1] - 1
                )
            now_chat_new_person = self.last_response_person.get(w_info.chat_id, ("", 0))
            if now_chat_new_person[0] != w_info.person_id:
                self.last_response_person[w_info.chat_id] = (w_info.person_id, 0)

    async def get_reply_probability(self, message_id: str):
        # sourcery skip: merge-duplicate-blocks, remove-redundant-if
        """获取回复概率"""
        async with self.lock:
            w_info = self.ongoing_messages[message_id]
            current_willing = self.chat_person_reply_willing[w_info.chat_id][w_info.person_id]
            if self.is_debug:
                self.logger.debug(f"基础意愿值：{current_willing}")

            if w_info.is_mentioned_bot:
                willing_gain = self.mention_willing_gain / (int(current_willing) + 1)
                current_willing += willing_gain
                if self.is_debug:
                    self.logger.debug(f"提及增益：{willing_gain}")

            if w_info.interested_rate > 0:
                willing_gain = math.atan(w_info.interested_rate / 2) / math.pi * 2 * self.interest_willing_gain
                current_willing += willing_gain
                if self.is_debug:
                    self.logger.debug(f"兴趣增益：{willing_gain}")

            self.chat_person_reply_willing[w_info.chat_id][w_info.person_id] = current_willing

            # 添加单聊增益
            if (
                w_info.chat_id in self.last_response_person
                and self.last_response_person[w_info.chat_id][0] == w_info.person_id
                and self.last_response_person[w_info.chat_id][1]
            ):
                current_willing += self.single_chat_gain * (2 * self.last_response_person[w_info.chat_id][1] + 1)
                if self.is_debug:
                    self.logger.debug(
                        f"单聊增益：{self.single_chat_gain * (2 * self.last_response_person[w_info.chat_id][1] + 1)}"
                    )

            current_willing += self.chat_fatigue_willing_attenuation.get(w_info.chat_id, 0)
            if self.is_debug:
                self.logger.debug(f"疲劳衰减：{self.chat_fatigue_willing_attenuation.get(w_info.chat_id, 0)}")

            chat_ongoing_messages = [msg for msg in self.ongoing_messages.values() if msg.chat_id == w_info.chat_id]
            chat_person_ongoing_messages = [msg for msg in chat_ongoing_messages if msg.person_id == w_info.person_id]
            if len(chat_person_ongoing_messages) >= 2:
                current_willing = 0
                if self.is_debug:
                    self.logger.debug("进行中消息惩罚：归0")
            elif len(chat_ongoing_messages) == 2:
                current_willing -= 0.5
                if self.is_debug:
                    self.logger.debug("进行中消息惩罚：-0.5")
            elif len(chat_ongoing_messages) == 3:
                current_willing -= 1.5
                if self.is_debug:
                    self.logger.debug("进行中消息惩罚：-1.5")
            elif len(chat_ongoing_messages) >= 4:
                current_willing = 0
                if self.is_debug:
                    self.logger.debug("进行中消息惩罚：归0")

            probability = self._willing_to_probability(current_willing)

            self.temporary_willing = current_willing

            return probability

    async def _return_to_basic_willing(self):
        """使每个人的意愿恢复到chat基础意愿"""
        while True:
            await asyncio.sleep(3)
            async with self.lock:
                for chat_id, person_willing in self.chat_person_reply_willing.items():
                    for person_id, willing in person_willing.items():
                        if chat_id not in self.chat_reply_willing:
                            self.logger.debug(f"聊天流{chat_id}不存在，错误")
                            continue
                        basic_willing = self.chat_reply_willing[chat_id]
                        person_willing[person_id] = (
                            basic_willing + (willing - basic_willing) * self.intention_decay_rate
                        )

    def setup(self, message: dict, chat_stream: ChatStream):
        super().setup(message, chat_stream)
        stream_id = chat_stream.stream_id
        self.chat_reply_willing[stream_id] = self.chat_reply_willing.get(stream_id, self.basic_maximum_willing)
        self.chat_person_reply_willing[stream_id] = self.chat_person_reply_willing.get(stream_id, {})
        self.chat_person_reply_willing[stream_id][self.ongoing_messages[message.get("message_id", "")].person_id] = (
            self.chat_person_reply_willing[stream_id].get(
                self.ongoing_messages[message.get("message_id", "")].person_id,
                self.chat_reply_willing[stream_id],
            )
        )

        current_time = time.time()
        if stream_id not in self.chat_new_message_time:
            self.chat_new_message_time[stream_id] = []
        self.chat_new_message_time[stream_id].append(current_time)
        if len(self.chat_new_message_time[stream_id]) > self.number_of_message_storage:
            self.chat_new_message_time[stream_id].pop(0)

        if stream_id not in self.chat_fatigue_punishment_list:
            self.chat_fatigue_punishment_list[stream_id] = [
                (
                    current_time,
                    self.number_of_message_storage * self.basic_maximum_willing / self.expected_replies_per_min * 60,
                )
            ]
            self.chat_fatigue_willing_attenuation[stream_id] = (
                -2 * self.basic_maximum_willing * self.fatigue_coefficient
            )

    @staticmethod
    def _willing_to_probability(willing: float) -> float:
        """意愿值转化为概率"""
        willing = max(0, willing)
        if willing < 2:
            return math.atan(willing * 2) / math.pi * 2
        elif willing < 2.5:
            return math.atan(willing * 4) / math.pi * 2
        else:
            return 1

    async def _chat_new_message_to_change_basic_willing(self):
        """聊天流新消息改变基础意愿"""
        update_time = 20
        while True:
            await asyncio.sleep(update_time)
            async with self.lock:
                for chat_id, message_times in self.chat_new_message_time.items():
                    # 清理过期消息
                    current_time = time.time()
                    message_times = [
                        msg_time
                        for msg_time in message_times
                        if current_time - msg_time
                        < self.number_of_message_storage
                        * self.basic_maximum_willing
                        / self.expected_replies_per_min
                        * 60
                    ]
                    self.chat_new_message_time[chat_id] = message_times

                    if len(message_times) < self.number_of_message_storage:
                        self.chat_reply_willing[chat_id] = self.basic_maximum_willing
                        update_time = 20
                    elif len(message_times) == self.number_of_message_storage:
                        time_interval = current_time - message_times[0]
                        basic_willing = self._basic_willing_calculate(time_interval)
                        self.chat_reply_willing[chat_id] = basic_willing
                        update_time = 17 * basic_willing / self.basic_maximum_willing + 3
                    else:
                        self.logger.debug(f"聊天流{chat_id}消息时间数量异常，数量：{len(message_times)}")
                        self.chat_reply_willing[chat_id] = 0
                if self.is_debug:
                    self.logger.debug(f"聊天流意愿值更新：{self.chat_reply_willing}")

    def _basic_willing_calculate(self, t: float) -> float:
        """基础意愿值计算"""
        return math.tan(t * self.expected_replies_per_min * math.pi / 120 / self.number_of_message_storage) / 2

    async def _fatigue_attenuation(self):
        """疲劳衰减"""
        while True:
            await asyncio.sleep(1)
            current_time = time.time()
            async with self.lock:
                for chat_id, fatigue_list in self.chat_fatigue_punishment_list.items():
                    fatigue_list = [z for z in fatigue_list if current_time - z[0] < z[1]]
                    self.chat_fatigue_willing_attenuation[chat_id] = 0
                    for start_time, duration in fatigue_list:
                        self.chat_fatigue_willing_attenuation[chat_id] += (
                            self.chat_reply_willing[chat_id]
                            * 2
                            / math.pi
                            * math.asin(2 * (current_time - start_time) / duration - 1)
                            - self.chat_reply_willing[chat_id]
                        ) * self.fatigue_coefficient

    async def get_willing(self, chat_id):
        return self.temporary_willing
