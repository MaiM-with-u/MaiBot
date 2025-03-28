import re
import time
from random import random
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    PrivateMessageEvent,
    GroupMessageEvent,
    NoticeEvent,
    PokeNotifyEvent,
    GroupRecallNoticeEvent,
    FriendRecallNoticeEvent,
)

from ..memory_system.Hippocampus import HippocampusManager
from ..moods.moods import MoodManager  # 导入情绪管理器
from ..config.config import global_config
from .emoji_manager import emoji_manager  # 导入表情包管理器
from .llm_generator import ResponseGenerator
from .message import MessageSending, MessageRecv, MessageThinking, MessageSet
from .message_cq import (
    MessageRecvCQ,
)
from .chat_stream import chat_manager

from .message_sender import message_manager  # 导入新的消息管理器
from .relationship_manager import relationship_manager
from .storage import MessageStorage
from .utils import is_mentioned_bot_in_message, get_recent_group_detailed_plain_text, is_reply_bot_in_message
from .utils_image import image_path_to_base64
from .utils_user import get_user_nickname, get_user_cardname
from ..willing.willing_manager import willing_manager  # 导入意愿管理器
from .message_base import UserInfo, GroupInfo, Seg

from src.think_flow_demo.heartflow import subheartflow_manager
from src.think_flow_demo.outer_world import outer_world

from src.common.logger import get_module_logger, CHAT_STYLE_CONFIG, LogConfig

# 定义日志配置
chat_config = LogConfig(
    # 使用消息发送专用样式
    console_format=CHAT_STYLE_CONFIG["console_format"],
    file_format=CHAT_STYLE_CONFIG["file_format"],
)

# 配置主程序日志格式
logger = get_module_logger("chat_bot", config=chat_config)


