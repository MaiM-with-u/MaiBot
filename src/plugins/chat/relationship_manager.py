import asyncio
from typing import Optional
from src.common.logger import get_module_logger

from ...common.database import db
from .message_base import UserInfo
from .chat_stream import ChatStream
import math

logger = get_module_logger("rel_manager")


class Impression:
    traits: str = None
    called: str = None
    know_time: float = None

    relationship_value: float = None


class Relationship:
    user_id: int = None
    platform: str = None
    gender: str = None
    age: int = None
    nickname: str = None
    relationship_value: float = None
    saved = False

    def __init__(self, chat: ChatStream = None, data: dict = None):
        self.user_id = chat.user_info.user_id if chat else data.get("user_id", 0)
        self.platform = chat.platform if chat else data.get("platform", "")
        self.nickname = chat.user_info.user_nickname if chat else data.get("nickname", "")
        self.relationship_value = data.get("relationship_value", 0) if data else 0
        self.age = data.get("age", 0) if data else 0
        self.gender = data.get("gender", "") if data else ""


class RelationshipManager:
    def __init__(self):
        self.relationships: dict[tuple[int, str], Relationship] = {}  # 修改为使用(user_id, platform)作为键

    async def update_relationship(self, chat_stream: ChatStream, data: dict = None, **kwargs) -> Optional[Relationship]:
        """更新或创建关系
        Args:
            chat_stream: 聊天流对象
            data: 字典格式的数据（可选）
            **kwargs: 其他参数
        Returns:
            Relationship: 关系对象
        """
        # 确定user_id和platform
        if chat_stream.user_info is not None:
            user_id = chat_stream.user_info.user_id
            platform = chat_stream.user_info.platform or "qq"
        else:
            platform = platform or "qq"

        if user_id is None:
            raise ValueError("必须提供user_id或user_info")

        # 使用(user_id, platform)作为键
        key = (user_id, platform)

        # 检查是否在内存中已存在
        relationship = self.relationships.get(key)
        if relationship:
            # 如果存在，更新现有对象
            if isinstance(data, dict):
                for k, value in data.items():
                    if hasattr(relationship, k) and value is not None:
                        setattr(relationship, k, value)
        else:
            # 如果不存在，创建新对象
            if chat_stream.user_info is not None:
                relationship = Relationship(chat=chat_stream, **kwargs)
            else:
                raise ValueError("必须提供user_id或user_info")
            self.relationships[key] = relationship

        # 保存到数据库
        await self.storage_relationship(relationship)
        relationship.saved = True

        return relationship

    async def update_relationship_value(self, chat_stream: ChatStream, **kwargs) -> Optional[Relationship]:
        """更新关系值
        Args:
            user_id: 用户ID（可选，如果提供user_info则不需要）
            platform: 平台（可选，如果提供user_info则不需要）
            user_info: 用户信息对象（可选）
            **kwargs: 其他参数
        Returns:
            Relationship: 关系对象
        """
        # 确定user_id和platform
        user_info = chat_stream.user_info
        if user_info is not None:
            user_id = user_info.user_id
            platform = user_info.platform or "qq"
        else:
            platform = platform or "qq"

        if user_id is None:
            raise ValueError("必须提供user_id或user_info")

        # 使用(user_id, platform)作为键
        key = (user_id, platform)

        # 检查是否在内存中已存在
        relationship = self.relationships.get(key)
        if relationship:
            for k, value in kwargs.items():
                if k == "relationship_value":
                    relationship.relationship_value += value
            await self.storage_relationship(relationship)
            relationship.saved = True
            return relationship
        else:
            # 如果不存在且提供了user_info，则创建新的关系
            if user_info is not None:
                return await self.update_relationship(chat_stream=chat_stream, **kwargs)
            logger.warning(f"[关系管理] 用户 {user_id}({platform}) 不存在，无法更新")
            return None

    def get_relationship(self, chat_stream: ChatStream) -> Optional[Relationship]:
        """获取用户关系对象
        Args:
            user_id: 用户ID（可选，如果提供user_info则不需要）
            platform: 平台（可选，如果提供user_info则不需要）
            user_info: 用户信息对象（可选）
        Returns:
            Relationship: 关系对象
        """
        # 确定user_id和platform
        user_info = chat_stream.user_info
        platform = chat_stream.user_info.platform or "qq"
        if user_info is not None:
            user_id = user_info.user_id
            platform = user_info.platform or "qq"
        else:
            platform = platform or "qq"

        if user_id is None:
            raise ValueError("必须提供user_id或user_info")

        key = (user_id, platform)
        if key in self.relationships:
            return self.relationships[key]
        else:
            return 0

    async def load_relationship(self, data: dict) -> Relationship:
        """从数据库加载或创建新的关系对象"""
        # 确保data中有platform字段，如果没有则默认为'qq'
        if "platform" not in data:
            data["platform"] = "qq"

        rela = Relationship(data=data)
        rela.saved = True
        key = (rela.user_id, rela.platform)
        self.relationships[key] = rela
        return rela

    async def load_all_relationships(self):
        """加载所有关系对象"""
        all_relationships = db.relationships.find({})
        for data in all_relationships:
            await self.load_relationship(data)

    async def _start_relationship_manager(self):
        """每5分钟自动保存一次关系数据"""
        # 获取所有关系记录
        all_relationships = db.relationships.find({})
        # 依次加载每条记录
        for data in all_relationships:
            await self.load_relationship(data)
        logger.debug(f"[关系管理] 已加载 {len(self.relationships)} 条关系记录")

        while True:
            logger.debug("正在自动保存关系")
            await asyncio.sleep(300)  # 等待300秒(5分钟)
            await self._save_all_relationships()

    async def _save_all_relationships(self):
        """将所有关系数据保存到数据库"""
        # 保存所有关系数据
        for _, relationship in self.relationships.items():
            if not relationship.saved:
                relationship.saved = True
                await self.storage_relationship(relationship)

    async def storage_relationship(self, relationship: Relationship):
        """将关系记录存储到数据库中"""
        user_id = relationship.user_id
        platform = relationship.platform
        nickname = relationship.nickname
        relationship_value = relationship.relationship_value
        gender = relationship.gender
        age = relationship.age
        saved = relationship.saved

        db.relationships.update_one(
            {"user_id": user_id, "platform": platform},
            {
                "$set": {
                    "platform": platform,
                    "nickname": nickname,
                    "relationship_value": relationship_value,
                    "gender": gender,
                    "age": age,
                    "saved": saved,
                }
            },
            upsert=True,
        )

    def get_name(self, user_id: int = None, platform: str = None, user_info: UserInfo = None) -> str:
        """获取用户昵称
        Args:
            user_id: 用户ID（可选，如果提供user_info则不需要）
            platform: 平台（可选，如果提供user_info则不需要）
            user_info: 用户信息对象（可选）
        Returns:
            str: 用户昵称
        """
        # 确定user_id和platform
        if user_info is not None:
            user_id = user_info.user_id
            platform = user_info.platform or "qq"
        else:
            platform = platform or "qq"

        if user_id is None:
            raise ValueError("必须提供user_id或user_info")

        # 确保user_id是整数类型
        user_id = int(user_id)
        key = (user_id, platform)
        if key in self.relationships:
            return self.relationships[key].nickname
        elif user_info is not None:
            return user_info.user_nickname or user_info.user_cardname or "某人"
        else:
            return "某人"

    async def calculate_update_relationship_value(self, chat_stream: ChatStream, label: str, stance: str) -> None:
        """计算变更关系值
        新的关系值变更计算方式：
            将关系值限定在-1000到1000
            对于关系值的变更，期望：
                1.向两端逼近时会逐渐减缓
                2.关系越差，改善越难，关系越好，恶化越容易
                3.人维护关系的精力往往有限，所以当高关系值用户越多，对于中高关系值用户增长越慢
        """
        stancedict = {
            "supportive": 0,
            "neutrality": 1,
            "opposed": 2,
        }

        valuedict = {
            "happy": 1.5,
            "angry": -3.0,
            "sad": -1.5,
            "surprised": 0.6,
            "disgusted": -4.5,
            "fearful": -2.1,
            "neutral": 0.3,
        }
        if self.get_relationship(chat_stream):
            old_value = self.get_relationship(chat_stream).relationship_value
        else:
            return

        if old_value > 1000:
            old_value = 1000
        elif old_value < -1000:
            old_value = -1000

        value = valuedict[label]
        if old_value >= 0:
            if valuedict[label] >= 0 and stancedict[stance] != 2:
                value = value * math.cos(math.pi * old_value / 2000)
                if old_value > 500:
                    high_value_count = 0
                    for _, relationship in self.relationships.items():
                        if relationship.relationship_value >= 850:
                            high_value_count += 1
                    value *= 3 / (high_value_count + 3)
            elif valuedict[label] < 0 and stancedict[stance] != 0:
                value = value * math.exp(old_value / 1000)
            else:
                value = 0
        elif old_value < 0:
            if valuedict[label] >= 0 and stancedict[stance] != 2:
                value = value * math.exp(old_value / 1000)
            elif valuedict[label] < 0 and stancedict[stance] != 0:
                value = value * math.cos(math.pi * old_value / 2000)
            else:
                value = 0

        logger.info(f"[关系变更] 立场：{stance}  标签：{label}  关系值：{value}")

        await self.update_relationship_value(chat_stream=chat_stream, relationship_value=value)

    def build_relationship_info(self, person) -> str:
        relationship_value = relationship_manager.get_relationship(person).relationship_value
        if -1000 <= relationship_value < -227:
            level_num = 0
        elif -227 <= relationship_value < -73:
            level_num = 1
        elif -76 <= relationship_value < 227:
            level_num = 2
        elif 227 <= relationship_value < 587:
            level_num = 3
        elif 587 <= relationship_value < 900:
            level_num = 4
        elif 900 <= relationship_value <= 1000:
            level_num = 5
        else:
            level_num = 5 if relationship_value > 1000 else 0

        relationship_level = ["厌恶", "冷漠", "一般", "友好", "喜欢", "暧昧"]
        relation_prompt2_list = [
            "冷漠回应",
            "冷淡回复",
            "保持理性",
            "愿意回复",
            "积极回复",
            "无条件支持",
        ]
        if person.user_info.user_cardname:
            return (
                f"你对昵称为'[({person.user_info.user_id}){person.user_info.user_nickname}]{person.user_info.user_cardname}'的用户的态度为{relationship_level[level_num]}，"
                f"回复态度为{relation_prompt2_list[level_num]}，关系等级为{level_num}。"
            )
        else:
            return (
                f"你对昵称为'({person.user_info.user_id}){person.user_info.user_nickname}'的用户的态度为{relationship_level[level_num]}，"
                f"回复态度为{relation_prompt2_list[level_num]}，关系等级为{level_num}。"
            )


relationship_manager = RelationshipManager()
