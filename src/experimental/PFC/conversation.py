import time
import asyncio
import traceback
from typing import Dict, Any, Optional
from src.common.logger_manager import get_logger
from maim_message import UserInfo
from src.chat.message_receive.chat_stream import chat_manager, ChatStream
from src.chat.message_receive.message import Message  # 假设 Message 类型被 _convert_to_message 使用
from src.config.config import global_config
from src.chat.person_info.person_info import person_info_manager
from src.chat.person_info.relationship_manager import relationship_manager
from src.manager.mood_manager import mood_manager

from .pfc_relationship import PfcRelationshipUpdater, PfcRepationshipTranslator
from .pfc_emotion import PfcEmotionUpdater

# 导入 PFC 内部组件和类型
from .pfc_types import ConversationState
from .pfc import GoalAnalyzer
from .chat_observer import ChatObserver
from .message_sender import DirectMessageSender
from .action_planner import ActionPlanner
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from .reply_generator import ReplyGenerator
from .PFC_idle.idle_chat import IdleChat
from .waiter import Waiter
from .reply_checker import ReplyChecker

from .conversation_loop import run_conversation_loop

from rich.traceback import install

install(extra_lines=3)

logger = get_logger("pfc_conversation")


class Conversation:
    """
    对话类，负责管理单个私聊对话的状态和核心逻辑流程。
    """

    def __init__(self, stream_id: str, private_name: str):
        """
        初始化对话实例的基本属性。
        核心组件的实例化将由 PFCManager 通过调用 conversation_initializer 中的函数完成。
        """
        self.stream_id: str = stream_id
        self.private_name: str = private_name
        self.state: ConversationState = ConversationState.INIT
        self.should_continue: bool = False
        self.ignore_until_timestamp: Optional[float] = None
        self.generated_reply: str = ""
        self.chat_stream: Optional[ChatStream] = None

        self.person_info_mng = person_info_manager
        self.relationship_mng = relationship_manager
        self.mood_mng = mood_manager

        self.relationship_updater: Optional[PfcRelationshipUpdater] = None
        self.relationship_translator: Optional[PfcRepationshipTranslator] = None
        self.emotion_updater: Optional[PfcEmotionUpdater] = None
        self.action_planner: Optional[ActionPlanner] = None
        self.goal_analyzer: Optional[GoalAnalyzer] = None
        self.reply_generator: Optional[ReplyGenerator] = None
        self.waiter: Optional[Waiter] = None
        self.direct_sender: Optional[DirectMessageSender] = None
        self.idle_chat: Optional[IdleChat] = None
        self.chat_observer: Optional[ChatObserver] = None
        self.observation_info: Optional[ObservationInfo] = None
        self.conversation_info: Optional[ConversationInfo] = None
        self.reply_checker: Optional[ReplyChecker] = None

        self._initialized: bool = False

        self.bot_qq_str: Optional[str] = str(global_config.BOT_QQ) if global_config.BOT_QQ else None
        if not self.bot_qq_str:
            logger.error(f"[私聊][{self.private_name}] 严重错误：未能从配置中获取 BOT_QQ ID！")

        # 确保这个属性被正确初始化
        self.consecutive_llm_action_failures: int = 0  # LLM相关动作连续失败的计数器

    async def start(self):
        """
        启动对话流程。创建并启动核心的规划与行动循环 (`run_conversation_loop`)。
        """
        if not self._initialized:
            logger.error(f"[私聊][{self.private_name}] 对话实例未被 Manager 正确初始化，无法启动规划循环。")
            return

        if not self.should_continue:
            logger.warning(f"[私聊][{self.private_name}] 对话实例已被 Manager 标记为不应继续，无法启动规划循环。")
            return

        logger.info(f"[私聊][{self.private_name}] 对话系统启动，准备创建规划循环任务...")
        try:
            # 创建PFC主循环任务
            _loop_task = asyncio.create_task(run_conversation_loop(self))
            logger.info(f"[私聊][{self.private_name}] 规划循环任务已创建。")
        except Exception as task_err:
            logger.error(f"[私聊][{self.private_name}] 创建规划循环任务时出错: {task_err}")
            await self.stop()  # 发生错误时尝试停止

    async def stop(self):
        """
        停止对话实例并清理相关资源。
        """
        logger.info(f"[私聊][{self.private_name}] 正在停止对话实例: {self.stream_id}")
        self.should_continue = False  # 设置标志以退出循环

        # 最终关系评估
        if (
            self._initialized  # 确保已初始化
            and self.relationship_updater
            and self.conversation_info
            and self.observation_info
            and self.chat_observer  # 确保所有需要的组件都存在
        ):
            try:
                logger.info(f"[私聊][{self.private_name}] 准备执行最终关系评估...")
                await self.relationship_updater.update_relationship_final(
                    conversation_info=self.conversation_info,
                    observation_info=self.observation_info,
                    chat_observer_for_history=self.chat_observer,
                )
                logger.debug(f"[私聊][{self.private_name}] 最终关系评估已调用。")
            except Exception as e_final_rel:
                logger.error(f"[私聊][{self.private_name}] 调用最终关系评估时出错: {e_final_rel}")
                logger.error(traceback.format_exc())
        else:
            logger.warning(f"[私聊][{self.private_name}] 跳过最终关系评估，实例未完全初始化或缺少组件。")

        # 停止其他组件
        if self.idle_chat:
            await self.idle_chat.decrement_active_instances()  # 减少活跃实例计数
            logger.debug(f"[私聊][{self.private_name}] 已减少IdleChat活跃实例计数")
        if self.observation_info and self.chat_observer:  # 确保二者都存在
            self.observation_info.unbind_from_chat_observer()  # 解绑

        self._initialized = False  # 标记为未初始化
        logger.info(f"[私聊][{self.private_name}] 对话实例 {self.stream_id} 已停止。")

    def _convert_to_message(self, msg_dict: Dict[str, Any]) -> Optional[Message]:
        """将从数据库或其他来源获取的消息字典转换为内部使用的 Message 对象"""
        # 这个方法似乎没有被其他内部方法调用，但为了完整性暂时保留
        try:
            # 尝试获取与此对话实例关联的 ChatStream
            chat_stream_to_use = self.chat_stream or chat_manager.get_stream(self.stream_id)
            if not chat_stream_to_use:
                logger.error(
                    f"[私聊][{self.private_name}] 无法确定 ChatStream for stream_id {self.stream_id}，无法转换消息。"
                )
                return None

            # 解析 UserInfo
            user_info_dict = msg_dict.get("user_info", {})
            user_info: Optional[UserInfo] = None
            if isinstance(user_info_dict, dict):
                try:
                    user_info = UserInfo.from_dict(user_info_dict)
                except Exception as e:
                    logger.warning(
                        f"[私聊][{self.private_name}] 从字典创建 UserInfo 时出错: {e}, dict: {user_info_dict}"
                    )
            if not user_info:  # 如果没有有效的 UserInfo，则无法创建 Message 对象
                logger.warning(
                    f"[私聊][{self.private_name}] 消息缺少有效的 UserInfo，无法转换。 msg_id: {msg_dict.get('message_id')}"
                )
                return None

            # 创建并返回 Message 对象
            return Message(
                message_id=msg_dict.get("message_id", f"gen_{time.time()}"),  # 提供默认 message_id
                chat_stream=chat_stream_to_use,
                time=msg_dict.get("time", time.time()),  # 提供默认时间
                user_info=user_info,
                processed_plain_text=msg_dict.get("processed_plain_text", ""),  # 提供默认文本
                detailed_plain_text=msg_dict.get("detailed_plain_text", ""),  # 提供默认详细文本
            )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 转换消息时出错: {e}")
            logger.error(f"[私聊][{self.private_name}] {traceback.format_exc()}")
            return None  # 出错时返回 None
