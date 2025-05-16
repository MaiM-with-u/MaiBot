from src.common.logger_manager import get_logger
from ..message_receive.chat_stream import ChatStream
import math
from bson.decimal128 import Decimal128
from .person_info import person_info_manager
import time
import random
from typing import List, Dict
from ...common.database import db
from maim_message import UserInfo

from ...manager.mood_manager import mood_manager

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
            logger.info(f"触发mood变更增益，当前增益系数：{self.gain_coefficient[abs(self.positive_feedback_value)]}")

    def mood_feedback(self, value):
        """情绪反馈"""
        mood_manager = self.mood_manager
        mood_gain = mood_manager.current_mood.valence**2 * math.copysign(1, value * mood_manager.current_mood.valence)
        value += value * mood_gain
        logger.info(f"当前relationship增益系数：{mood_gain:.3f}")
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
        is_known = person_info_manager.is_person_known(platform, user_id)
        return is_known

    # --- [修改] 使用全局 db 对象进行查询 ---
    @staticmethod
    async def get_person_names_batch(platform: str, user_ids: List[str]) -> Dict[str, str]:
        """
        批量获取多个用户的 person_name。
        """
        if not user_ids:
            return {}

        person_ids = [person_info_manager.get_person_id(platform, str(uid)) for uid in user_ids]
        names_map = {}
        try:
            cursor = db.person_info.find(
                {"person_id": {"$in": person_ids}},
                {"_id": 0, "person_id": 1, "user_id": 1, "person_name": 1},  # 只查询需要的字段
            )

            for doc in cursor:
                user_id_val = doc.get("user_id")  # 获取原始值
                original_user_id = None  # 初始化

                if isinstance(user_id_val, (int, float)):  # 检查是否是数字类型
                    original_user_id = str(user_id_val)  # 直接转换为字符串
                elif isinstance(user_id_val, str):  # 检查是否是字符串
                    if "_" in user_id_val:  # 如果包含下划线，则分割
                        original_user_id = user_id_val.split("_", 1)[-1]
                    else:  # 如果不包含下划线，则直接使用该字符串
                        original_user_id = user_id_val
                # else: # 其他类型或 None，original_user_id 保持为 None

                person_name = doc.get("person_name")

                # 确保 original_user_id 和 person_name 都有效
                if original_user_id and person_name:
                    names_map[original_user_id] = person_name

            logger.debug(f"批量获取 {len(user_ids)} 个用户的 person_name，找到 {len(names_map)} 个。")
        except AttributeError as e:
            # 如果 db 对象没有 person_info 属性，或者 find 方法不存在
            logger.error(f"访问数据库时出错: {e}。请检查 common/database.py 和集合名称。")
        except Exception as e:
            logger.error(f"批量获取 person_name 时出错: {e}", exc_info=True)
        return names_map

    @staticmethod
    async def get_users_group_nicknames(
        platform: str, user_ids: List[str], group_id: str
    ) -> Dict[str, List[Dict[str, int]]]:
        """
        批量获取多个用户在指定群组的绰号信息。

        Args:
            platform (str): 平台名称。
            user_ids (List[str]): 用户 ID 列表。
            group_id (str): 群组 ID。

        Returns:
            Dict[str, List[Dict[str, int]]]: 映射 {person_name: [{"绰号A": 次数}, ...]}
        """
        if not user_ids or not group_id:
            return {}

        person_ids = [person_info_manager.get_person_id(platform, str(uid)) for uid in user_ids]
        nicknames_data = {}
        group_id_str = str(group_id)  # 确保 group_id 是字符串

        try:
            # 查询包含目标 person_id 的文档
            cursor = db.person_info.find(
                {"person_id": {"$in": person_ids}},
                {"_id": 0, "person_id": 1, "person_name": 1, "group_nicknames": 1},  # 查询所需字段
            )

            # 假设同步迭代可行
            for doc in cursor:
                person_name = doc.get("person_name")
                if not person_name:
                    continue  # 跳过没有 person_name 的用户

                group_nicknames_list = doc.get("group_nicknames", [])  # 获取 group_nicknames 数组
                target_group_nicknames = []  # 存储目标群组的绰号列表

                # 遍历 group_nicknames 数组，查找匹配的 group_id
                for group_entry in group_nicknames_list:
                    # 确保 group_entry 是字典且包含 group_id 键
                    if isinstance(group_entry, dict) and group_entry.get("group_id") == group_id_str:
                        # 提取 nicknames 列表
                        nicknames_raw = group_entry.get("nicknames", [])
                        if isinstance(nicknames_raw, list):
                            target_group_nicknames = nicknames_raw
                        break  # 找到匹配的 group_id 后即可退出内层循环

                # 如果找到了目标群组的绰号列表
                if target_group_nicknames:
                    valid_nicknames_formatted = []  # 存储格式化后的绰号
                    for item in target_group_nicknames:
                        # 校验每个绰号条目的格式 { "name": str, "count": int }
                        if (
                            isinstance(item, dict)
                            and isinstance(item.get("name"), str)
                            and isinstance(item.get("count"), int)
                            and item["count"] > 0
                        ):  # 确保 count 是正整数
                            # --- 格式转换：从 { "name": "xxx", "count": y } 转为 { "xxx": y } ---
                            valid_nicknames_formatted.append({item["name"]: item["count"]})
                            # --- 结束格式转换 ---
                        else:
                            logger.warning(
                                f"数据库中用户 {person_name} 群组 {group_id_str} 的绰号格式无效或 count <= 0: {item}"
                            )

                    if valid_nicknames_formatted:  # 如果存在有效的、格式化后的绰号
                        nicknames_data[person_name] = valid_nicknames_formatted  # 使用 person_name 作为 key

            logger.debug(
                f"批量获取群组 {group_id_str} 中 {len(user_ids)} 个用户的绰号，找到 {len(nicknames_data)} 个用户的数据。"
            )

        except AttributeError as e:
            logger.error(f"访问数据库时出错: {e}。请检查 common/database.py 和集合名称 'person_info'。")
        except Exception as e:
            logger.error(f"批量获取群组绰号时出错: {e}", exc_info=True)

        return nicknames_data

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
            return f"你{relationship_level[level_num]}{person_name}，打算{relation_prompt2_list[level_num]}。\n"
        elif level_num == 2:
            return ""
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
                return f"你{relationship_level[level_num]}{person_name}，打算{relation_prompt2_list[level_num]}。\n"
            else:
                return ""

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
