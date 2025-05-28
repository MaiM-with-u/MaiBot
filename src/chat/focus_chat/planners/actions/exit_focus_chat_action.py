import asyncio
import traceback
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.base_action import BaseAction, register_action
from typing import Tuple, List
from src.chat.heart_flow.observation.observation import Observation
from src.chat.message_receive.chat_stream import ChatStream

logger = get_logger("action_taken")


@register_action
class ExitFocusChatAction(BaseAction):
    """退出专注聊天动作处理类

    处理决定退出专注聊天的动作。
    执行后会将所属的sub heartflow转变为normal_chat状态。
    """

    action_name = "exit_focus_chat"
    action_description = "退出专注聊天，转为普通聊天模式"
    action_parameters = {}
    action_require = [
        "很长时间没有回复，你决定退出专注聊天",
        "当前内容不需要持续专注关注，你决定退出专注聊天",
        "聊天内容已经完成，你决定退出专注聊天",
    ]
    default = False

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        observations: List[Observation],
        log_prefix: str,
        chat_stream: ChatStream,
        shutting_down: bool = False,
        **kwargs,
    ):
        """初始化退出专注聊天动作处理器

        Args:
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
        处理退出专注聊天的情况

        工作流程：
        1. 将sub heartflow转换为normal_chat状态
        2. 等待新消息、超时或关闭信号
        3. 根据等待结果更新连续不回复计数
        4. 如果达到阈值，触发回调

        Returns:
            Tuple[bool, str]: (是否执行成功, 状态转换消息)
        """
        try:
            # 转换状态
            status_message = ""
            command = "stop_focus_chat"
            return True, status_message, command

        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} 处理 'exit_focus_chat' 时等待被中断 (CancelledError)")
            raise
        except Exception as e:
            error_msg = f"处理 'exit_focus_chat' 时发生错误: {str(e)}"
            logger.error(f"{self.log_prefix} {error_msg}")
            logger.error(traceback.format_exc())
            return False, "", ""
