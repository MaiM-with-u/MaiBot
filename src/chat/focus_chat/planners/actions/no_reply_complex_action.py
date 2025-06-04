import asyncio
import traceback
from src.common.logger_manager import get_logger
from src.chat.utils.timer_calculator import Timer
from src.chat.focus_chat.planners.actions.base_action import BaseAction, register_action
from typing import Tuple, List
from src.chat.heart_flow.observation.observation import Observation
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.focus_chat.hfc_utils import parse_thinking_id_to_timestamp

logger = get_logger("action_taken")

# 常量定义
WAITING_TIME_THRESHOLD = 1200  # 等待新消息时间阈值，单位秒


@register_action
class NoReplyAction(BaseAction):
    """不回复动作处理类

    处理决定不回复的动作。
    """

    action_name = "no_reply"
    action_description = "不回复"
    action_parameters = {}
    action_require = [
        "话题无关/无聊/不感兴趣/不懂",
        "聊天记录中最新一条消息是你自己发的且无人回应你",
        "你连续发送了太多消息，且无人回复",
    ]
    default = True

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        observations: List[Observation],
        log_prefix: str,
        shutting_down: bool = False,
        **kwargs,
    ):
        """初始化不回复动作处理器

        Args:
            action_name: 动作名称
            action_data: 动作数据
            reasoning: 执行该动作的理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            observations: 观察列表
            log_prefix: 日志前缀
            shutting_down: 是否正在关闭
        """
        super().__init__(action_data, reasoning, cycle_timers, thinking_id)
        self.observations = observations
        self.log_prefix = log_prefix
        self._shutting_down = shutting_down

    async def handle_action(self) -> Tuple[bool, str]:
        """
        处理不回复的情况

        工作流程：
        1. 等待新消息、超时或关闭信号
        2. 根据等待结果更新连续不回复计数
        3. 如果达到阈值，触发回调

        Returns:
            Tuple[bool, str]: (是否执行成功, 空字符串)
        """
        logger.info(f"{self.log_prefix} 决定不回复: {self.reasoning}")

        observation = self.observations[0] if self.observations else None

        try:
            with Timer("等待新消息", self.cycle_timers):
                # 等待新消息、超时或关闭信号，并获取结果
                await self._wait_for_new_message(observation, self.thinking_id, self.log_prefix)

            return True, ""  # 不回复动作没有回复文本

        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} 处理 'no_reply' 时等待被中断 (CancelledError)")
            raise
        except Exception as e:  # 捕获调用管理器或其他地方可能发生的错误
            logger.error(f"{self.log_prefix} 处理 'no_reply' 时发生错误: {e}")
            logger.error(traceback.format_exc())
            return False, ""

    async def _wait_for_new_message(self, observation: ChattingObservation, thinking_id: str, log_prefix: str) -> bool:
        """
        等待新消息 或 检测到关闭信号

        参数:
            observation: 观察实例
            thinking_id: 思考ID
            log_prefix: 日志前缀

        返回:
            bool: 是否检测到新消息 (如果因关闭信号退出则返回 False)
        """
        wait_start_time = asyncio.get_event_loop().time()
        while True:
            # --- 在每次循环开始时检查关闭标志 ---
            if self._shutting_down:
                logger.info(f"{log_prefix} 等待新消息时检测到关闭信号，中断等待。")
                return False  # 表示因为关闭而退出
            # -----------------------------------

            thinking_id_timestamp = parse_thinking_id_to_timestamp(thinking_id)

            # 检查新消息
            if await observation.has_new_messages_since(thinking_id_timestamp):
                logger.info(f"{log_prefix} 检测到新消息")
                return True

            # 检查超时 (放在检查新消息和关闭之后)
            if asyncio.get_event_loop().time() - wait_start_time > WAITING_TIME_THRESHOLD:
                logger.warning(f"{log_prefix} 等待新消息超时({WAITING_TIME_THRESHOLD}秒)")
                return False

            try:
                # 短暂休眠，让其他任务有机会运行，并能更快响应取消或关闭
                await asyncio.sleep(0.5)  # 缩短休眠时间
            except asyncio.CancelledError:
                # 如果在休眠时被取消，再次检查关闭标志
                # 如果是正常关闭，则不需要警告
                if not self._shutting_down:
                    logger.warning(f"{log_prefix} _wait_for_new_message 的休眠被意外取消")
                # 无论如何，重新抛出异常，让上层处理
                raise
