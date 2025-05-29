from src.common.logger_manager import get_logger
from src.chat.message_receive.chat_stream import ChatStream
import math
from bson.decimal128 import Decimal128
from src.person_info.person_info import person_info_manager
import time
import random
from maim_message import UserInfo

from src.manager.mood_manager import mood_manager

# import re
# import traceback


logger = get_logger("relation")


class RelationshipManager:
    def __init__(self):
        self.positive_feedback_value = 0  # 正反馈系统
        self.gain_coefficient = [1.0, 1.0, 1.1, 1.2, 1.4, 1.7, 1.9, 2.0]
        self._mood_manager = None

    @property
    def mood_manager(self):
        if self._mood_manager is None:
            self._mood_manager = mood_manager
        return self._mood_manager

    def positive_feedback_sys(self, label: str, stance: str):
        """正反馈系统，通过正反馈系数增益情绪变化，根据情绪再影响关系变更"""

        positive_list = [
            "开心",
            "惊讶",
            "害羞",
        ]

        negative_list = [
            "愤怒",
            "悲伤",
            "恐惧",
            "厌恶",
        ]

        if label in positive_list:
            if 7 > self.positive_feedback_value >= 0:
                self.positive_feedback_value += 1
            elif self.positive_feedback_value < 0:
                self.positive_feedback_value = 0
        elif label in negative_list:
            if -7 < self.positive_feedback_value <= 0:
                self.positive_feedback_value -= 1
            elif self.positive_feedback_value > 0:
                self.positive_feedback_value = 0

        if abs(self.positive_feedback_value) > 1:
            logger.debug(f"触发mood变更增益，当前增益系数：{self.gain_coefficient[abs(self.positive_feedback_value)]}")

    def mood_feedback(self, value):
        """情绪反馈"""
        mood_manager = self.mood_manager
        mood_gain = mood_manager.current_mood.valence**2 * math.copysign(1, value * mood_manager.current_mood.valence)
        value += value * mood_gain
        logger.debug(f"当前relationship增益系数：{mood_gain:.3f}")
        return value

    def feedback_to_mood(self, mood_value):
        """对情绪的反馈"""
        coefficient = self.gain_coefficient[abs(self.positive_feedback_value)]
        if mood_value > 0 and self.positive_feedback_value > 0 or mood_value < 0 and self.positive_feedback_value < 0:
            return mood_value * coefficient
        else:
            return mood_value / coefficient

    @staticmethod
    async def is_known_some_one(platform, user_id):
        """判断是否认识某人"""
        is_known = await person_info_manager.is_person_known(platform, user_id)
        return is_known

    @staticmethod
    async def is_qved_name(platform, user_id):
        """判断是否认识某人"""
        person_id = person_info_manager.get_person_id(platform, user_id)
        is_qved = await person_info_manager.has_one_field(person_id, "person_name")
        old_name = await person_info_manager.get_value(person_id, "person_name")
        # print(f"old_name: {old_name}")
        # print(f"is_qved: {is_qved}")
        if is_qved and old_name is not None:
            return True
        else:
            return False

    @staticmethod
    async def first_knowing_some_one(
        platform: str, user_id: str, user_nickname: str, user_cardname: str, user_avatar: str
    ):
        """判断是否认识某人"""
        person_id = person_info_manager.get_person_id(platform, user_id)
        data = {
            "platform": platform,
            "user_id": user_id,
            "nickname": user_nickname,
            "konw_time": int(time.time()),
        }
        await person_info_manager.update_one_field(
            person_id=person_id, field_name="nickname", value=user_nickname, data=data
        )
        await person_info_manager.qv_person_name(
            person_id=person_id, user_nickname=user_nickname, user_cardname=user_cardname, user_avatar=user_avatar
        )

    async def calculate_update_relationship_value(self, user_info: UserInfo, platform: str, label: str, stance: str):
        """计算并变更关系值
        新的关系值变更计算方式：
            将关系值限定在-1000到1000
            对于关系值的变更，期望：
                1.向两端逼近时会逐渐减缓
                2.关系越差，改善越难，关系越好，恶化越容易
                3.人维护关系的精力往往有限，所以当高关系值用户越多，对于中高关系值用户增长越慢
                4.连续正面或负面情感会正反馈

        返回：
            用户昵称，变更值，变更后关系等级

        """
        stancedict = {
            "支持": 0,
            "中立": 1,
            "反对": 2,
        }

        valuedict = {
            "开心": 1.5,
            "愤怒": -2.0,
            "悲伤": -0.5,
            "惊讶": 0.6,
            "害羞": 2.0,
            "平静": 0.3,
            "恐惧": -1.5,
            "厌恶": -1.0,
            "困惑": 0.5,
        }

        person_id = person_info_manager.get_person_id(platform, user_info.user_id)
        data = {
            "platform": platform,
            "user_id": user_info.user_id,
            "nickname": user_info.user_nickname,
            "konw_time": int(time.time()),
        }
        old_value = await person_info_manager.get_value(person_id, "relationship_value")
        old_value = self.ensure_float(old_value, person_id)

        if old_value > 1000:
            old_value = 1000
        elif old_value < -1000:
            old_value = -1000

        value = valuedict[label]
        if old_value >= 0:
            if valuedict[label] >= 0 and stancedict[stance] != 2:
                value = value * math.cos(math.pi * old_value / 2000)
                if old_value > 500:
                    rdict = await person_info_manager.get_specific_value_list("relationship_value", lambda x: x > 700)
                    high_value_count = len(rdict)
                    if old_value > 700:
                        value *= 3 / (high_value_count + 2)  # 排除自己
                    else:
                        value *= 3 / (high_value_count + 3)
            elif valuedict[label] < 0 and stancedict[stance] != 0:
                value = value * math.exp(old_value / 2000)
            else:
                value = 0
        elif old_value < 0:
            if valuedict[label] >= 0 and stancedict[stance] != 2:
                value = value * math.exp(old_value / 2000)
            elif valuedict[label] < 0 and stancedict[stance] != 0:
                value = value * math.cos(math.pi * old_value / 2000)
            else:
                value = 0

        self.positive_feedback_sys(label, stance)
        value = self.mood_feedback(value)

        level_num = self.calculate_level_num(old_value + value)
        relationship_level = ["厌恶", "冷漠", "一般", "友好", "喜欢", "暧昧"]
        logger.info(
            f"用户: {user_info.user_nickname}"
            f"当前关系: {relationship_level[level_num]}, "
            f"关系值: {old_value:.2f}, "
            f"当前立场情感: {stance}-{label}, "
            f"变更: {value:+.5f}"
        )

        await person_info_manager.update_one_field(person_id, "relationship_value", old_value + value, data)

    async def calculate_update_relationship_value_with_reason(
        self, chat_stream: ChatStream, label: str, stance: str, reason: str
    ) -> tuple:
        """计算并变更关系值
        新的关系值变更计算方式：
            将关系值限定在-1000到1000
            对于关系值的变更，期望：
                1.向两端逼近时会逐渐减缓
                2.关系越差，改善越难，关系越好，恶化越容易
                3.人维护关系的精力往往有限，所以当高关系值用户越多，对于中高关系值用户增长越慢
                4.连续正面或负面情感会正反馈

        返回：
            用户昵称，变更值，变更后关系等级

        """
        stancedict = {
            "支持": 0,
            "中立": 1,
            "反对": 2,
        }

        valuedict = {
            "开心": 1.5,
            "愤怒": -2.0,
            "悲伤": -0.5,
            "惊讶": 0.6,
            "害羞": 2.0,
            "平静": 0.3,
            "恐惧": -1.5,
            "厌恶": -1.0,
            "困惑": 0.5,
        }

        person_id = person_info_manager.get_person_id(chat_stream.user_info.platform, chat_stream.user_info.user_id)
        data = {
            "platform": chat_stream.user_info.platform,
            "user_id": chat_stream.user_info.user_id,
            "nickname": chat_stream.user_info.user_nickname,
            "konw_time": int(time.time()),
        }
        old_value = await person_info_manager.get_value(person_id, "relationship_value")
        old_value = self.ensure_float(old_value, person_id)

        if old_value > 1000:
            old_value = 1000
        elif old_value < -1000:
            old_value = -1000

        value = valuedict[label]
        if old_value >= 0:
            if valuedict[label] >= 0 and stancedict[stance] != 2:
                value = value * math.cos(math.pi * old_value / 2000)
                if old_value > 500:
                    rdict = await person_info_manager.get_specific_value_list("relationship_value", lambda x: x > 700)
                    high_value_count = len(rdict)
                    if old_value > 700:
                        value *= 3 / (high_value_count + 2)  # 排除自己
                    else:
                        value *= 3 / (high_value_count + 3)
            elif valuedict[label] < 0 and stancedict[stance] != 0:
                value = value * math.exp(old_value / 2000)
            else:
                value = 0
        elif old_value < 0:
            if valuedict[label] >= 0 and stancedict[stance] != 2:
                value = value * math.exp(old_value / 2000)
            elif valuedict[label] < 0 and stancedict[stance] != 0:
                value = value * math.cos(math.pi * old_value / 2000)
            else:
                value = 0

        self.positive_feedback_sys(label, stance)
        value = self.mood_feedback(value)

        level_num = self.calculate_level_num(old_value + value)
        relationship_level = ["厌恶", "冷漠", "一般", "友好", "喜欢", "暧昧"]
        logger.info(
            f"用户: {chat_stream.user_info.user_nickname}"
            f"当前关系: {relationship_level[level_num]}, "
            f"关系值: {old_value:.2f}, "
            f"当前立场情感: {stance}-{label}, "
            f"变更: {value:+.5f}"
        )

        await person_info_manager.update_one_field(person_id, "relationship_value", old_value + value, data)

        return chat_stream.user_info.user_nickname, value, relationship_level[level_num]

    async def build_relationship_info(self, person, is_id: bool = False) -> str:
        if is_id:
            person_id = person
        else:
            # print(f"person: {person}")
            person_id = person_info_manager.get_person_id(person[0], person[1])
        person_name = await person_info_manager.get_value(person_id, "person_name")
        # print(f"person_name: {person_name}")
        relationship_value = await person_info_manager.get_value(person_id, "relationship_value")
        level_num = self.calculate_level_num(relationship_value)

        relation_value_prompt = ""

        if level_num == 0 or level_num == 5:
            relationship_level = ["厌恶", "冷漠以对", "认识", "友好对待", "喜欢", "暧昧"]
            relation_prompt2_list = [
                "忽视的回应",
                "冷淡回复",
                "保持理性",
                "愿意回复",
                "积极回复",
                "友善和包容的回复",
            ]
            relation_value_prompt = (
                f"你{relationship_level[level_num]}{person_name}，打算{relation_prompt2_list[level_num]}。"
            )
        elif level_num == 2:
            relation_value_prompt = ""
        else:
            if random.random() < 0.6:
                relationship_level = ["厌恶", "冷漠以对", "认识", "友好对待", "喜欢", "暧昧"]
                relation_prompt2_list = [
                    "忽视的回应",
                    "冷淡回复",
                    "保持理性",
                    "愿意回复",
                    "积极回复",
                    "友善和包容的回复",
                ]
                relation_value_prompt = (
                    f"你{relationship_level[level_num]}{person_name}，打算{relation_prompt2_list[level_num]}。"
                )
            else:
                relation_value_prompt = ""

        if relation_value_prompt:
            nickname_str = await person_info_manager.get_value(person_id, "nickname")
            platform = await person_info_manager.get_value(person_id, "platform")
            relation_prompt = f"{relation_value_prompt}，ta在{platform}上的昵称是{nickname_str}。\n"
        else:
            relation_prompt = ""

        return relation_prompt

    @staticmethod
    def calculate_level_num(relationship_value) -> int:
        """关系等级计算"""
        if -1000 <= relationship_value < -227:
            level_num = 0
        elif -227 <= relationship_value < -73:
            level_num = 1
        elif -73 <= relationship_value < 227:
            level_num = 2
        elif 227 <= relationship_value < 587:
            level_num = 3
        elif 587 <= relationship_value < 900:
            level_num = 4
        elif 900 <= relationship_value <= 1000:
            level_num = 5
        else:
            level_num = 5 if relationship_value > 1000 else 0
        return level_num

    @staticmethod
    def ensure_float(value, person_id):
        """确保返回浮点数，转换失败返回0.0"""
        if isinstance(value, float):
            return value
        try:
            return float(value.to_decimal() if isinstance(value, Decimal128) else value)
        except (ValueError, TypeError, AttributeError):
            logger.warning(f"[关系管理] {person_id}值转换失败（原始值：{value}），已重置为0")
            return 0.0


relationship_manager = RelationshipManager()
