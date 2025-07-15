import random
import time
from typing import Tuple

# 导入新插件系统
from src.plugin_system import BaseAction, ActionActivationType, ChatMode

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import message_api
from src.config.config import global_config


logger = get_logger("core_actions")


class NoReplyAction(BaseAction):
    """不回复动作，根据新消息的兴趣值或数量决定何时结束等待.

    新的等待逻辑:
    1. 新消息累计兴趣值超过阈值 (默认10) 则结束等待
    2. 累计新消息数量达到随机阈值 (默认5-10条) 则结束等待
    """

    focus_activation_type = ActionActivationType.NEVER
    normal_activation_type = ActionActivationType.NEVER
    mode_enable = ChatMode.FOCUS
    parallel_action = False

    # 动作基本信息
    action_name = "no_reply"
    action_description = "暂时不回复消息"

    # 连续no_reply计数器
    _consecutive_count = 0

    # 新增：兴趣值退出阈值
    _interest_exit_threshold = 3.0
    # 新增：消息数量退出阈值
    _min_exit_message_count = 5
    _max_exit_message_count = 10

    # 动作参数定义
    action_parameters = {"reason": "不回复的原因"}

    # 动作使用场景
    action_require = ["你发送了消息，目前无人回复"]

    # 关联类型
    associated_types = []

    async def execute(self) -> Tuple[bool, str]:
        """执行不回复动作"""
        import asyncio

        try:
            # 增加连续计数
            NoReplyAction._consecutive_count += 1
            count = NoReplyAction._consecutive_count

            reason = self.action_data.get("reason", "")
            start_time = self.action_data.get("loop_start_time", time.time())
            check_interval = 0.6  # 每秒检查一次

            # 随机生成本次等待需要的新消息数量阈值
            exit_message_count_threshold = random.randint(self._min_exit_message_count, self._max_exit_message_count)
            logger.info(
                f"{self.log_prefix} 本次no_reply需要 {exit_message_count_threshold} 条新消息或累计兴趣值超过 {self._interest_exit_threshold} 才能打断"
            )

            logger.info(f"{self.log_prefix} 选择不回复(第{count}次)，开始摸鱼，原因: {reason}")

            # 进入等待状态
            while True:
                current_time = time.time()
                elapsed_time = current_time - start_time

                # 1. 检查新消息
                recent_messages_dict = message_api.get_messages_by_time_in_chat(
                    chat_id=self.chat_id, start_time=start_time, end_time=current_time, filter_mai=True
                )
                new_message_count = len(recent_messages_dict)

                # 2. 检查消息数量是否达到阈值
                if new_message_count >= exit_message_count_threshold:
                    logger.info(
                        f"{self.log_prefix} 累计消息数量达到{new_message_count}条(>{exit_message_count_threshold})，结束等待"
                    )
                    exit_reason = f"{global_config.bot.nickname}（你）看到了{new_message_count}条新消息，可以考虑一下是否要进行回复"
                    await self.store_action_info(
                        action_build_into_prompt=False,
                        action_prompt_display=exit_reason,
                        action_done=True,
                    )
                    return True, f"累计消息数量达到{new_message_count}条，结束等待 (等待时间: {elapsed_time:.1f}秒)"

                # 3. 检查累计兴趣值
                if new_message_count > 0:
                    accumulated_interest = 0.0
                    for msg_dict in recent_messages_dict:
                        text = msg_dict.get("processed_plain_text", "")
                        interest_value = msg_dict.get("interest_value", 0.0)
                        if text:
                            accumulated_interest += interest_value
                    logger.info(f"{self.log_prefix} 当前累计兴趣值: {accumulated_interest:.2f}")
                    if accumulated_interest >= self._interest_exit_threshold:
                        logger.info(
                            f"{self.log_prefix} 累计兴趣值达到{accumulated_interest:.2f}(>{self._interest_exit_threshold})，结束等待"
                        )
                        exit_reason = f"{global_config.bot.nickname}（你）感觉到了大家浓厚的兴趣（兴趣值{accumulated_interest:.1f}），决定重新加入讨论"
                        await self.store_action_info(
                            action_build_into_prompt=False,
                            action_prompt_display=exit_reason,
                            action_done=True,
                        )
                        return (
                            True,
                            f"累计兴趣值达到{accumulated_interest:.2f}，结束等待 (等待时间: {elapsed_time:.1f}秒)",
                        )

                # 每10秒输出一次等待状态
                if int(elapsed_time) > 0 and int(elapsed_time) % 10 == 0:
                    logger.debug(
                        f"{self.log_prefix} 已等待{elapsed_time:.0f}秒，累计{new_message_count}条消息，继续等待..."
                    )
                    # 使用 asyncio.sleep(1) 来避免在同一秒内重复打印日志
                    await asyncio.sleep(1)

                # 短暂等待后继续检查
                await asyncio.sleep(check_interval)

        except Exception as e:
            logger.error(f"{self.log_prefix} 不回复动作执行失败: {e}")
            exit_reason = f"执行异常: {str(e)}"
            full_prompt = f"no_reply执行异常: {exit_reason}，你思考是否要进行回复"
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=full_prompt,
                action_done=True,
            )
            return False, f"不回复动作执行失败: {e}"

    @classmethod
    def reset_consecutive_count(cls):
        """重置连续计数器"""
        cls._consecutive_count = 0
        logger.debug("NoReplyAction连续计数器已重置")
