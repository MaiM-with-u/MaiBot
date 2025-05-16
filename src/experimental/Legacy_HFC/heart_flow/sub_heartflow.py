from .observation import Observation, ChattingObservation
import asyncio
import time
from typing import Optional, List, Dict, Tuple, Callable, Coroutine
import traceback
from src.common.logger_manager import get_logger
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.chat_stream import chat_manager
from ..heartFC_chat import HeartFChatting
from ..normal_chat import NormalChat
from .mai_state_manager import MaiStateInfo
from .chat_state_info import ChatState, ChatStateInfo
from .sub_mind import SubMind
from .utils_chat import get_chat_type_and_target_info
from .interest_chatting import InterestChatting


logger = get_logger("sub_heartflow")


class SubHeartflow:
    def __init__(
        self,
        subheartflow_id,
        mai_states: MaiStateInfo,
        hfc_no_reply_callback: Callable[[], Coroutine[None, None, None]],
    ):
        """子心流初始化函数

        Args:
            subheartflow_id: 子心流唯一标识符
            mai_states: 麦麦状态信息实例
            hfc_no_reply_callback: HFChatting 连续不回复时触发的回调
        """
        # 基础属性，两个值是一样的
        self.subheartflow_id = subheartflow_id
        self.chat_id = subheartflow_id
        self.hfc_no_reply_callback = hfc_no_reply_callback

        # 麦麦的状态
        self.mai_states = mai_states

        # 这个聊天流的状态
        self.chat_state: ChatStateInfo = ChatStateInfo()
        self.chat_state_changed_time: float = time.time()
        self.chat_state_last_time: float = 0
        self.history_chat_state: List[Tuple[ChatState, float]] = []

        # --- Initialize attributes ---
        self.is_group_chat: bool = False
        self.chat_target_info: Optional[dict] = None
        # --- End Initialization ---

        # 兴趣检测器
        self.interest_chatting: InterestChatting = InterestChatting()

        # 活动状态管理
        self.should_stop = False  # 停止标志
        self.task: Optional[asyncio.Task] = None  # 后台任务

        # 随便水群 normal_chat 和 认真水群 heartFC_chat 实例
        # CHAT模式激活 随便水群  FOCUS模式激活 认真水群
        self.heart_fc_instance: Optional[HeartFChatting] = None  # 该sub_heartflow的HeartFChatting实例
        self.normal_chat_instance: Optional[NormalChat] = None  # 该sub_heartflow的NormalChat实例

        # 观察，目前只有聊天观察，可以载入多个
        # 负责对处理过的消息进行观察
        self.observations: List[ChattingObservation] = []  # 观察列表
        # self.running_knowledges = []  # 运行中的知识，待完善

        # LLM模型配置，负责进行思考
        self.sub_mind = SubMind(
            subheartflow_id=self.subheartflow_id, chat_state=self.chat_state, observations=self.observations
        )

        # 日志前缀 - Moved determination to initialize
        self.log_prefix = str(subheartflow_id)  # Initial default prefix

    async def initialize(self):
        """异步初始化方法，创建兴趣流并确定聊天类型"""

        # --- Use utility function to determine chat type and fetch info ---
        self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.chat_id)
        # Update log prefix after getting info (potential stream name)
        self.log_prefix = (
            chat_manager.get_stream_name(self.subheartflow_id) or self.subheartflow_id
        )  # Keep this line or adjust if utils provides name
        logger.debug(
            f"SubHeartflow {self.chat_id} initialized: is_group={self.is_group_chat}, target_info={self.chat_target_info}"
        )
        # --- End using utility function ---

        # Initialize interest system (existing logic)
        await self.interest_chatting.initialize()
        logger.debug(f"{self.log_prefix} InterestChatting 实例已初始化。")

    def update_last_chat_state_time(self):
        self.chat_state_last_time = time.time() - self.chat_state_changed_time

    async def _stop_normal_chat(self):
        """
        停止 NormalChat 实例
        切出 CHAT 状态时使用
        """
        if self.normal_chat_instance:
            logger.info(f"{self.log_prefix} 离开CHAT模式，结束 随便水群")
            try:
                await self.normal_chat_instance.stop_chat()  # 调用 stop_chat
            except Exception as e:
                logger.error(f"{self.log_prefix} 停止 NormalChat 监控任务时出错: {e}")
                logger.error(traceback.format_exc())

    async def _start_normal_chat(self, rewind=False) -> bool:
        """
        启动 NormalChat 实例，并进行异步初始化。
        进入 CHAT 状态时使用。
        确保 HeartFChatting 已停止。
        """
        await self._stop_heart_fc_chat()  # 确保 专注聊天已停止

        log_prefix = self.log_prefix
        try:
            # 获取聊天流并创建 NormalChat 实例 (同步部分)
            chat_stream = chat_manager.get_stream(self.chat_id)
            if not chat_stream:
                logger.error(f"{log_prefix} 无法获取 chat_stream，无法启动 NormalChat。")
                return False
            if rewind:
                self.normal_chat_instance = NormalChat(chat_stream=chat_stream, interest_dict=self.get_interest_dict())
            else:
                self.normal_chat_instance = NormalChat(chat_stream=chat_stream)

            # 进行异步初始化
            await self.normal_chat_instance.initialize()

            # 启动聊天任务
            logger.info(f"{log_prefix} 开始普通聊天，随便水群...")
            await self.normal_chat_instance.start_chat()  # start_chat now ensures init is called again if needed
            return True
        except Exception as e:
            logger.error(f"{log_prefix} 启动 NormalChat 或其初始化时出错: {e}")
            logger.error(traceback.format_exc())
            self.normal_chat_instance = None  # 启动/初始化失败，清理实例
            return False

    async def _stop_heart_fc_chat(self):
        """停止并清理 HeartFChatting 实例"""
        if self.heart_fc_instance:
            logger.debug(f"{self.log_prefix} 结束专注聊天...")
            try:
                await self.heart_fc_instance.shutdown()
            except Exception as e:
                logger.error(f"{self.log_prefix} 关闭 HeartFChatting 实例时出错: {e}")
                logger.error(traceback.format_exc())
            finally:
                # 无论是否成功关闭，都清理引用
                self.heart_fc_instance = None

    async def _start_heart_fc_chat(self) -> bool:
        """启动 HeartFChatting 实例，确保 NormalChat 已停止"""
        await self._stop_normal_chat()  # 确保普通聊天监控已停止
        self.clear_interest_dict()  # 清理兴趣字典，准备专注聊天

        log_prefix = self.log_prefix
        # 如果实例已存在，检查其循环任务状态
        if self.heart_fc_instance:
            # 如果任务已完成或不存在，则尝试重新启动
            if self.heart_fc_instance._loop_task is None or self.heart_fc_instance._loop_task.done():
                logger.info(f"{log_prefix} HeartFChatting 实例存在但循环未运行，尝试启动...")
                try:
                    await self.heart_fc_instance.start()  # 启动循环
                    logger.info(f"{log_prefix} HeartFChatting 循环已启动。")
                    return True
                except Exception as e:
                    logger.error(f"{log_prefix} 尝试启动现有 HeartFChatting 循环时出错: {e}")
                    logger.error(traceback.format_exc())
                    return False  # 启动失败
            else:
                # 任务正在运行
                logger.debug(f"{log_prefix} HeartFChatting 已在运行中。")
                return True  # 已经在运行

        # 如果实例不存在，则创建并启动
        logger.info(f"{log_prefix} 麦麦准备开始专注聊天...")
        try:
            # 创建 HeartFChatting 实例，并传递 从构造函数传入的 回调函数
            self.heart_fc_instance = HeartFChatting(
                chat_id=self.subheartflow_id,
                sub_mind=self.sub_mind,
                observations=self.observations,  # 传递所有观察者
                on_consecutive_no_reply_callback=self.hfc_no_reply_callback,  # <-- Use stored callback
            )

            # 初始化并启动 HeartFChatting
            if await self.heart_fc_instance._initialize():
                await self.heart_fc_instance.start()
                logger.debug(f"{log_prefix} 麦麦已成功进入专注聊天模式 (新实例已启动)。")
                return True
            else:
                logger.error(f"{log_prefix} HeartFChatting 初始化失败，无法进入专注模式。")
                self.heart_fc_instance = None  # 初始化失败，清理实例
                return False
        except Exception as e:
            logger.error(f"{log_prefix} 创建或启动 HeartFChatting 实例时出错: {e}")
            logger.error(traceback.format_exc())
            self.heart_fc_instance = None  # 创建或初始化异常，清理实例
            return False

    async def change_chat_state(self, new_state: "ChatState"):
        """更新sub_heartflow的聊天状态，并管理 HeartFChatting 和 NormalChat 实例及任务"""
        current_state = self.chat_state.chat_status

        if current_state == new_state:
            return

        log_prefix = self.log_prefix
        state_changed = False  # 标记状态是否实际发生改变

        # --- 状态转换逻辑 ---
        if new_state == ChatState.CHAT:
            # 移除限额检查逻辑
            logger.debug(f"{log_prefix} 准备进入或保持 聊天 状态")
            if current_state == ChatState.FOCUSED:
                if await self._start_normal_chat(rewind=False):
                    # logger.info(f"{log_prefix} 成功进入或保持 NormalChat 状态。")
                    state_changed = True
                else:
                    logger.error(f"{log_prefix} 从FOCUSED状态启动 NormalChat 失败，无法进入 CHAT 状态。")
                    # 考虑是否需要回滚状态或采取其他措施
                    return  # 启动失败，不改变状态
            else:
                if await self._start_normal_chat(rewind=True):
                    # logger.info(f"{log_prefix} 成功进入或保持 NormalChat 状态。")
                    state_changed = True
                else:
                    logger.error(f"{log_prefix} 从ABSENT状态启动 NormalChat 失败，无法进入 CHAT 状态。")
                    # 考虑是否需要回滚状态或采取其他措施
                    return  # 启动失败，不改变状态

        elif new_state == ChatState.FOCUSED:
            # 移除限额检查逻辑
            logger.debug(f"{log_prefix} 准备进入或保持 专注聊天 状态")
            if await self._start_heart_fc_chat():
                logger.debug(f"{log_prefix} 成功进入或保持 HeartFChatting 状态。")
                state_changed = True
            else:
                logger.error(f"{log_prefix} 启动 HeartFChatting 失败，无法进入 FOCUSED 状态。")
                # 启动失败，状态回滚到之前的状态或ABSENT？这里保持不改变
                return  # 启动失败，不改变状态

        elif new_state == ChatState.ABSENT:
            logger.info(f"{log_prefix} 进入 ABSENT 状态，停止所有聊天活动...")
            self.clear_interest_dict()

            await self._stop_normal_chat()
            await self._stop_heart_fc_chat()
            state_changed = True  # 总是可以成功转换到 ABSENT

        # --- 更新状态和最后活动时间 ---
        if state_changed:
            self.update_last_chat_state_time()
            self.history_chat_state.append((current_state, self.chat_state_last_time))

            logger.info(
                f"{log_prefix} 麦麦的聊天状态从 {current_state.value} （持续了 {int(self.chat_state_last_time)} 秒） 变更为 {new_state.value}"
            )

            self.chat_state.chat_status = new_state
            self.chat_state_last_time = 0
            self.chat_state_changed_time = time.time()
        else:
            # 如果因为某些原因（如启动失败）没有成功改变状态，记录一下
            logger.debug(
                f"{log_prefix} 尝试将状态从 {current_state.value} 变为 {new_state.value}，但未成功或未执行更改。"
            )

    async def subheartflow_start_working(self):
        """启动子心流的后台任务

        功能说明:
        - 负责子心流的主要后台循环
        - 每30秒检查一次停止标志
        """
        logger.trace(f"{self.log_prefix} 子心流开始工作...")

        while not self.should_stop:
            await asyncio.sleep(30)  # 30秒检查一次停止标志

        logger.info(f"{self.log_prefix} 子心流后台任务已停止。")

    def update_current_mind(self, response):
        self.sub_mind.update_current_mind(response)

    def add_observation(self, observation: Observation):
        for existing_obs in self.observations:
            if existing_obs.observe_id == observation.observe_id:
                return
        self.observations.append(observation)

    def remove_observation(self, observation: Observation):
        if observation in self.observations:
            self.observations.remove(observation)

    def get_all_observations(self) -> list[Observation]:
        return self.observations

    def clear_observations(self):
        self.observations.clear()

    def _get_primary_observation(self) -> Optional[ChattingObservation]:
        if self.observations and isinstance(self.observations[0], ChattingObservation):
            return self.observations[0]
        logger.warning(f"SubHeartflow {self.subheartflow_id} 没有找到有效的 ChattingObservation")
        return None

    async def get_interest_state(self) -> dict:
        return await self.interest_chatting.get_state()

    def get_normal_chat_last_speak_time(self) -> float:
        if self.normal_chat_instance:
            return self.normal_chat_instance.last_speak_time
        return 0

    def get_interest_dict(self) -> Dict[str, tuple[MessageRecv, float, bool]]:
        return self.interest_chatting.interest_dict

    def clear_interest_dict(self):
        self.interest_chatting.interest_dict.clear()

    async def get_full_state(self) -> dict:
        """获取子心流的完整状态，包括兴趣、思维和聊天状态。"""
        interest_state = await self.get_interest_state()
        return {
            "interest_state": interest_state,
            "current_mind": self.sub_mind.current_mind,
            "chat_state": self.chat_state.chat_status.value,
            "chat_state_changed_time": self.chat_state_changed_time,
        }

    async def shutdown(self):
        """安全地关闭子心流及其管理的任务"""
        if self.should_stop:
            logger.info(f"{self.log_prefix} 子心流已在关闭过程中。")
            return

        logger.info(f"{self.log_prefix} 开始关闭子心流...")
        self.should_stop = True  # 标记为停止，让后台任务退出

        # 使用新的停止方法
        await self._stop_normal_chat()
        await self._stop_heart_fc_chat()

        # 停止兴趣更新任务
        if self.interest_chatting:
            logger.info(f"{self.log_prefix} 停止兴趣系统后台任务...")
            await self.interest_chatting.stop_updates()

        # 取消可能存在的旧后台任务 (self.task)
        if self.task and not self.task.done():
            logger.debug(f"{self.log_prefix} 取消子心流主任务 (Shutdown)...")
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=1.0)  # 给点时间响应取消
            except asyncio.CancelledError:
                logger.debug(f"{self.log_prefix} 子心流主任务已取消 (Shutdown)。")
            except asyncio.TimeoutError:
                logger.warning(f"{self.log_prefix} 等待子心流主任务取消超时 (Shutdown)。")
            except Exception as e:
                logger.error(f"{self.log_prefix} 等待子心流主任务取消时发生错误 (Shutdown): {e}")

        self.task = None  # 清理任务引用
        self.chat_state.chat_status = ChatState.ABSENT  # 状态重置为不参与

        logger.info(f"{self.log_prefix} 子心流关闭完成。")
