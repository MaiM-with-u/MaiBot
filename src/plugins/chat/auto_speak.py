import time
import asyncio
import random
from random import random as random_float
from typing import Dict
from ..config.config import global_config
from .message import MessageSending, MessageThinking, MessageSet, MessageRecv
from .message_base import UserInfo, Seg
from .message_sender import message_manager
from ..moods.moods import MoodManager
from .llm_generator import ResponseGenerator
from src.common.logger import get_module_logger
from src.think_flow_demo.heartflow import subheartflow_manager
from ...common.database import db
logger = get_module_logger("auto_speak")

class AutoSpeakManager:
    def __init__(self):
        self._last_auto_speak_time: Dict[str, float] = {}  # 记录每个聊天流上次自主发言的时间
        self.mood_manager = MoodManager.get_instance()
        self.gpt = ResponseGenerator()  # 添加gpt实例
        self._started = False
        self._check_task = None
        self.db = db

    async def get_chat_info(self, chat_id: str) -> dict:
        """从数据库获取聊天流信息"""
        chat_info = await self.db.chat_streams.find_one({"stream_id": chat_id})
        return chat_info

    async def start_auto_speak_check(self):
        """启动自动发言检查任务"""
        if not self._started:
            self._check_task = asyncio.create_task(self._periodic_check())
            self._started = True
            logger.success("自动发言检查任务已启动")

    async def _periodic_check(self):
        """定期检查是否需要自主发言"""
        while True and global_config.enable_think_flow:
            
            # 获取所有活跃的子心流
            active_subheartflows = []
            for chat_id, subheartflow in subheartflow_manager._subheartflows.items():
                if subheartflow.is_active and subheartflow.current_state.willing > 0:  # 只考虑活跃且意愿值大于0.5的子心流
                    active_subheartflows.append((chat_id, subheartflow))
                    logger.debug(f"发现活跃子心流 - 聊天ID: {chat_id}, 意愿值: {subheartflow.current_state.willing:.2f}")

            if not active_subheartflows:
                logger.debug("当前没有活跃的子心流")
                await asyncio.sleep(20)  # 添加异步等待
                continue

            # 随机选择一个活跃的子心流
            chat_id, subheartflow = random.choice(active_subheartflows)
            logger.info(f"随机选择子心流 - 聊天ID: {chat_id}, 意愿值: {subheartflow.current_state.willing:.2f}")
            
            # 检查是否应该自主发言
            if await self.check_auto_speak(subheartflow):
                logger.info(f"准备自主发言 - 聊天ID: {chat_id}")
                # 生成自主发言
                bot_user_info = UserInfo(
                    user_id=global_config.BOT_QQ,
                    user_nickname=global_config.BOT_NICKNAME,
                    platform="qq",  # 默认使用qq平台
                )
                
                # 创建一个空的MessageRecv对象作为上下文
                message = MessageRecv({
                    "message_info": {
                        "user_info": {
                            "user_id": chat_id,
                            "user_nickname": "",
                            "platform": "qq"
                        },
                        "group_info": None,
                        "platform": "qq",
                        "time": time.time()
                    },
                    "processed_plain_text": "",
                    "raw_message": "",
                    "is_emoji": False
                })
                
                await self.generate_auto_speak(subheartflow, message, bot_user_info, message.message_info["user_info"], message.message_info)
            else:
                logger.debug(f"不满足自主发言条件 - 聊天ID: {chat_id}")
            
            # 每分钟检查一次
            await asyncio.sleep(20)
        
            # await asyncio.sleep(5)  # 发生错误时等待5秒再继续

    async def check_auto_speak(self, subheartflow) -> bool:
        """检查是否应该自主发言"""
        if not subheartflow:
            return False
            
        current_time = time.time()
        chat_id = subheartflow.observe_chat_id
        
        # 获取上次自主发言时间
        if chat_id not in self._last_auto_speak_time:
            self._last_auto_speak_time[chat_id] = 0 
        last_speak_time = self._last_auto_speak_time.get(chat_id, 0)
        
        # 如果距离上次自主发言不到5分钟，不发言
        if current_time - last_speak_time < 30:
            logger.debug(f"距离上次发言时间太短 - 聊天ID: {chat_id}, 剩余时间: {30 - (current_time - last_speak_time):.1f}秒")
            return False
            
        # 获取当前意愿值
        current_willing = subheartflow.current_state.willing
        
        if current_willing > 0.1 and random_float() < 0.5:
            self._last_auto_speak_time[chat_id] = current_time
            logger.info(f"满足自主发言条件 - 聊天ID: {chat_id}, 意愿值: {current_willing:.2f}")
            return True
            
        logger.debug(f"不满足自主发言条件 - 聊天ID: {chat_id}, 意愿值: {current_willing:.2f}")
        return False

    async def generate_auto_speak(self, subheartflow, message, bot_user_info: UserInfo, userinfo, messageinfo):
        """生成自主发言内容"""
        thinking_time_point = round(time.time(), 2)
        think_id = "mt" + str(thinking_time_point)
        thinking_message = MessageThinking(
            message_id=think_id,
            chat_stream=None,  # 不需要chat_stream
            bot_user_info=bot_user_info,
            reply=message,
            thinking_start_time=thinking_time_point,
        )
        
        message_manager.add_message(thinking_message)
        
        # 生成自主发言内容
        response, raw_content = await self.gpt.generate_response(message)
        
        if response:
            message_set = MessageSet(None, think_id)  # 不需要chat_stream
            mark_head = False
            
            for msg in response:
                message_segment = Seg(type="text", data=msg)
                bot_message = MessageSending(
                    message_id=think_id,
                    chat_stream=None,  # 不需要chat_stream
                    bot_user_info=bot_user_info,
                    sender_info=userinfo,
                    message_segment=message_segment,
                    reply=message,
                    is_head=not mark_head,
                    is_emoji=False,
                    thinking_start_time=thinking_time_point,
                )
                if not mark_head:
                    mark_head = True
                message_set.add_message(bot_message)
            
            message_manager.add_message(message_set)
            
            # 更新情绪和关系
            stance, emotion = await self.gpt._get_emotion_tags(raw_content, message.processed_plain_text)
            self.mood_manager.update_mood_from_emotion(emotion, global_config.mood_intensity_factor)
            
            return True
            
        return False

# 创建全局AutoSpeakManager实例
auto_speak_manager = AutoSpeakManager() 