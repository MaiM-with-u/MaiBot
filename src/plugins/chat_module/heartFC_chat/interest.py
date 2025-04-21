import time
import math
import asyncio
import threading
import json  # 引入 json
import os  # 引入 os
from typing import Optional  # <--- 添加导入
import random  # <--- 添加导入 random
from src.common.logger import get_module_logger, LogConfig, DEFAULT_CONFIG  # 引入 DEFAULT_CONFIG
from src.plugins.chat.chat_stream import chat_manager  # *** Import ChatManager ***

# 定义日志配置 (使用 loguru 格式)
interest_log_config = LogConfig(
    console_format=DEFAULT_CONFIG["console_format"],  # 使用默认控制台格式
    file_format=DEFAULT_CONFIG["file_format"],  # 使用默认文件格式
)
logger = get_module_logger("InterestManager", config=interest_log_config)


# 定义常量
DEFAULT_DECAY_RATE_PER_SECOND = 0.98  # 每秒衰减率 (兴趣保留 99%)
MAX_INTEREST = 15.0  # 最大兴趣值
# MIN_INTEREST_THRESHOLD = 0.1      # 低于此值可能被清理 (可选)
CLEANUP_INTERVAL_SECONDS = 1200  # 清理任务运行间隔 (例如：20分钟)
INACTIVE_THRESHOLD_SECONDS = 1200  # 不活跃时间阈值 (例如：20分钟)
LOG_INTERVAL_SECONDS = 3  # 日志记录间隔 (例如：30秒)
LOG_DIRECTORY = "logs/interest"  # 日志目录
# LOG_FILENAME = "interest_log.json"  # 快照日志文件名 (保留，以防其他地方用到)
HISTORY_LOG_FILENAME = "interest_history.log"  # 新的历史日志文件名
# 移除阈值，将移至 HeartFC_Chat
# INTEREST_INCREASE_THRESHOLD = 0.5

# --- 新增：概率回复相关常量 ---
REPLY_TRIGGER_THRESHOLD = 3.0  # 触发概率回复的兴趣阈值 (示例值)
BASE_REPLY_PROBABILITY = 0.1  # 首次超过阈值时的基础回复概率 (示例值)
PROBABILITY_INCREASE_RATE_PER_SECOND = 0.02  # 高于阈值时，每秒概率增加量 (线性增长, 示例值)
PROBABILITY_DECAY_FACTOR_PER_SECOND = 0.2  # 低于阈值时，每秒概率衰减因子 (指数衰减, 示例值)
MAX_REPLY_PROBABILITY = 1  # 回复概率上限 (示例值)
# --- 结束：概率回复相关常量 ---


