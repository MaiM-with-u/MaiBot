import enum
import time
import random
from typing import List, Tuple, Optional
from src.common.logger_manager import get_logger
from src.manager.mood_manager import mood_manager
from src.config.config import global_config

logger = get_logger("mai_state")


# -- 状态相关的可配置参数 (可以从 glocal_config 加载) --
# The line `enable_unlimited_hfc_chat = False` is setting a configuration parameter that controls
# whether a specific debugging feature is enabled or not. When `enable_unlimited_hfc_chat` is set to
# `False`, it means that the debugging feature for unlimited focused chatting is disabled.
# enable_unlimited_hfc_chat = True  # 调试用：无限专注聊天
enable_unlimited_hfc_chat = False
prevent_offline_state = True
# 目前默认不启用OFFLINE状态

# 不同状态下普通聊天的最大消息数
base_normal_chat_num = global_config.base_normal_chat_num
base_focused_chat_num = global_config.base_focused_chat_num


MAX_NORMAL_CHAT_NUM_PEEKING = int(base_normal_chat_num / 2)
MAX_NORMAL_CHAT_NUM_NORMAL = base_normal_chat_num
MAX_NORMAL_CHAT_NUM_FOCUSED = base_normal_chat_num + 1

# 不同状态下专注聊天的最大消息数
MAX_FOCUSED_CHAT_NUM_PEEKING = int(base_focused_chat_num / 2)
MAX_FOCUSED_CHAT_NUM_NORMAL = base_focused_chat_num
MAX_FOCUSED_CHAT_NUM_FOCUSED = base_focused_chat_num + 2

# -- 状态定义 --


class MaiState(enum.Enum):
    """
    聊天状态:
    OFFLINE: 不在线：回复概率极低，不会进行任何聊天
    PEEKING: 看一眼手机：回复概率较低，会进行一些普通聊天
    NORMAL_CHAT: 正常看手机：回复概率较高，会进行一些普通聊天和少量的专注聊天
    FOCUSED_CHAT: 专注聊天：回复概率极高，会进行专注聊天和少量的普通聊天
    """

    OFFLINE = "不在线"
    PEEKING = "看一眼手机"
    NORMAL_CHAT = "正常看手机"
    FOCUSED_CHAT = "专心看手机"

    def get_normal_chat_max_num(self):
        # 调试用
        if enable_unlimited_hfc_chat:
            return 1000

        if self == MaiState.OFFLINE:
            return 0
        elif self == MaiState.PEEKING:
            return MAX_NORMAL_CHAT_NUM_PEEKING
        elif self == MaiState.NORMAL_CHAT:
            return MAX_NORMAL_CHAT_NUM_NORMAL
        elif self == MaiState.FOCUSED_CHAT:
            return MAX_NORMAL_CHAT_NUM_FOCUSED
        return None

    def get_focused_chat_max_num(self):
        # 调试用
        if enable_unlimited_hfc_chat:
            return 1000

        if self == MaiState.OFFLINE:
            return 0
        elif self == MaiState.PEEKING:
            return MAX_FOCUSED_CHAT_NUM_PEEKING
        elif self == MaiState.NORMAL_CHAT:
            return MAX_FOCUSED_CHAT_NUM_NORMAL
        elif self == MaiState.FOCUSED_CHAT:
            return MAX_FOCUSED_CHAT_NUM_FOCUSED
        return None


class MaiStateInfo:
    def __init__(self):
        self.mai_status: MaiState = MaiState.OFFLINE
        self.mai_status_history: List[Tuple[MaiState, float]] = []  # 历史状态，包含 状态，时间戳
        self.last_status_change_time: float = time.time()  # 状态最后改变时间
        self.last_min_check_time: float = time.time()  # 上次1分钟规则检查时间

        # Mood management is now part of MaiStateInfo
        self.mood_manager = mood_manager  # Use singleton instance

    def update_mai_status(self, new_status: MaiState) -> bool:
        """
        更新聊天状态。

        Args:
            new_status: 新的 MaiState 状态。

        Returns:
            bool: 如果状态实际发生了改变则返回 True，否则返回 False。
        """
        if new_status != self.mai_status:
            self.mai_status = new_status
            current_time = time.time()
            self.last_status_change_time = current_time
            self.last_min_check_time = current_time  # Reset 1-min check on any state change
            self.mai_status_history.append((new_status, current_time))
            logger.info(f"麦麦状态更新为: {self.mai_status.value}")
            return True
        else:
            return False

    def reset_state_timer(self):
        """
        重置状态持续时间计时器和一分钟规则检查计时器。
        通常在状态保持不变但需要重新开始计时的情况下调用（例如，保持 OFFLINE）。
        """
        current_time = time.time()
        self.last_status_change_time = current_time
        self.last_min_check_time = current_time  # Also reset the 1-min check timer
        logger.debug("MaiStateInfo 状态计时器已重置。")

    def get_mood_prompt(self) -> str:
        """获取当前的心情提示词"""
        # Delegate to the internal mood manager
        return self.mood_manager.get_mood_prompt()

    def get_current_state(self) -> MaiState:
        """获取当前的 MaiState"""
        return self.mai_status


