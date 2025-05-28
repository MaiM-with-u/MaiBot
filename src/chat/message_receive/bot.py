import traceback
from typing import Dict, Any

from src.common.logger_manager import get_logger
from src.manager.mood_manager import mood_manager  # 导入情绪管理器
from src.chat.message_receive.chat_stream import chat_manager
from src.chat.message_receive.message import MessageRecv
from src.experimental.only_message_process import MessageProcessor
from src.experimental.PFC.pfc_manager import PFCManager
from src.chat.focus_chat.heartflow_message_processor import HeartFCMessageReceiver
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.config.config import global_config

# 定义日志配置


# 配置主程序日志格式
logger = get_logger("chat")


class ChatBot:
    def __init__(self):
        self.bot = None  # bot 实例引用
        self._started = False
        self.mood_manager = mood_manager  # 获取情绪管理器单例
        self.heartflow_message_receiver = HeartFCMessageReceiver()  # 新增

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
            if global_config.experimental.pfc_chatting:
                chat_id = str(message.chat_stream.stream_id)
                private_name = str(message.message_info.user_info.user_nickname)

                await self.pfc_manager.get_or_create_conversation(chat_id, private_name)

        except Exception as e:
            logger.error(f"创建PFC聊天失败: {e}")

    async def message_process(self, message_data: Dict[str, Any]) -> None:
        """处理转化后的统一格式消息
        这个函数本质是预处理一些数据，根据配置信息和消息内容，预处理消息，并分发到合适的消息处理器中
        heart_flow模式：使用思维流系统进行回复
        - 包含思维流状态管理
        - 在回复前进行观察和状态更新
        - 回复后更新思维流状态
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
            # print(message_data)
            logger.trace(f"处理消息:{str(message_data)[:120]}...")
            message = MessageRecv(message_data)
            group_info = message.message_info.group_info
            user_info = message.message_info.user_info
            chat_manager.register_message(message)

            # 确认从接口发来的message是否有自定义的prompt模板信息
            if message.message_info.template_info and not message.message_info.template_info.template_default:
                template_group_name = message.message_info.template_info.template_name
                template_items = message.message_info.template_info.template_items
                async with global_prompt_manager.async_message_scope(template_group_name):
                    if isinstance(template_items, dict):
                        for k in template_items.keys():
                            await Prompt.create_async(template_items[k], k)
                            logger.debug(f"注册{template_items[k]},{k}")
            else:
                template_group_name = None

            async def preprocess():
                logger.trace("开始预处理消息...")
                # 如果在私聊中
                if group_info is None:
                    logger.trace("检测到私聊消息")
                    if global_config.experimental.pfc_chatting:
                        logger.trace("进入PFC私聊处理流程")
                        # 创建聊天流
                        logger.trace(f"为{user_info.user_id}创建/获取聊天流")
                        chat = await chat_manager.get_or_create_stream(
                            platform=message.message_info.platform,
                            user_info=user_info,
                            group_info=group_info,
                        )
                        message.update_chat_stream(chat)
                        await self.only_process_chat.process_message(message)
                        await self._create_pfc_chat(message)
                    # 禁止PFC，进入普通的心流消息处理逻辑
                    else:
                        logger.trace("进入普通心流私聊处理")
                        await self.heartflow_message_receiver.process_message(message_data)
                # 群聊默认进入心流消息处理逻辑
                else:
                    logger.trace(f"检测到群聊消息，群ID: {group_info.group_id}")
                    await self.heartflow_message_receiver.process_message(message_data)

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