class InterestChatting:
    def __init__(
        self,
        decay_rate=DEFAULT_DECAY_RATE_PER_SECOND,
        max_interest=MAX_INTEREST,
        trigger_threshold=REPLY_TRIGGER_THRESHOLD,
        base_reply_probability=BASE_REPLY_PROBABILITY,
        increase_rate=PROBABILITY_INCREASE_RATE_PER_SECOND,
        decay_factor=PROBABILITY_DECAY_FACTOR_PER_SECOND,
        max_probability=MAX_REPLY_PROBABILITY,
    ):
        self.interest_level: float = 0.0
        self.last_update_time: float = time.time()  # 同时作为兴趣和概率的更新时间基准
        self.decay_rate_per_second: float = decay_rate
        self.max_interest: float = max_interest
        self.last_interaction_time: float = self.last_update_time  # 新增：最后交互时间

        # --- 新增：概率回复相关属性 ---
        self.trigger_threshold: float = trigger_threshold
        self.base_reply_probability: float = base_reply_probability
        self.probability_increase_rate: float = increase_rate
        self.probability_decay_factor: float = decay_factor
        self.max_reply_probability: float = max_probability
        self.current_reply_probability: float = 0.0
        self.is_above_threshold: bool = False  # 标记兴趣值是否高于阈值
        # --- 结束：概率回复相关属性 ---

    def _calculate_decay(self, current_time: float):
        """计算从上次更新到现在的衰减"""
        time_delta = current_time - self.last_update_time
        if time_delta > 0:
            # 指数衰减: interest = interest * (decay_rate ^ time_delta)
            # 添加处理极小兴趣值避免 math domain error
            old_interest = self.interest_level
            if self.interest_level < 1e-9:
                self.interest_level = 0.0
            else:
                # 检查 decay_rate_per_second 是否为非正数，避免 math domain error
                if self.decay_rate_per_second <= 0:
                    logger.warning(
                        f"InterestChatting encountered non-positive decay rate: {self.decay_rate_per_second}. Setting interest to 0."
                    )
                    self.interest_level = 0.0
                # 检查 interest_level 是否为负数，虽然理论上不应发生，但以防万一
                elif self.interest_level < 0:
                    logger.warning(
                        f"InterestChatting encountered negative interest level: {self.interest_level}. Setting interest to 0."
                    )
                    self.interest_level = 0.0
                else:
                    try:
                        decay_factor = math.pow(self.decay_rate_per_second, time_delta)
                        self.interest_level *= decay_factor
                    except ValueError as e:
                        # 捕获潜在的 math domain error，例如对负数开非整数次方（虽然已加保护）
                        logger.error(
                            f"Math error during decay calculation: {e}. Rate: {self.decay_rate_per_second}, Delta: {time_delta}, Level: {self.interest_level}. Setting interest to 0."
                        )
                        self.interest_level = 0.0

            # 防止低于阈值 (如果需要)
            # self.interest_level = max(self.interest_level, MIN_INTEREST_THRESHOLD)

            # 只有在兴趣值发生变化时才更新时间戳
            if old_interest != self.interest_level:
                self.last_update_time = current_time

    def _update_reply_probability(self, current_time: float):
        """根据当前兴趣是否超过阈值及时间差，更新回复概率"""
        time_delta = current_time - self.last_update_time
        if time_delta <= 0:
            return  # 时间未前进，无需更新

        currently_above = self.interest_level >= self.trigger_threshold

        if currently_above:
            if not self.is_above_threshold:
                # 刚跨过阈值，重置为基础概率
                self.current_reply_probability = self.base_reply_probability
                logger.debug(
                    f"兴趣跨过阈值 ({self.trigger_threshold}). 概率重置为基础值: {self.base_reply_probability:.4f}"
                )
            else:
                # 持续高于阈值，线性增加概率
                increase_amount = self.probability_increase_rate * time_delta
                self.current_reply_probability += increase_amount
                # logger.debug(f"兴趣高于阈值 ({self.trigger_threshold}) 持续 {time_delta:.2f}秒. 概率增加 {increase_amount:.4f} 到 {self.current_reply_probability:.4f}")

            # 限制概率不超过最大值
            self.current_reply_probability = min(self.current_reply_probability, self.max_reply_probability)

        else:
            if 0 < self.probability_decay_factor < 1:
                decay_multiplier = math.pow(self.probability_decay_factor, time_delta)
                # old_prob = self.current_reply_probability
                self.current_reply_probability *= decay_multiplier
                # 避免因浮点数精度问题导致概率略微大于0，直接设为0
                if self.current_reply_probability < 1e-6:
                    self.current_reply_probability = 0.0
                # logger.debug(f"兴趣低于阈值 ({self.trigger_threshold}) 持续 {time_delta:.2f}秒. 概率从 {old_prob:.4f} 衰减到 {self.current_reply_probability:.4f} (因子: {self.probability_decay_factor})")
            elif self.probability_decay_factor <= 0:
                # 如果衰减因子无效或为0，直接清零
                if self.current_reply_probability > 0:
                    logger.warning(f"无效的衰减因子 ({self.probability_decay_factor}). 设置概率为0.")
                    self.current_reply_probability = 0.0
            # else: decay_factor >= 1, probability will not decay or increase, which might be intended in some cases.

            # 确保概率不低于0
            self.current_reply_probability = max(self.current_reply_probability, 0.0)

        # 更新状态标记
        self.is_above_threshold = currently_above
        # 更新时间戳放在调用者处，确保 interest 和 probability 基于同一点更新

    def increase_interest(self, current_time: float, value: float):
        """根据传入的值增加兴趣值，并记录增加量"""
        # 先更新概率和计算衰减（基于上次更新时间）
        self._update_reply_probability(current_time)
        self._calculate_decay(current_time)
        # 应用增加
        self.interest_level += value
        self.interest_level = min(self.interest_level, self.max_interest)  # 不超过最大值
        self.last_update_time = current_time  # 更新时间戳
        self.last_interaction_time = current_time  # 更新最后交互时间

    def decrease_interest(self, current_time: float, value: float):
        """降低兴趣值并更新时间 (确保不低于0)"""
        # 先更新概率（基于上次更新时间）
        self._update_reply_probability(current_time)
        # 注意：降低兴趣度是否需要先衰减？取决于具体逻辑，这里假设不衰减直接减
        self.interest_level -= value
        self.interest_level = max(self.interest_level, 0.0)  # 确保不低于0
        self.last_update_time = current_time  # 降低也更新时间戳
        self.last_interaction_time = current_time  # 更新最后交互时间

    def get_interest(self) -> float:
        """获取当前兴趣值 (计算衰减后)"""
        # 注意：这个方法现在会触发概率和兴趣的更新
        current_time = time.time()
        self._update_reply_probability(current_time)
        self._calculate_decay(current_time)
        self.last_update_time = current_time  # 更新时间戳
        return self.interest_level

    def get_state(self) -> dict:
        """获取当前状态字典"""
        # 调用 get_interest 来确保状态已更新
        interest = self.get_interest()
        return {
            "interest_level": round(interest, 2),
            "last_update_time": self.last_update_time,
            "current_reply_probability": round(self.current_reply_probability, 4),  # 添加概率到状态
            "is_above_threshold": self.is_above_threshold,  # 添加阈值状态
            "last_interaction_time": self.last_interaction_time,  # 新增：添加最后交互时间到状态
            # 可以选择性地暴露 last_increase_amount 给状态，方便调试
            # "last_increase_amount": round(self.last_increase_amount, 2)
        }

    def should_evaluate_reply(self) -> bool:
        """
        判断是否应该触发一次回复评估。
        首先更新概率状态，然后根据当前概率进行随机判断。
        """
        current_time = time.time()
        # 确保概率是基于最新兴趣值计算的
        self._update_reply_probability(current_time)
        # 更新兴趣衰减（如果需要，取决于逻辑，这里保持和 get_interest 一致）
        # self._calculate_decay(current_time)
        # self.last_update_time = current_time # 更新时间戳

        if self.current_reply_probability > 0:
            # 只有在阈值之上且概率大于0时才有可能触发
            trigger = random.random() < self.current_reply_probability
            # if trigger:
            #     logger.info(f"回复概率评估触发! 概率: {self.current_reply_probability:.4f}, 阈值: {self.trigger_threshold}, 兴趣: {self.interest_level:.2f}")
            #     # 可选：触发后是否重置/降低概率？根据需要决定
            #     # self.current_reply_probability = self.base_reply_probability # 例如，触发后降回基础概率
            #     # self.current_reply_probability *= 0.5 # 例如，触发后概率减半
            # else:
            #      logger.debug(f"回复概率评估未触发。概率: {self.current_reply_probability:.4f}")
            return trigger
        else:
            # logger.debug(f"Reply evaluation check: Below threshold or zero probability. Probability: {self.current_reply_probability:.4f}")
            return False