class MaiStateManager:
    """管理 Mai 的整体状态转换逻辑"""

    def __init__(self):
        pass

    @staticmethod
    def check_and_decide_next_state(current_state_info: MaiStateInfo) -> Optional[MaiState]:
        """
        根据当前状态和规则检查是否需要转换状态，并决定下一个状态。

        Args:
            current_state_info: 当前的 MaiStateInfo 实例。

        Returns:
            Optional[MaiState]: 如果需要转换，返回目标 MaiState；否则返回 None。
        """
        current_time = time.time()
        current_status = current_state_info.mai_status
        time_in_current_status = current_time - current_state_info.last_status_change_time
        time_since_last_min_check = current_time - current_state_info.last_min_check_time
        next_state: Optional[MaiState] = None

        # 辅助函数：根据 prevent_offline_state 标志调整目标状态
        def _resolve_offline(candidate_state: MaiState) -> MaiState:
            if prevent_offline_state and candidate_state == MaiState.OFFLINE:
                logger.debug("阻止进入 OFFLINE，改为 PEEKING")
                return MaiState.PEEKING
            return candidate_state

        if current_status == MaiState.OFFLINE:
            logger.info("当前[离线]，没看手机，思考要不要上线看看......")
        elif current_status == MaiState.PEEKING:
            logger.info("当前[看一眼手机]，思考要不要继续聊下去......")
        elif current_status == MaiState.NORMAL_CHAT:
            logger.info("当前在[正常看手机]思考要不要继续聊下去......")
        elif current_status == MaiState.FOCUSED_CHAT:
            logger.info("当前在[专心看手机]思考要不要继续聊下去......")

        # 1. 麦麦每分钟都有概率离线
        if time_since_last_min_check >= 60:
            if current_status != MaiState.OFFLINE:
                if random.random() < 0.03:  # 3% 概率切换到 OFFLINE
                    potential_next = MaiState.OFFLINE
                    resolved_next = _resolve_offline(potential_next)
                    logger.debug(f"概率触发下线，resolve 为 {resolved_next.value}")
                    # 只有当解析后的状态与当前状态不同时才设置 next_state
                    if resolved_next != current_status:
                        next_state = resolved_next

        # 2. 状态持续时间规则 (只有在规则1没有触发状态改变时才检查)
        if next_state is None:
            time_limit_exceeded = False
            choices_list = []
            weights = []
            rule_id = ""

            if current_status == MaiState.OFFLINE:
                # 注意：即使 prevent_offline_state=True，也可能从初始的 OFFLINE 状态启动
                if time_in_current_status >= 60:
                    time_limit_exceeded = True
                    rule_id = "2.1 (From OFFLINE)"
                    weights = [30, 30, 20, 20]
                    choices_list = [MaiState.PEEKING, MaiState.NORMAL_CHAT, MaiState.FOCUSED_CHAT, MaiState.OFFLINE]
            elif current_status == MaiState.PEEKING:
                if time_in_current_status >= 600:  # PEEKING 最多持续 600 秒
                    time_limit_exceeded = True
                    rule_id = "2.2 (From PEEKING)"
                    weights = [70, 20, 10]
                    choices_list = [MaiState.OFFLINE, MaiState.NORMAL_CHAT, MaiState.FOCUSED_CHAT]
            elif current_status == MaiState.NORMAL_CHAT:
                if time_in_current_status >= 300:  # NORMAL_CHAT 最多持续 300 秒
                    time_limit_exceeded = True
                    rule_id = "2.3 (From NORMAL_CHAT)"
                    weights = [50, 50]
                    choices_list = [MaiState.OFFLINE, MaiState.FOCUSED_CHAT]
            elif current_status == MaiState.FOCUSED_CHAT:
                if time_in_current_status >= 600:  # FOCUSED_CHAT 最多持续 600 秒
                    time_limit_exceeded = True
                    rule_id = "2.4 (From FOCUSED_CHAT)"
                    weights = [80, 20]
                    choices_list = [MaiState.OFFLINE, MaiState.NORMAL_CHAT]

            if time_limit_exceeded:
                next_state_candidate = random.choices(choices_list, weights=weights, k=1)[0]
                resolved_candidate = _resolve_offline(next_state_candidate)
                logger.debug(
                    f"规则{rule_id}：时间到，随机选择 {next_state_candidate.value}，resolve 为 {resolved_candidate.value}"
                )
                next_state = resolved_candidate  # 直接使用解析后的状态

            # 注意：enable_unlimited_hfc_chat 优先级高于 prevent_offline_state
            # 如果触发了这个，它会覆盖上面规则2设置的 next_state
            if enable_unlimited_hfc_chat:
                logger.debug("调试用：开挂了，强制切换到专注聊天")
                next_state = MaiState.FOCUSED_CHAT

        # --- 最终决策 --- #
        # 如果决定了下一个状态，且这个状态与当前状态不同，则返回下一个状态
        if next_state is not None and next_state != current_status:
            return next_state
        # 如果决定保持 OFFLINE (next_state == MaiState.OFFLINE) 且当前也是 OFFLINE，
        # 并且是由于持续时间规则触发的，返回 OFFLINE 以便调用者可以重置计时器。
        # 注意：这个分支只有在 prevent_offline_state = False 时才可能被触发。
        elif next_state == MaiState.OFFLINE and current_status == MaiState.OFFLINE and time_in_current_status >= 60:
            logger.debug("决定保持 OFFLINE (持续时间规则)，返回 OFFLINE 以提示重置计时器。")
            return MaiState.OFFLINE  # Return OFFLINE to signal caller that timer reset might be needed
        else:
            # 1. next_state is None (没有触发任何转换规则)
            # 2. next_state is not None 但等于 current_status (例如规则1想切OFFLINE但被resolve成PEEKING，而当前已经是PEEKING)
            # 3. next_state is OFFLINE, current is OFFLINE, 但不是因为时间规则触发 (例如初始状态还没到60秒)
            return None  # 没有状态转换发生或无需重置计时器
