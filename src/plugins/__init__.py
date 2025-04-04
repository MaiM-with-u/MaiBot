"""
MaiMBot插件系统
包含聊天、情绪、记忆、日程等功能模块
"""

from .chat.chat_stream import chat_manager
from .chat.emoji_manager import emoji_manager
from .person_info.relationship_manager import relationship_manager
from .moods.moods import MoodManager
from .willing.willing_manager import willing_manager
from .schedule.schedule_generator import bot_schedule

# 导出主要组件供外部使用
__all__ = [
    "chat_manager",
    "emoji_manager",
    "relationship_manager",
    "MoodManager",
    "willing_manager",
    "hippocampus",
    "bot_schedule",
]
