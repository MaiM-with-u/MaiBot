import asyncio
from src.config.config import global_config
from typing import Optional, Dict
import traceback
from src.common.logger_manager import get_logger
from src.chat.message_receive.message import MessageRecv
import math


# 定义常量 (从 interest.py 移动过来)
MAX_INTEREST = 15.0

logger = get_logger("interest_chatting")

PROBABILITY_INCREASE_RATE_PER_SECOND = 0.1
PROBABILITY_DECREASE_RATE_PER_SECOND = 0.1
MAX_REPLY_PROBABILITY = 1


class InterestChatting:
    def __init__(
        self,
        decay_rate=global_config.default_decay_rate_per_second,
        max_interest=MAX_INTEREST,
        trigger_threshold=global_config.reply_trigger_threshold,
        max_probability=MAX_REPLY_PROBABILITY,
    ):
        # 基础属性初始化
        self.interest_level: float = 0.0
        self.decay_rate_per_second: float = decay_rate
        self.max_interest: float = max_interest

        self.trigger_threshold: float = trigger_threshold
        self.max_reply_probability: float = max_probability
        self.is_above_threshold: bool = False

        # 任务相关属性初始化
        self.update_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._task_lock = asyncio.Lock()
        self._is_running = False

        self.interest_dict: Dict[str, tuple[MessageRecv, float, bool]] = {}
        self.update_interval = 1.0

        self.above_threshold = False
        self.start_hfc_probability = 0.0

    async def initialize(self):
        async with self._task_lock:
            if self._is_running:
                logger.debug("后台兴趣更新任务已在运行中。")
                return

            # 清理已完成或已取消的任务
            if self.update_task and (self.update_task.done() or self.update_task.cancelled()):
                self.update_task = None

            if not self.update_task:
                self._stop_event.clear()
                self._is_running = True
                self.update_task = asyncio.create_task(self._run_update_loop(self.update_interval))
                logger.debug("后台兴趣更新任务已创建并启动。")

    def add_interest_dict(self, message: MessageRecv, interest_value: float, is_mentioned: bool):
        """添加消息到兴趣字典

        参数:
            message: 接收到的消息
            interest_value: 兴趣值
            is_mentioned: 是否被提及

        功能:
            1. 将消息添加到兴趣字典
            2. 更新最后交互时间
            3. 如果字典长度超过10，删除最旧的消息
        """
        # 添加新消息
        self.interest_dict[message.message_info.message_id] = (message, interest_value, is_mentioned)

        # 如果字典长度超过10，删除最旧的消息
        if len(self.interest_dict) > 10:
            oldest_key = next(iter(self.interest_dict))
            self.interest_dict.pop(oldest_key)

    async def _calculate_decay(self):
        """计算兴趣值的衰减

        参数:
            current_time: 当前时间戳

        处理逻辑:
        1. 计算时间差
        2. 处理各种异常情况(负值/零值)
        3. 正常计算衰减
        4. 更新最后更新时间
        """

        # 处理极小兴趣值情况
        if self.interest_level < 1e-9:
            self.interest_level = 0.0
            return

        # 异常情况处理
        if self.decay_rate_per_second <= 0:
            logger.warning(f"衰减率({self.decay_rate_per_second})无效，重置兴趣值为0")
            self.interest_level = 0.0
            return

        # 正常衰减计算
        try:
            decay_factor = math.pow(self.decay_rate_per_second, self.update_interval)
            self.interest_level *= decay_factor
        except ValueError as e:
            logger.error(
                f"衰减计算错误: {e} 参数: 衰减率={self.decay_rate_per_second} 时间差={self.update_interval} 当前兴趣={self.interest_level}"
            )
            self.interest_level = 0.0

    async def _update_reply_probability(self):
        self.above_threshold = self.interest_level >= self.trigger_threshold
        if self.above_threshold:
            self.start_hfc_probability += PROBABILITY_INCREASE_RATE_PER_SECOND
        else:
            if self.start_hfc_probability > 0:
                self.start_hfc_probability = max(0, self.start_hfc_probability - PROBABILITY_DECREASE_RATE_PER_SECOND)

    async def increase_interest(self, value: float):
        self.interest_level += value
        self.interest_level = min(self.interest_level, self.max_interest)

    async def decrease_interest(self, value: float):
        self.interest_level -= value
        self.interest_level = max(self.interest_level, 0.0)

    async def get_interest(self) -> float:
        return self.interest_level

    async def get_state(self) -> dict:
        interest = self.interest_level  # 直接使用属性值
        return {
            "interest_level": round(interest, 2),
            "start_hfc_probability": round(self.start_hfc_probability, 4),
            "above_threshold": self.above_threshold,
        }

    # --- 新增后台更新任务相关方法 ---
    async def _run_update_loop(self, update_interval: float = 1.0):
        """后台循环，定期更新兴趣和回复概率。"""
        try:
            while not self._stop_event.is_set():
                try:
                    if self.interest_level != 0:
                        await self._calculate_decay()

                    await self._update_reply_probability()

                    # 等待下一个周期或停止事件
                    await asyncio.wait_for(self._stop_event.wait(), timeout=update_interval)
                except asyncio.TimeoutError:
                    # 正常超时，继续循环
                    continue
                except Exception as e:
                    logger.error(f"InterestChatting 更新循环出错: {e}")
                    logger.error(traceback.format_exc())
                    # 防止错误导致CPU飙升，稍作等待
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("InterestChatting 更新循环被取消。")
        finally:
            self._is_running = False
            logger.info("InterestChatting 更新循环已停止。")

    async def stop_updates(self):
        """停止后台更新任务，使用锁确保并发安全"""
        async with self._task_lock:
            if not self._is_running:
                logger.debug("后台兴趣更新任务未运行。")
                return

            logger.info("正在停止 InterestChatting 后台更新任务...")
            self._stop_event.set()

            if self.update_task and not self.update_task.done():
                try:
                    # 等待任务结束，设置超时
                    await asyncio.wait_for(self.update_task, timeout=5.0)
                    logger.info("InterestChatting 后台更新任务已成功停止。")
                except asyncio.TimeoutError:
                    logger.warning("停止 InterestChatting 后台任务超时，尝试取消...")
                    self.update_task.cancel()
                    try:
                        await self.update_task  # 等待取消完成
                    except asyncio.CancelledError:
                        logger.info("InterestChatting 后台更新任务已被取消。")
                except Exception as e:
                    logger.error(f"停止 InterestChatting 后台任务时发生异常: {e}")
                finally:
                    self.update_task = None
                    self._is_running = False