class InterestManager:
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                # Double-check locking
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                # 确保初始化也只执行一次
                if not self._initialized:
                    logger.info("Initializing InterestManager singleton...")
                    # key: stream_id (str), value: InterestChatting instance
                    self.interest_dict: dict[str, InterestChatting] = {}
                    # 保留旧的快照文件路径变量，尽管此任务不再写入
                    # self._snapshot_log_file_path = os.path.join(LOG_DIRECTORY, LOG_FILENAME)
                    # 定义新的历史日志文件路径
                    self._history_log_file_path = os.path.join(LOG_DIRECTORY, HISTORY_LOG_FILENAME)
                    self._ensure_log_directory()
                    self._cleanup_task = None
                    self._logging_task = None  # 添加日志任务变量
                    self._initialized = True
                    logger.info("InterestManager initialized.")  # 修改日志消息
                    self._decay_task = None  # 新增：衰减任务变量

    def _ensure_log_directory(self):
        """确保日志目录存在"""
        try:
            os.makedirs(LOG_DIRECTORY, exist_ok=True)
            logger.info(f"Log directory '{LOG_DIRECTORY}' ensured.")
        except OSError as e:
            logger.error(f"Error creating log directory '{LOG_DIRECTORY}': {e}")

    async def _periodic_cleanup_task(self, interval_seconds: int, max_age_seconds: int):
        """后台清理任务的异步函数"""
        while True:
            await asyncio.sleep(interval_seconds)
            logger.info(f"运行定期清理 (间隔: {interval_seconds}秒)...")
            self.cleanup_inactive_chats(max_age_seconds=max_age_seconds)

    async def _periodic_log_task(self, interval_seconds: int):
        """后台日志记录任务的异步函数 (记录历史数据，包含 group_name)"""
        while True:
            await asyncio.sleep(interval_seconds)
            # logger.debug(f"运行定期历史记录 (间隔: {interval_seconds}秒)...")
            try:
                current_timestamp = time.time()
                all_states = self.get_all_interest_states()  # 获取当前所有状态

                # 以追加模式打开历史日志文件
                with open(self._history_log_file_path, "a", encoding="utf-8") as f:
                    count = 0
                    for stream_id, state in all_states.items():
                        # *** Get group name from ChatManager ***
                        group_name = stream_id  # Default to stream_id
                        try:
                            # Use the imported chat_manager instance
                            chat_stream = chat_manager.get_stream(stream_id)
                            if chat_stream and chat_stream.group_info:
                                group_name = chat_stream.group_info.group_name
                            elif chat_stream and not chat_stream.group_info:
                                # Handle private chats - maybe use user nickname?
                                group_name = (
                                    f"私聊_{chat_stream.user_info.user_nickname}"
                                    if chat_stream.user_info
                                    else stream_id
                                )
                        except Exception as e:
                            logger.warning(f"Could not get group name for stream_id {stream_id}: {e}")
                            # Fallback to stream_id is already handled by default value

                        log_entry = {
                            "timestamp": round(current_timestamp, 2),
                            "stream_id": stream_id,
                            "interest_level": state.get("interest_level", 0.0),  # 确保有默认值
                            "group_name": group_name,  # *** Add group_name ***
                            # --- 新增：记录概率相关信息 ---
                            "reply_probability": state.get("current_reply_probability", 0.0),
                            "is_above_threshold": state.get("is_above_threshold", False),
                            # --- 结束新增 ---
                        }
                        # 将每个条目作为单独的 JSON 行写入
                        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                        count += 1
                # logger.debug(f"Successfully appended {count} interest history entries to {self._history_log_file_path}")

                # 注意：不再写入快照文件 interest_log.json
                # 如果需要快照文件，可以在这里单独写入 self._snapshot_log_file_path
                # 例如：
                # with open(self._snapshot_log_file_path, 'w', encoding='utf-8') as snap_f:
                #     json.dump(all_states, snap_f, indent=4, ensure_ascii=False)
                # logger.debug(f"Successfully wrote snapshot to {self._snapshot_log_file_path}")

            except IOError as e:
                logger.error(f"Error writing interest history log to {self._history_log_file_path}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error during periodic history logging: {e}")

    async def _periodic_decay_task(self):
        """后台衰减任务的异步函数，每秒更新一次所有实例的衰减"""
        while True:
            await asyncio.sleep(1)  # 每秒运行一次
            current_time = time.time()
            # logger.debug("Running periodic decay calculation...") # 调试日志，可能过于频繁

            # 创建字典项的快照进行迭代，避免在迭代时修改字典的问题
            items_snapshot = list(self.interest_dict.items())
            count = 0
            for stream_id, chatting in items_snapshot:
                try:
                    # 调用 InterestChatting 实例的衰减方法
                    chatting._calculate_decay(current_time)
                    count += 1
                except Exception as e:
                    logger.error(f"Error calculating decay for stream_id {stream_id}: {e}")
            # if count > 0: # 仅在实际处理了项目时记录日志，避免空闲时刷屏
            #     logger.debug(f"Applied decay to {count} streams.")

    async def start_background_tasks(self):
        """启动清理，启动衰减，启动记录，启动启动启动启动启动"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._periodic_cleanup_task(
                    interval_seconds=CLEANUP_INTERVAL_SECONDS, max_age_seconds=INACTIVE_THRESHOLD_SECONDS
                )
            )
            logger.info(
                f"已创建定期清理任务。间隔时间: {CLEANUP_INTERVAL_SECONDS}秒, 不活跃阈值: {INACTIVE_THRESHOLD_SECONDS}秒"
            )
        else:
            logger.warning("跳过创建清理任务:任务已在运行或存在。")

        if self._logging_task is None or self._logging_task.done():
            self._logging_task = asyncio.create_task(self._periodic_log_task(interval_seconds=LOG_INTERVAL_SECONDS))
            logger.info(f"已创建定期日志任务。间隔时间: {LOG_INTERVAL_SECONDS}秒")
        else:
            logger.warning("跳过创建日志任务:任务已在运行或存在。")

        # 启动新的衰减任务
        if self._decay_task is None or self._decay_task.done():
            self._decay_task = asyncio.create_task(self._periodic_decay_task())
            logger.info("已创建定期衰减任务。间隔时间: 1秒")
        else:
            logger.warning("跳过创建衰减任务:任务已在运行或存在。")

    def get_all_interest_states(self) -> dict[str, dict]:
        """获取所有聊天流的当前兴趣状态"""
        # 不再需要 current_time, 因为 get_state 现在不接收它
        states = {}
        # 创建副本以避免在迭代时修改字典
        items_snapshot = list(self.interest_dict.items())
        for stream_id, chatting in items_snapshot:
            try:
                # 直接调用 get_state，它会使用内部的 get_interest 获取已更新的值
                states[stream_id] = chatting.get_state()
            except Exception as e:
                logger.warning(f"Error getting state for stream_id {stream_id}: {e}")
        return states

    def get_interest_chatting(self, stream_id: str) -> Optional[InterestChatting]:
        """获取指定流的 InterestChatting 实例，如果不存在则返回 None"""
        return self.interest_dict.get(stream_id)

    def _get_or_create_interest_chatting(self, stream_id: str) -> InterestChatting:
        """获取或创建指定流的 InterestChatting 实例 (线程安全)"""
        if stream_id not in self.interest_dict:
            logger.debug(f"创建兴趣流: {stream_id}")
            # --- 修改：创建时传入概率相关参数 (如果需要定制化，否则使用默认值) ---
            self.interest_dict[stream_id] = InterestChatting(
                # decay_rate=..., max_interest=..., # 可以从配置读取
                trigger_threshold=REPLY_TRIGGER_THRESHOLD,  # 使用全局常量
                base_reply_probability=BASE_REPLY_PROBABILITY,
                increase_rate=PROBABILITY_INCREASE_RATE_PER_SECOND,
                decay_factor=PROBABILITY_DECAY_FACTOR_PER_SECOND,
                max_probability=MAX_REPLY_PROBABILITY,
            )
            # --- 结束修改 ---
            # 首次创建时兴趣为 0，由第一次消息的 activate rate 决定初始值
        return self.interest_dict[stream_id]

    def get_interest(self, stream_id: str) -> float:
        """获取指定聊天流当前的兴趣度 (值由后台任务更新)"""
        # current_time = time.time() # 不再需要获取当前时间
        interest_chatting = self._get_or_create_interest_chatting(stream_id)
        # 直接调用修改后的 get_interest，不传入时间
        return interest_chatting.get_interest()

    def increase_interest(self, stream_id: str, value: float):
        """当收到消息时，增加指定聊天流的兴趣度"""
        current_time = time.time()
        interest_chatting = self._get_or_create_interest_chatting(stream_id)
        # 调用修改后的 increase_interest，不再传入 message
        interest_chatting.increase_interest(current_time, value)
        stream_name = chat_manager.get_stream_name(stream_id) or stream_id  # 获取流名称
        logger.debug(
            f"增加了聊天流 {stream_name} 的兴趣度 {value:.2f}，当前值为 {interest_chatting.interest_level:.2f}"
        )  # 更新日志

    def decrease_interest(self, stream_id: str, value: float):
        """降低指定聊天流的兴趣度"""
        current_time = time.time()
        # 尝试获取，如果不存在则不做任何事
        interest_chatting = self.get_interest_chatting(stream_id)
        if interest_chatting:
            interest_chatting.decrease_interest(current_time, value)
            stream_name = chat_manager.get_stream_name(stream_id) or stream_id  # 获取流名称
            logger.debug(
                f"降低了聊天流 {stream_name} 的兴趣度 {value:.2f}，当前值为 {interest_chatting.interest_level:.2f}"
            )
        else:
            stream_name = chat_manager.get_stream_name(stream_id) or stream_id  # 获取流名称
            logger.warning(f"尝试降低不存在的聊天流 {stream_name} 的兴趣度")

    def cleanup_inactive_chats(self, max_age_seconds=INACTIVE_THRESHOLD_SECONDS):
        """
        清理长时间不活跃的聊天流记录
        max_age_seconds: 超过此时间未更新的将被清理
        """
        current_time = time.time()
        keys_to_remove = []
        initial_count = len(self.interest_dict)
        # with self._lock: # 如果需要锁整个迭代过程
        # 创建副本以避免在迭代时修改字典
        items_snapshot = list(self.interest_dict.items())

        for stream_id, chatting in items_snapshot:
            # 先计算当前兴趣，确保是最新的
            # 加锁保护 chatting 对象状态的读取和可能的修改
            # with self._lock: # 如果 InterestChatting 内部操作不是原子的
            last_interaction = chatting.last_interaction_time  # 使用最后交互时间
            should_remove = False
            reason = ""
            # 只有设置了 max_age_seconds 才检查时间
            if (
                max_age_seconds is not None and (current_time - last_interaction) > max_age_seconds
            ):  # 使用 last_interaction
                should_remove = True
                reason = f"inactive time ({current_time - last_interaction:.0f}s) > max age ({max_age_seconds}s)"  # 更新日志信息

            if should_remove:
                keys_to_remove.append(stream_id)
                stream_name = chat_manager.get_stream_name(stream_id) or stream_id  # 获取流名称
                logger.debug(f"Marking stream {stream_name} for removal. Reason: {reason}")

        if keys_to_remove:
            logger.info(f"清理识别到 {len(keys_to_remove)} 个不活跃/低兴趣的流。")
            # with self._lock: # 确保删除操作的原子性
            for key in keys_to_remove:
                # 再次检查 key 是否存在，以防万一在迭代和删除之间状态改变
                if key in self.interest_dict:
                    del self.interest_dict[key]
                    stream_name = chat_manager.get_stream_name(key) or key  # 获取流名称
                    logger.debug(f"移除了流: {stream_name}")
            final_count = initial_count - len(keys_to_remove)
            logger.info(f"清理完成。移除了 {len(keys_to_remove)} 个流。当前数量: {final_count}")
        else:
            logger.info(f"清理完成。没有流符合移除条件。当前数量: {initial_count}")
