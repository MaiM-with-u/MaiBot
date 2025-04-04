import time
from random import random
import re

from ...memory_system.Hippocampus import HippocampusManager
from ...moods.moods import MoodManager
from ...config.config import global_config
from ...chat.emoji_manager import emoji_manager
from .think_flow_generator import ResponseGenerator
from ...chat.message import MessageSending, MessageRecv, MessageThinking, MessageSet
from ...chat.message_sender import message_manager
from ...storage.storage import MessageStorage
from ...chat.utils import is_mentioned_bot_in_message, get_recent_group_detailed_plain_text
from ...chat.utils_image import image_path_to_base64
from ...willing.willing_manager import willing_manager
from ...message import UserInfo, Seg
from src.heart_flow.heartflow import heartflow
from src.common.logger import get_module_logger, CHAT_STYLE_CONFIG, LogConfig
from ...chat.chat_stream import chat_manager
from ...person_info.relationship_manager import relationship_manager

# 定义日志配置
chat_config = LogConfig(
    console_format=CHAT_STYLE_CONFIG["console_format"],
    file_format=CHAT_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("think_flow_chat", config=chat_config)

class ThinkFlowChat:
    def __init__(self):
        self.storage = MessageStorage()
        self.gpt = ResponseGenerator()
        self.mood_manager = MoodManager.get_instance()
        self.mood_manager.start_mood_update()

    async def _create_thinking_message(self, message, chat, userinfo, messageinfo):
        """创建思考消息"""
        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=messageinfo.platform,
        )

        thinking_time_point = round(time.time(), 2)
        thinking_id = "mt" + str(thinking_time_point)
        thinking_message = MessageThinking(
            message_id=thinking_id,
            chat_stream=chat,
            bot_user_info=bot_user_info,
            reply=message,
            thinking_start_time=thinking_time_point,
        )

        message_manager.add_message(thinking_message)
        willing_manager.change_reply_willing_sent(chat)

        return thinking_id

    async def _send_response_messages(self, message, chat, response_set, thinking_id):
        """发送回复消息"""
        container = message_manager.get_container(chat.stream_id)
        thinking_message = None

        for msg in container.messages:
            if isinstance(msg, MessageThinking) and msg.message_info.message_id == thinking_id:
                thinking_message = msg
                container.messages.remove(msg)
                break

        if not thinking_message:
            logger.warning("未找到对应的思考消息，可能已超时被移除")
            return

        thinking_start_time = thinking_message.thinking_start_time
        message_set = MessageSet(chat, thinking_id)

        mark_head = False
        for msg in response_set:
            message_segment = Seg(type="text", data=msg)
            bot_message = MessageSending(
                message_id=thinking_id,
                chat_stream=chat,
                bot_user_info=UserInfo(
                    user_id=global_config.BOT_QQ,
                    user_nickname=global_config.BOT_NICKNAME,
                    platform=message.message_info.platform,
                ),
                sender_info=message.message_info.user_info,
                message_segment=message_segment,
                reply=message,
                is_head=not mark_head,
                is_emoji=False,
                thinking_start_time=thinking_start_time,
            )
            if not mark_head:
                mark_head = True
            message_set.add_message(bot_message)
        message_manager.add_message(message_set)

    async def _handle_emoji(self, message, chat, response):
        """处理表情包"""
        if random() < global_config.emoji_chance:
            emoji_raw = await emoji_manager.get_emoji_for_text(response)
            # print("11111111111111")
            # logger.info(emoji_raw)
            if emoji_raw:
                emoji_path, description = emoji_raw
                emoji_cq = image_path_to_base64(emoji_path)
                
                # logger.info(emoji_cq)

                thinking_time_point = round(message.message_info.time, 2)

                message_segment = Seg(type="emoji", data=emoji_cq)
                bot_message = MessageSending(
                    message_id="mt" + str(thinking_time_point),
                    chat_stream=chat,
                    bot_user_info=UserInfo(
                        user_id=global_config.BOT_QQ,
                        user_nickname=global_config.BOT_NICKNAME,
                        platform=message.message_info.platform,
                    ),
                    sender_info=message.message_info.user_info,
                    message_segment=message_segment,
                    reply=message,
                    is_head=False,
                    is_emoji=True,
                )
                
                # logger.info("22222222222222")
                message_manager.add_message(bot_message)

    async def _update_using_response(self, message, response_set):
        """更新心流状态"""
        stream_id = message.chat_stream.stream_id
        chat_talking_prompt = ""
        if stream_id:
            chat_talking_prompt = get_recent_group_detailed_plain_text(
                stream_id, limit=global_config.MAX_CONTEXT_SIZE, combine=True
            )

        await heartflow.get_subheartflow(stream_id).do_thinking_after_reply(response_set, chat_talking_prompt)

    async def _update_relationship(self, message, response_set):
        """更新关系情绪"""
        ori_response = ",".join(response_set)
        stance, emotion = await self.gpt._get_emotion_tags(ori_response, message.processed_plain_text)
        await relationship_manager.calculate_update_relationship_value(
            chat_stream=message.chat_stream, label=emotion, stance=stance
        )
        self.mood_manager.update_mood_from_emotion(emotion, global_config.mood_intensity_factor)

    async def process_message(self, message_data: str) -> None:
        """处理消息并生成回复"""
        timing_results = {}
        response_set = None

        message = MessageRecv(message_data)
        groupinfo = message.message_info.group_info
        userinfo = message.message_info.user_info
        messageinfo = message.message_info


        # 创建聊天流
        chat = await chat_manager.get_or_create_stream(
            platform=messageinfo.platform,
            user_info=userinfo,
            group_info=groupinfo,
        )
        message.update_chat_stream(chat)

        # 创建心流与chat的观察
        heartflow.create_subheartflow(chat.stream_id)

        await message.process()
        logger.debug(f"消息处理成功{message.processed_plain_text}")
        
        # 过滤词/正则表达式过滤
        if self._check_ban_words(message.processed_plain_text, chat, userinfo) or self._check_ban_regex(
            message.raw_message, chat, userinfo
        ):
            return
        logger.debug(f"过滤词/正则表达式过滤成功{message.processed_plain_text}")

        await self.storage.store_message(message, chat)
        logger.debug(f"存储成功{message.processed_plain_text}")
        
        # 记忆激活
        timer1 = time.time()
        interested_rate = await HippocampusManager.get_instance().get_activate_from_text(
            message.processed_plain_text, fast_retrieval=True
        )
        timer2 = time.time()
        timing_results["记忆激活"] = timer2 - timer1
        logger.debug(f"记忆激活: {interested_rate}")

        is_mentioned = is_mentioned_bot_in_message(message)

        # 计算回复意愿
        current_willing_old = willing_manager.get_willing(chat_stream=chat)
        # current_willing_new = (heartflow.get_subheartflow(chat.stream_id).current_state.willing - 5) / 4
        # current_willing = (current_willing_old + current_willing_new) / 2 
        # 有点bug
        current_willing = current_willing_old


        willing_manager.set_willing(chat.stream_id, current_willing)

        # 意愿激活
        timer1 = time.time()
        reply_probability = await willing_manager.change_reply_willing_received(
            chat_stream=chat,
            is_mentioned_bot=is_mentioned,
            config=global_config,
            is_emoji=message.is_emoji,
            interested_rate=interested_rate,
            sender_id=str(message.message_info.user_info.user_id),
        )
        timer2 = time.time()
        timing_results["意愿激活"] = timer2 - timer1
        logger.debug(f"意愿激活: {reply_probability}")

        # 打印消息信息
        mes_name = chat.group_info.group_name if chat.group_info else "私聊"
        current_time = time.strftime("%H:%M:%S", time.localtime(messageinfo.time))
        logger.info(
            f"[{current_time}][{mes_name}]"
            f"{chat.user_info.user_nickname}:"
            f"{message.processed_plain_text}[回复意愿:{current_willing:.2f}][概率:{reply_probability * 100:.1f}%]"
        )

        if message.message_info.additional_config:
            if "maimcore_reply_probability_gain" in message.message_info.additional_config.keys():
                reply_probability += message.message_info.additional_config["maimcore_reply_probability_gain"]

        do_reply = False
        if random() < reply_probability:
            do_reply = True
            
            # 创建思考消息
            timer1 = time.time()
            thinking_id = await self._create_thinking_message(message, chat, userinfo, messageinfo)
            timer2 = time.time()
            timing_results["创建思考消息"] = timer2 - timer1
            
            # 观察
            timer1 = time.time()
            await heartflow.get_subheartflow(chat.stream_id).do_observe()
            timer2 = time.time()
            timing_results["观察"] = timer2 - timer1
            
            # 思考前脑内状态
            timer1 = time.time()
            await heartflow.get_subheartflow(chat.stream_id).do_thinking_before_reply(message.processed_plain_text)
            timer2 = time.time()
            timing_results["思考前脑内状态"] = timer2 - timer1
            
            # 生成回复
            timer1 = time.time()
            response_set = await self.gpt.generate_response(message)
            timer2 = time.time()
            timing_results["生成回复"] = timer2 - timer1

            if not response_set:
                logger.info("为什么生成回复失败？")
                return

            # 发送消息
            timer1 = time.time()
            await self._send_response_messages(message, chat, response_set, thinking_id)
            timer2 = time.time()
            timing_results["发送消息"] = timer2 - timer1

            # 处理表情包
            timer1 = time.time()
            await self._handle_emoji(message, chat, response_set)
            timer2 = time.time()
            timing_results["处理表情包"] = timer2 - timer1

            # 更新心流
            timer1 = time.time()
            await self._update_using_response(message, response_set)
            timer2 = time.time()
            timing_results["更新心流"] = timer2 - timer1

            # 更新关系情绪
            timer1 = time.time()
            await self._update_relationship(message, response_set)
            timer2 = time.time()
            timing_results["更新关系情绪"] = timer2 - timer1

        # 输出性能计时结果
        if do_reply:
            timing_str = " | ".join([f"{step}: {duration:.2f}秒" for step, duration in timing_results.items()])
            trigger_msg = message.processed_plain_text
            response_msg = " ".join(response_set) if response_set else "无回复"
            logger.info(f"触发消息: {trigger_msg[:20]}... | 思维消息: {response_msg[:20]}... | 性能计时: {timing_str}")

    def _check_ban_words(self, text: str, chat, userinfo) -> bool:
        """检查消息中是否包含过滤词"""
        for word in global_config.ban_words:
            if word in text:
                logger.info(
                    f"[{chat.group_info.group_name if chat.group_info else '私聊'}]{userinfo.user_nickname}:{text}"
                )
                logger.info(f"[过滤词识别]消息中含有{word}，filtered")
                return True
        return False

    def _check_ban_regex(self, text: str, chat, userinfo) -> bool:
        """检查消息是否匹配过滤正则表达式"""
        for pattern in global_config.ban_msgs_regex:
            if re.search(pattern, text):
                logger.info(
                    f"[{chat.group_info.group_name if chat.group_info else '私聊'}]{userinfo.user_nickname}:{text}"
                )
                logger.info(f"[正则表达式过滤]消息匹配到{pattern}，filtered")
                return True
        return False