class ChatBot:
    def __init__(self):
        self.storage = MessageStorage()
        self.gpt = ResponseGenerator()
        self.bot = None  # bot 实例引用
        self._started = False
        self.mood_manager = MoodManager.get_instance()  # 获取情绪管理器单例
        self.mood_manager.start_mood_update()  # 启动情绪更新

    async def _ensure_started(self):
        """确保所有任务已启动"""
        if not self._started:
            self._started = True

    async def message_process(self, message_cq: MessageRecvCQ) -> None:
        """处理转化后的统一格式消息
        1. 过滤消息
        2. 记忆激活
        3. 意愿激活
        4. 生成回复并发送
        5. 更新关系
        6. 更新情绪
        """
        await message_cq.initialize()
        message_json = message_cq.to_dict()
        # 哦我嘞个json

        # 进入maimbot
        message = MessageRecv(message_json)
        groupinfo = message.message_info.group_info
        userinfo = message.message_info.user_info
        messageinfo = message.message_info

        # 消息过滤，涉及到config有待更新

        # 创建聊天流
        chat = await chat_manager.get_or_create_stream(
            platform=messageinfo.platform,
            user_info=userinfo,
            group_info=groupinfo,  # 我嘞个gourp_info
        )
        message.update_chat_stream(chat)
        
        
        #创建 心流 观察
        if global_config.enable_think_flow:
            await outer_world.check_and_add_new_observe()
            subheartflow_manager.create_subheartflow(chat.stream_id)
        
        
        await relationship_manager.update_relationship(
            chat_stream=chat,
        )
        await relationship_manager.update_relationship_value(chat_stream=chat, relationship_value=0)

        await message.process()

        # 过滤词
        for word in global_config.ban_words:
            if word in message.processed_plain_text:
                logger.info(
                    f"[{chat.group_info.group_name if chat.group_info else '私聊'}]"
                    f"{userinfo.user_nickname}:{message.processed_plain_text}"
                )
                logger.info(f"[过滤词识别]消息中含有{word}，filtered")
                return

        # 正则表达式过滤
        for pattern in global_config.ban_msgs_regex:
            if re.search(pattern, message.raw_message):
                logger.info(
                    f"[{chat.group_info.group_name if chat.group_info else '私聊'}]"
                    f"{userinfo.user_nickname}:{message.raw_message}"
                )
                logger.info(f"[正则表达式过滤]消息匹配到{pattern}，filtered")
                return

        current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(messageinfo.time))

        # 根据话题计算激活度
        topic = ""
        interested_rate = await HippocampusManager.get_instance().get_activate_from_text(
            message.processed_plain_text,fast_retrieval=True)
        # interested_rate = 0.1
        # logger.info(f"对{message.processed_plain_text}的激活度:{interested_rate}")
        # logger.info(f"\033[1;32m[主题识别]\033[0m 使用{global_config.topic_extract}主题: {topic}")

        await self.storage.store_message(message, chat, topic[0] if topic else None)

        # 处理提及
        if (f"[CQ:at,qq={global_config.BOT_QQ}" in message_cq.raw_message) and global_config.at_bot_inevitable_reply:
            reply_probability = 1
            logger.info("被@，回复概率设置为100%")
        else:
            is_reply = is_reply_bot_in_message(message_cq.reply_message)
            if is_reply:
                is_mentioned = True
            else:
                is_mentioned = is_mentioned_bot_in_message(message)
            if is_mentioned and global_config.metioned_bot_inevitable_reply:
                reply_probability = 1
                logger.info("被提及，回复概率设置为100%")
            else:
                reply_probability = await willing_manager.change_reply_willing_received(
                    chat_stream=chat,
                    is_mentioned_bot=is_mentioned,
                    config=global_config,
                    is_emoji=message.is_emoji,
                    interested_rate=interested_rate,
                    sender_id=str(message.message_info.user_info.user_id),
                )
        
        if global_config.enable_think_flow:
            current_willing_old = willing_manager.get_willing(chat_stream=chat)
            current_willing_new = (subheartflow_manager.get_subheartflow(chat.stream_id).current_state.willing-5)/4
            print(f"旧回复意愿：{current_willing_old}，新回复意愿：{current_willing_new}")
            current_willing = (current_willing_old + current_willing_new) / 2
        else:
            current_willing = willing_manager.get_willing(chat_stream=chat)

        logger.info(
            f"[{current_time}][{chat.group_info.group_name if chat.group_info else '私聊'}]"
            f"{chat.user_info.user_nickname}:"
            f"{message.processed_plain_text}[回复意愿:{current_willing:.2f}][概率:{reply_probability * 100:.1f}%]"
        )

        response = None
        # 开始组织语言
        if random() < reply_probability:
            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=messageinfo.platform,
            )
            # 开始思考的时间点
            thinking_time_point = round(time.time(), 2)
            # logger.debug(f"开始思考的时间点: {thinking_time_point}")
            think_id = "mt" + str(thinking_time_point)
            thinking_message = MessageThinking(
                message_id=think_id,
                chat_stream=chat,
                bot_user_info=bot_user_info,
                reply=message,
                thinking_start_time=thinking_time_point,
            )

            message_manager.add_message(thinking_message)

            willing_manager.change_reply_willing_sent(chat)

            response, raw_content = await self.gpt.generate_response(message)
        else:
            # 决定不回复时，也更新回复意愿
            willing_manager.change_reply_willing_not_sent(chat)

        # print(f"response: {response}")
        if response:
            stream_id = message.chat_stream.stream_id
            
            if global_config.enable_think_flow:
                chat_talking_prompt = ""
                if stream_id:
                    chat_talking_prompt = get_recent_group_detailed_plain_text(
                        stream_id, limit=global_config.MAX_CONTEXT_SIZE, combine=True
                    )
                await subheartflow_manager.get_subheartflow(stream_id).do_after_reply(response,chat_talking_prompt)
            
            
            # print(f"有response: {response}")
            container = message_manager.get_container(chat.stream_id)
            thinking_message = None
            # 找到message,删除
            # print(f"开始找思考消息")
            for msg in container.messages:
                if isinstance(msg, MessageThinking) and msg.message_info.message_id == think_id:
                    # print(f"找到思考消息: {msg}")
                    thinking_message = msg
                    container.messages.remove(msg)
                    break

            # 如果找不到思考消息，直接返回
            if not thinking_message:
                logger.warning("未找到对应的思考消息，可能已超时被移除")
                return

            # 记录开始思考的时间，避免从思考到回复的时间太久
            thinking_start_time = thinking_message.thinking_start_time
            message_set = MessageSet(chat, think_id)
            # 计算打字时间，1是为了模拟打字，2是避免多条回复乱序
            # accu_typing_time = 0

            mark_head = False
            for msg in response:
                # print(f"\033[1;32m[回复内容]\033[0m {msg}")
                # 通过时间改变时间戳
                # typing_time = calculate_typing_time(msg)
                # logger.debug(f"typing_time: {typing_time}")
                # accu_typing_time += typing_time
                # timepoint = thinking_time_point + accu_typing_time
                message_segment = Seg(type="text", data=msg)
                # logger.debug(f"message_segment: {message_segment}")
                bot_message = MessageSending(
                    message_id=think_id,
                    chat_stream=chat,
                    bot_user_info=bot_user_info,
                    sender_info=userinfo,
                    message_segment=message_segment,
                    reply=message,
                    is_head=not mark_head,
                    is_emoji=False,
                    thinking_start_time=thinking_start_time,
                )
                if not mark_head:
                    mark_head = True
                message_set.add_message(bot_message)
                if len(str(bot_message)) < 1000:
                    logger.debug(f"bot_message: {bot_message}")
                    logger.debug(f"添加消息到message_set: {bot_message}")
                else:
                    logger.debug(f"bot_message: {str(bot_message)[:1000]}...{str(bot_message)[-10:]}")
                    logger.debug(f"添加消息到message_set: {str(bot_message)[:1000]}...{str(bot_message)[-10:]}")
            # message_set 可以直接加入 message_manager
            # print(f"\033[1;32m[回复]\033[0m 将回复载入发送容器")

            logger.debug("添加message_set到message_manager")

            message_manager.add_message(message_set)

            bot_response_time = thinking_time_point

            if random() < global_config.emoji_chance:
                emoji_raw = await emoji_manager.get_emoji_for_text(response)

                # 检查是否 <没有找到> emoji
                if emoji_raw != None:
                    emoji_path, description = emoji_raw

                    emoji_cq = image_path_to_base64(emoji_path)

                    if random() < 0.5:
                        bot_response_time = thinking_time_point - 1
                    else:
                        bot_response_time = bot_response_time + 1

                    message_segment = Seg(type="emoji", data=emoji_cq)
                    bot_message = MessageSending(
                        message_id=think_id,
                        chat_stream=chat,
                        bot_user_info=bot_user_info,
                        sender_info=userinfo,
                        message_segment=message_segment,
                        reply=message,
                        is_head=False,
                        is_emoji=True,
                    )
                    message_manager.add_message(bot_message)

            # 获取立场和情感标签，更新关系值
            stance, emotion = await self.gpt._get_emotion_tags(raw_content, message.processed_plain_text)
            logger.debug(f"为 '{response}' 立场为：{stance} 获取到的情感标签为：{emotion}")
            await relationship_manager.calculate_update_relationship_value(
                chat_stream=chat, label=emotion, stance=stance
            )

            # 使用情绪管理器更新情绪
            self.mood_manager.update_mood_from_emotion(emotion, global_config.mood_intensity_factor)

    async def handle_notice(self, event: NoticeEvent, bot: Bot) -> None:
        """处理收到的通知"""
        if isinstance(event, PokeNotifyEvent):
            # 戳一戳 通知
            # 不处理其他人的戳戳
            if not event.is_tome():
                return

            # 用户屏蔽,不区分私聊/群聊
            if event.user_id in global_config.ban_user_id:
                return

            # 白名单模式
            if event.group_id:
                if event.group_id not in global_config.talk_allowed_groups:
                    return

            raw_message = f"[戳了戳]{global_config.BOT_NICKNAME}"  # 默认类型
            if info := event.model_extra["raw_info"]:
                poke_type = info[2].get("txt", "戳了戳")  # 戳戳类型，例如"拍一拍"、"揉一揉"、"捏一捏"
                custom_poke_message = info[4].get("txt", "")  # 自定义戳戳消息，若不存在会为空字符串
                raw_message = f"[{poke_type}]{global_config.BOT_NICKNAME}{custom_poke_message}"

                raw_message += "，作为一个类似摸摸头的友善行为"

            user_info = UserInfo(
                user_id=event.user_id,
                user_nickname=(await bot.get_stranger_info(user_id=event.user_id, no_cache=True))["nickname"],
                user_cardname=None,
                platform="qq",
            )

            if event.group_id:
                group_info = GroupInfo(group_id=event.group_id, group_name=None, platform="qq")
            else:
                group_info = None

            message_cq = MessageRecvCQ(
                message_id=0,
                user_info=user_info,
                raw_message=str(raw_message),
                group_info=group_info,
                reply_message=None,
                platform="qq",
            )

            await self.message_process(message_cq)

        elif isinstance(event, GroupRecallNoticeEvent) or isinstance(event, FriendRecallNoticeEvent):
            user_info = UserInfo(
                user_id=event.user_id,
                user_nickname=get_user_nickname(event.user_id) or None,
                user_cardname=get_user_cardname(event.user_id) or None,
                platform="qq",
            )

            if isinstance(event, GroupRecallNoticeEvent):
                group_info = GroupInfo(group_id=event.group_id, group_name=None, platform="qq")
            else:
                group_info = None

            chat = await chat_manager.get_or_create_stream(
                platform=user_info.platform, user_info=user_info, group_info=group_info
            )

            await self.storage.store_recalled_message(event.message_id, time.time(), chat)

    async def handle_message(self, event: MessageEvent, bot: Bot) -> None:
        """处理收到的消息"""

        self.bot = bot  # 更新 bot 实例

        # 用户屏蔽,不区分私聊/群聊
        if event.user_id in global_config.ban_user_id:
            return

        if (
            event.reply
            and hasattr(event.reply, "sender")
            and hasattr(event.reply.sender, "user_id")
            and event.reply.sender.user_id in global_config.ban_user_id
        ):
            logger.debug(f"跳过处理回复来自被ban用户 {event.reply.sender.user_id} 的消息")
            return
        # 处理私聊消息
        if isinstance(event, PrivateMessageEvent):
            if not global_config.enable_friend_chat:  # 私聊过滤
                return
            else:
                try:
                    user_info = UserInfo(
                        user_id=event.user_id,
                        user_nickname=(await bot.get_stranger_info(user_id=event.user_id, no_cache=True))["nickname"],
                        user_cardname=None,
                        platform="qq",
                    )
                except Exception as e:
                    logger.error(f"获取陌生人信息失败: {e}")
                    return
                logger.debug(user_info)

                # group_info = GroupInfo(group_id=0, group_name="私聊", platform="qq")
                group_info = None

        # 处理群聊消息
        else:
            # 白名单设定由nontbot侧完成
            if event.group_id:
                if event.group_id not in global_config.talk_allowed_groups:
                    return

            user_info = UserInfo(
                user_id=event.user_id,
                user_nickname=event.sender.nickname,
                user_cardname=event.sender.card or None,
                platform="qq",
            )

            group_info = GroupInfo(group_id=event.group_id, group_name=None, platform="qq")

        # group_info = await bot.get_group_info(group_id=event.group_id)
        # sender_info = await bot.get_group_member_info(group_id=event.group_id, user_id=event.user_id, no_cache=True)

        message_cq = MessageRecvCQ(
            message_id=event.message_id,
            user_info=user_info,
            raw_message=str(event.original_message),
            group_info=group_info,
            reply_message=event.reply,
            platform="qq",
        )

        await self.message_process(message_cq)

    async def handle_forward_message(self, event: MessageEvent, bot: Bot) -> None:
        """专用于处理合并转发的消息处理器"""

        # 用户屏蔽,不区分私聊/群聊
        if event.user_id in global_config.ban_user_id:
            return

        if isinstance(event, GroupMessageEvent):
            if event.group_id:
                if event.group_id not in global_config.talk_allowed_groups:
                    return

        # 获取合并转发消息的详细信息
        forward_info = await bot.get_forward_msg(message_id=event.message_id)
        messages = forward_info["messages"]

        # 构建合并转发消息的文本表示
        processed_messages = []
        for node in messages:
            # 提取发送者昵称
            nickname = node["sender"].get("nickname", "未知用户")

            # 递归处理消息内容
            message_content = await self.process_message_segments(node["message"], layer=0)

            # 拼接为【昵称】+ 内容
            processed_messages.append(f"【{nickname}】{message_content}")

        # 组合所有消息
        combined_message = "\n".join(processed_messages)
        combined_message = f"合并转发消息内容：\n{combined_message}"

        # 构建用户信息（使用转发消息的发送者）
        user_info = UserInfo(
            user_id=event.user_id,
            user_nickname=event.sender.nickname,
            user_cardname=event.sender.card if hasattr(event.sender, "card") else None,
            platform="qq",
        )

        # 构建群聊信息（如果是群聊）
        group_info = None
        if isinstance(event, GroupMessageEvent):
            group_info = GroupInfo(group_id=event.group_id, group_name=None, platform="qq")

        # 创建消息对象
        message_cq = MessageRecvCQ(
            message_id=event.message_id,
            user_info=user_info,
            raw_message=combined_message,
            group_info=group_info,
            reply_message=event.reply,
            platform="qq",
        )

        # 进入标准消息处理流程
        await self.message_process(message_cq)

    async def process_message_segments(self, segments: list, layer: int) -> str:
        """递归处理消息段"""
        parts = []
        for seg in segments:
            part = await self.process_segment(seg, layer + 1)
            parts.append(part)
        return "".join(parts)

    async def process_segment(self, seg: dict, layer: int) -> str:
        """处理单个消息段"""
        seg_type = seg["type"]
        if layer > 3:
            # 防止有那种100层转发消息炸飞麦麦
            return "【转发消息】"
        if seg_type == "text":
            return seg["data"]["text"]
        elif seg_type == "image":
            return "[图片]"
        elif seg_type == "face":
            return "[表情]"
        elif seg_type == "at":
            return f"@{seg['data'].get('qq', '未知用户')}"
        elif seg_type == "forward":
            # 递归处理嵌套的合并转发消息
            nested_nodes = seg["data"].get("content", [])
            nested_messages = []
            nested_messages.append("合并转发消息内容：")
            for node in nested_nodes:
                nickname = node["sender"].get("nickname", "未知用户")
                content = await self.process_message_segments(node["message"], layer=layer)
                # nested_messages.append('-' * layer)
                nested_messages.append(f"{'--' * layer}【{nickname}】{content}")
            # nested_messages.append(f"{'--' * layer}合并转发第【{layer}】层结束")
            return "\n".join(nested_messages)
        else:
            return f"[{seg_type}]"


# 创建全局ChatBot实例
chat_bot = ChatBot()
