import traceback
from typing import Optional, Dict
import asyncio
from asyncio import Lock
from ...moods.moods import MoodManager
from ...chat.emoji_manager import emoji_manager
from .heartFC_generator import ResponseGenerator
from .messagesender import MessageManager
from src.heart_flow.heartflow import heartflow
from src.common.logger import get_module_logger, CHAT_STYLE_CONFIG, LogConfig
from src.plugins.person_info.relationship_manager import relationship_manager
from src.do_tool.tool_use import ToolUser
from .interest import InterestManager
from src.plugins.chat.chat_stream import chat_manager
from .pf_chatting import PFChatting

# 定义日志配置
chat_config = LogConfig(
    console_format=CHAT_STYLE_CONFIG["console_format"],
    file_format=CHAT_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("HeartFCController", config=chat_config)

# 检测群聊兴趣的间隔时间
INTEREST_MONITOR_INTERVAL_SECONDS = 1


class HeartFCController:
    _instance = None  # For potential singleton access if needed by MessageManager

    def __init__(self):
        # --- Updated Init ---
        if HeartFCController._instance is not None:
            # Prevent re-initialization if used as a singleton
            return
        self.gpt = ResponseGenerator()
        self.mood_manager = MoodManager.get_instance()
        self.mood_manager.start_mood_update()
        self.tool_user = ToolUser()
        self.interest_manager = InterestManager()
        self._interest_monitor_task: Optional[asyncio.Task] = None
        # --- New PFChatting Management ---
        self.pf_chatting_instances: Dict[str, PFChatting] = {}
        self._pf_chatting_lock = Lock()
        # --- End New PFChatting Management ---
        HeartFCController._instance = self  # Register instance
        # --- End Updated Init ---
        # --- Make dependencies accessible for PFChatting ---
        # These are accessed via the passed instance in PFChatting
        self.emoji_manager = emoji_manager
        self.relationship_manager = relationship_manager
        self.MessageManager = MessageManager  # Pass the class/singleton access
        # --- End dependencies ---

    # --- Added Class Method for Singleton Access ---
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            # This might indicate an issue if called before initialization
            logger.warning("HeartFCController get_instance called before initialization.")
            # Optionally, initialize here if a strict singleton pattern is desired
            # cls._instance = cls()
        return cls._instance

    # --- End Added Class Method ---

    async def start(self):
        """启动异步任务,如回复启动器"""
        logger.debug("HeartFCController 正在启动异步任务...")
        self._initialize_monitor_task()
        logger.info("HeartFCController 异步任务启动完成")

    def _initialize_monitor_task(self):
        """启动后台兴趣监控任务，可以检查兴趣是否足以开启心流对话"""
        if self._interest_monitor_task is None or self._interest_monitor_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._interest_monitor_task = loop.create_task(self._interest_monitor_loop())
            except RuntimeError:
                logger.error("创建兴趣监控任务失败：没有运行中的事件循环。")
                raise
        else:
            logger.warning("跳过兴趣监控任务创建：任务已存在或正在运行。")

    # --- Added PFChatting Instance Manager ---
    async def _get_or_create_pf_chatting(self, stream_id: str) -> Optional[PFChatting]:
        """获取现有PFChatting实例或创建新实例。"""
        async with self._pf_chatting_lock:
            if stream_id not in self.pf_chatting_instances:
                logger.info(f"为流 {stream_id} 创建新的PFChatting实例")
                # 传递 self (HeartFCController 实例) 进行依赖注入
                instance = PFChatting(stream_id, self)
                # 执行异步初始化
                if not await instance._initialize():
                    logger.error(f"为流 {stream_id} 初始化PFChatting失败")
                    return None
                self.pf_chatting_instances[stream_id] = instance
            return self.pf_chatting_instances[stream_id]

    # --- End Added PFChatting Instance Manager ---

    async def _interest_monitor_loop(self):
        """后台任务，定期检查兴趣度变化并触发回复"""
        logger.info("兴趣监控循环开始...")
        while True:
            await asyncio.sleep(INTEREST_MONITOR_INTERVAL_SECONDS)
            try:
                # 从心流中获取活跃流
                active_stream_ids = list(heartflow.get_all_subheartflows_streams_ids())
                for stream_id in active_stream_ids:
                    stream_name = chat_manager.get_stream_name(stream_id) or stream_id  # 获取流名称
                    sub_hf = heartflow.get_subheartflow(stream_id)
                    if not sub_hf:
                        logger.warning(f"监控循环: 无法获取活跃流 {stream_name} 的 sub_hf")
                        continue

                    should_trigger = False
                    try:
                        interest_chatting = self.interest_manager.get_interest_chatting(stream_id)
                        if interest_chatting:
                            should_trigger = interest_chatting.should_evaluate_reply()
                        else:
                            logger.trace(
                                f"[{stream_name}] 没有找到对应的 InterestChatting 实例，跳过基于兴趣的触发检查。"
                            )
                    except Exception as e:
                        logger.error(f"检查兴趣触发器时出错 流 {stream_name}: {e}")
                        logger.error(traceback.format_exc())

                    if should_trigger:
                        # 启动一次麦麦聊天
                        pf_instance = await self._get_or_create_pf_chatting(stream_id)
                        if pf_instance:
                            asyncio.create_task(pf_instance.add_time())
                        else:
                            logger.error(f"[{stream_name}] 无法获取或创建PFChatting实例。跳过触发。")

            except asyncio.CancelledError:
                logger.info("兴趣监控循环已取消。")
                break
            except Exception as e:
                logger.error(f"兴趣监控循环错误: {e}")
                logger.error(traceback.format_exc())
                await asyncio.sleep(5)  # 发生错误时等待
