from ..moods.moods import MoodManager  # 导入情绪管理器
from ..config.config import global_config
from .message import MessageRecv
from ..PFC.pfc import Conversation, ConversationState
from .chat_stream import chat_manager
from ..chat_module.only_process.only_message_process import MessageProcessor

from src.common.logger import get_module_logger, CHAT_STYLE_CONFIG, LogConfig
from ..chat_module.think_flow_chat.think_flow_chat import ThinkFlowChat
from ..chat_module.reasoning_chat.reasoning_chat import ReasoningChat
import asyncio

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
        self.bot = None  # bot 实例引用
        self._started = False
        self.mood_manager = MoodManager.get_instance()  # 获取情绪管理器单例
        self.mood_manager.start_mood_update()  # 启动情绪更新
        self.think_flow_chat = ThinkFlowChat()
        self.reasoning_chat = ReasoningChat()
        self.only_process_chat = MessageProcessor()

    async def _ensure_started(self):
        """确保所有任务已启动"""
        if not self._started:
            self._started = True

    async def _create_PFC_chat(self, message: MessageRecv):
        try:
            chat_id = str(message.chat_stream.stream_id)
            
            if global_config.enable_pfc_chatting:
                # 获取或创建对话实例
                conversation = Conversation.get_instance(chat_id)
                # 如果是新创建的实例，启动对话系统
                if conversation.state == ConversationState.INIT:
                    asyncio.create_task(conversation.start())
                    logger.info(f"为聊天 {chat_id} 创建新的对话实例")
        except Exception as e:
            logger.error(f"创建PFC聊天流失败: {e}")

    async def message_process(self, message_data: str) -> None:
        """处理转化后的统一格式消息
        根据global_config.response_mode选择不同的回复模式：
        1. heart_flow模式：使用思维流系统进行回复
           - 包含思维流状态管理
           - 在回复前进行观察和状态更新
           - 回复后更新思维流状态
        
        2. reasoning模式：使用推理系统进行回复
           - 直接使用意愿管理器计算回复概率
           - 没有思维流相关的状态管理
           - 更简单直接的回复逻辑
        
        3. pfc_chatting模式：仅进行消息处理
           - 不进行任何回复
           - 只处理和存储消息
        
        所有模式都包含：
        - 消息过滤
        - 记忆激活
        - 意愿计算
        - 消息生成和发送
        - 表情包处理
        - 性能计时
        """
        try:
            message = MessageRecv(message_data)
            groupinfo = message.message_info.group_info
            logger.debug(f"处理消息:{str(message_data)[:50]}...")

            if global_config.enable_pfc_chatting:
                try:
                    if groupinfo is None and global_config.enable_friend_chat:
                        userinfo = message.message_info.user_info
                        messageinfo = message.message_info
                        # 创建聊天流
                        chat = await chat_manager.get_or_create_stream(
                            platform=messageinfo.platform,
                            user_info=userinfo,
                            group_info=groupinfo,
                        )
                        message.update_chat_stream(chat)
                        await self.only_process_chat.process_message(message)
                        await self._create_PFC_chat(message)
                    else:
                        if groupinfo.group_id in global_config.talk_allowed_groups:
                            logger.debug(f"开始群聊模式{message_data}")
                            if global_config.response_mode == "heart_flow":
                                await self.think_flow_chat.process_message(message_data)
                            elif global_config.response_mode == "reasoning":
                                logger.debug(f"开始推理模式{message_data}")
                                await self.reasoning_chat.process_message(message_data)
                            else:
                                logger.error(f"未知的回复模式，请检查配置文件！！: {global_config.response_mode}")
                except Exception as e:
                    logger.error(f"处理PFC消息失败: {e}")
            else:
                if groupinfo is None and global_config.enable_friend_chat:
                    # 私聊处理流程 
                    # await self._handle_private_chat(message)
                    if global_config.response_mode == "heart_flow":
                        await self.think_flow_chat.process_message(message_data)
                    elif global_config.response_mode == "reasoning":
                        await self.reasoning_chat.process_message(message_data)
                    else:
                        logger.error(f"未知的回复模式，请检查配置文件！！: {global_config.response_mode}")
                else:  # 群聊处理
                    if groupinfo.group_id in global_config.talk_allowed_groups:
                        if global_config.response_mode == "heart_flow":
                            await self.think_flow_chat.process_message(message_data)
                        elif global_config.response_mode == "reasoning":
                            await self.reasoning_chat.process_message(message_data)
                        else:
                            logger.error(f"未知的回复模式，请检查配置文件！！: {global_config.response_mode}")
        except Exception as e:
            logger.error(f"预处理消息失败: {e}")


# 创建全局ChatBot实例
chat_bot = ChatBot()
