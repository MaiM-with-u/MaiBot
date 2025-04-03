import asyncio
import hashlib
import time
import copy
from typing import Dict, Optional


from ...common.database import db
from ..message.message_base import GroupInfo, UserInfo

from src.common.logger import get_module_logger

logger = get_module_logger("chat_stream")


class ChatStream:
    """聊天流对象，存储一个完整的聊天上下文"""

    def __init__(
        self,
        stream_id: str,
        platform: str,
        user_info: UserInfo,
        group_info: Optional[GroupInfo] = None,
        data: dict = None,
    ):
        self.stream_id = stream_id
        self.platform = platform
        self.user_info = user_info
        self.group_info = group_info
        self.create_time = data.get("create_time", int(time.time())) if data else int(time.time())
        self.last_active_time = data.get("last_active_time", self.create_time) if data else self.create_time
        self.saved = False

    def to_dict(self) -> dict:
        """转换为字典格式"""
        result = {
            "stream_id": self.stream_id,
            "platform": self.platform,
            "user_info": self.user_info.to_dict() if self.user_info else None,
            "group_info": self.group_info.to_dict() if self.group_info else None,
            "create_time": self.create_time,
            "last_active_time": self.last_active_time,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ChatStream":
        """从字典创建实例"""
        user_info = UserInfo.from_dict(data.get("user_info", {})) if data.get("user_info") else None
        group_info = GroupInfo.from_dict(data.get("group_info", {})) if data.get("group_info") else None

        return cls(
            stream_id=data["stream_id"],
            platform=data["platform"],
            user_info=user_info,
            group_info=group_info,
            data=data,
        )

    def update_active_time(self):
        """更新最后活跃时间"""
        self.last_active_time = int(time.time())
        self.saved = False


class ChatManager:
    """聊天管理器，管理所有聊天流"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.streams: Dict[str, ChatStream] = {}  # stream_id -> ChatStream
            self._ensure_collection()
            self._initialized = True
            # 在事件循环中启动初始化
            # asyncio.create_task(self._initialize())
            # # 启动自动保存任务
            # asyncio.create_task(self._auto_save_task())

    async def _initialize(self):
        """异步初始化"""
        try:
            await self.load_all_streams()
            logger.success(f"聊天管理器已启动，已加载 {len(self.streams)} 个聊天流")
        except Exception as e:
            logger.error(f"聊天管理器启动失败: {str(e)}")

    async def _auto_save_task(self):
        """定期自动保存所有聊天流"""
        while True:
            await asyncio.sleep(300)  # 每5分钟保存一次
            try:
                await self._save_all_streams()
                logger.info("聊天流自动保存完成")
            except Exception as e:
                logger.error(f"聊天流自动保存失败: {str(e)}")

    def _ensure_collection(self):
        """确保数据库集合存在并创建索引"""
        if "chat_streams" not in db.list_collection_names():
            db.create_collection("chat_streams")
            # 创建索引
            db.chat_streams.create_index([("stream_id", 1)], unique=True)
            db.chat_streams.create_index([("platform", 1), ("user_info.user_id", 1), ("group_info.group_id", 1)])

    def _generate_stream_id(self, platform: str, user_info: UserInfo, group_info: Optional[GroupInfo] = None) -> str:
        """生成聊天流唯一ID"""
        if group_info:
            # 组合关键信息
            components = [platform, str(group_info.group_id)]
        else:
            components = [platform, str(user_info.user_id), "private"]

        # 使用MD5生成唯一ID
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    async def get_or_create_stream(
        self, platform: str, user_info: UserInfo, group_info: Optional[GroupInfo] = None
    ) -> ChatStream:
        """获取或创建聊天流

        Args:
            platform: 平台标识
            user_info: 用户信息
            group_info: 群组信息（可选）

        Returns:
            ChatStream: 聊天流对象
        """
        # 生成stream_id
        try:
            stream_id = self._generate_stream_id(platform, user_info, group_info)

            # 检查内存中是否存在
            if stream_id in self.streams:
                stream = self.streams[stream_id]
                # 更新用户信息和群组信息
                stream.update_active_time()
                stream = copy.deepcopy(stream)
                stream.user_info = user_info
                if group_info:
                    stream.group_info = group_info
                return stream

            # 检查数据库中是否存在
            data = db.chat_streams.find_one({"stream_id": stream_id})
            if data:
                stream = ChatStream.from_dict(data)
                # 更新用户信息和群组信息
                stream.user_info = user_info
                if group_info:
                    stream.group_info = group_info
                stream.update_active_time()
            else:
                # 创建新的聊天流
                stream = ChatStream(
                    stream_id=stream_id,
                    platform=platform,
                    user_info=user_info,
                    group_info=group_info,
                )
        except Exception as e:
            logger.error(f"创建聊天流失败: {e}")
            raise e

        # 保存到内存和数据库
        self.streams[stream_id] = stream
        await self._save_stream(stream)
        return copy.deepcopy(stream)

    def get_stream(self, stream_id: str) -> Optional[ChatStream]:
        """通过stream_id获取聊天流"""
        return self.streams.get(stream_id)

    def get_stream_by_info(
        self, platform: str, user_info: UserInfo, group_info: Optional[GroupInfo] = None
    ) -> Optional[ChatStream]:
        """通过信息获取聊天流"""
        stream_id = self._generate_stream_id(platform, user_info, group_info)
        return self.streams.get(stream_id)

    async def _save_stream(self, stream: ChatStream):
        """保存聊天流到数据库"""
        if not stream.saved:
            db.chat_streams.update_one({"stream_id": stream.stream_id}, {"$set": stream.to_dict()}, upsert=True)
            stream.saved = True

    async def _save_all_streams(self):
        """保存所有聊天流"""
        for stream in self.streams.values():
            await self._save_stream(stream)

    async def load_all_streams(self):
        """从数据库加载所有聊天流"""
        all_streams = db.chat_streams.find({})
        for data in all_streams:
            stream = ChatStream.from_dict(data)
            self.streams[stream.stream_id] = stream


# 创建全局单例
chat_manager = ChatManager()
