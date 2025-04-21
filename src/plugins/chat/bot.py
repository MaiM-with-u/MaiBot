from ..moods.moods import MoodManager  # 导入情绪管理器
from ...config.config import global_config
from .message import MessageRecv
from ..PFC.pfc_manager import PFCManager
from .chat_stream import chat_manager
from ..chat_module.only_process.only_message_process import MessageProcessor

from src.common.logger import get_module_logger, CHAT_STYLE_CONFIG, LogConfig
from ..chat_module.reasoning_chat.reasoning_chat import ReasoningChat
from ..chat_module.heartFC_chat.heartFC_processor import HeartFCProcessor
from ..utils.prompt_builder import Prompt, global_prompt_manager
import traceback

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
        self.reasoning_chat = ReasoningChat()
        self.heartFC_processor = HeartFCProcessor()  # 新增

        # 创建初始化PFC管理器的任务，会在_ensure_started时执行
        self.only_process_chat = MessageProcessor()
        self.pfc_manager = PFCManager.get_instance()

    async def _ensure_started(self):
        """确保所有任务已启动"""
        if not self._started:
            logger.trace("确保ChatBot所有任务已启动")

            self._started = True

    async def _create_pfc_chat(self, message: MessageRecv):
        try:
            chat_id = str(message.chat_stream.stream_id)

            if global_config.enable_pfc_chatting:
                await self.pfc_manager.get_or_create_conversation(chat_id)

        except Exception as e:
            logger.error(f"创建PFC聊天失败: {e}")

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

        所有模式都包含：
        - 消息过滤
        - 记忆激活
        - 意愿计算
        - 消息生成和发送
        - 表情包处理
        - 性能计时
        """
        try:
            # 确保所有任务已启动
            await self._ensure_started()

            if message_data["message_info"].get("group_info") is not None:
                message_data["message_info"]["group_info"]["group_id"] = str(
                    message_data["message_info"]["group_info"]["group_id"]
                )
            message_data["message_info"]["user_info"]["user_id"] = str(
                message_data["message_info"]["user_info"]["user_id"]
            )
            logger.trace(f"处理消息:{str(message_data)[:120]}...")
            message = MessageRecv(message_data)
            groupinfo = message.message_info.group_info
            userinfo = message.message_info.user_info

            if userinfo.user_id in global_config.ban_user_id:
                logger.debug(f"用户{userinfo.user_id}被禁止回复")
                return

            if message.message_info.template_info and not message.message_info.template_info.template_default:
                template_group_name = message.message_info.template_info.template_name
                template_items = message.message_info.template_info.template_items
                async with global_prompt_manager.async_message_scope(template_group_name):
                    if isinstance(template_items, dict):
                        for k in template_items.keys():
                            await Prompt.create_async(template_items[k], k)
                            print(f"注册{template_items[k]},{k}")
            else:
                template_group_name = None

            async def preprocess():
                if global_config.enable_pfc_chatting:
                    try:
                        if groupinfo is None:
                            if global_config.enable_friend_chat:
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
                                await self._create_pfc_chat(message)
                        else:
                            if groupinfo.group_id in global_config.talk_allowed_groups:
                                # logger.debug(f"开始群聊模式{str(message_data)[:50]}...")
                                if global_config.response_mode == "heart_flow":
                                    # logger.info(f"启动最新最好的思维流FC模式{str(message_data)[:50]}...")
                                    await self.heartFC_processor.process_message(message_data)
                                elif global_config.response_mode == "reasoning":
                                    # logger.debug(f"开始推理模式{str(message_data)[:50]}...")
                                    await self.reasoning_chat.process_message(message_data)
                                else:
                                    logger.error(f"未知的回复模式，请检查配置文件！！: {global_config.response_mode}")
                    except Exception as e:
                        logger.error(f"处理PFC消息失败: {e}")
                else:
                    if groupinfo is None:
                        if global_config.enable_friend_chat:
                            # 私聊处理流程
                            # await self._handle_private_chat(message)
                            if global_config.response_mode == "heart_flow":
                                await self.heartFC_processor.process_message(message_data)
                            elif global_config.response_mode == "reasoning":
                                await self.reasoning_chat.process_message(message_data)
                            else:
                                logger.error(f"未知的回复模式，请检查配置文件！！: {global_config.response_mode}")
                    else:  # 群聊处理
                        if groupinfo.group_id in global_config.talk_allowed_groups:
                            if global_config.response_mode == "heart_flow":
                                await self.heartFC_processor.process_message(message_data)
                            elif global_config.response_mode == "reasoning":
                                await self.reasoning_chat.process_message(message_data)
                            else:
                                logger.error(f"未知的回复模式，请检查配置文件！！: {global_config.response_mode}")

            if template_group_name:
                async with global_prompt_manager.async_message_scope(template_group_name):
                    await preprocess()
            else:
                await preprocess()

        except Exception as e:
            logger.error(f"预处理消息失败: {e}")
            traceback.print_exc()


# 创建全局ChatBot实例
chat_bot = ChatBot()
