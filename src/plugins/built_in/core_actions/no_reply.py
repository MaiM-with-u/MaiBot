import random
import time
from typing import Tuple, List
from collections import deque

# 导入新插件系统
from src.plugin_system import BaseAction, ActionActivationType, ChatMode

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import message_api
from src.config.config import global_config


logger = get_logger("no_reply_action")


class NoReplyAction(BaseAction):
    """不回复动作，支持waiting和breaking两种形式.

    waiting形式:
    - 只要有新消息就结束动作
    - 记录新消息的兴趣度到列表（最多保留最近三项）
    - 如果最近三次动作都是no_reply，且最近新消息列表兴趣度之和小于阈值，就进入breaking形式

    breaking形式:
    - 和原有逻辑一致，需要消息满足一定数量或累计一定兴趣值才结束动作
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
    
    # 最近三次no_reply的新消息兴趣度记录
    _recent_interest_records: deque = deque(maxlen=3)

    # 兴趣值退出阈值
    _interest_exit_threshold = 3.0
    # 消息数量退出阈值
    _min_exit_message_count = 3
    _max_exit_message_count = 6

    # 动作参数定义
    action_parameters = {}

    # 动作使用场景
    action_require = [""]

    # 关联类型
    associated_types = []

    async def execute(self) -> Tuple[bool, str]:
        """执行不回复动作"""

        try:
            reason = self.action_data.get("reason", "")
            start_time = self.action_data.get("loop_start_time", time.time())
            check_interval = 0.6

            # 判断使用哪种形式
            form_type = self._determine_form_type()
            
            logger.info(f"{self.log_prefix} 选择不回复(第{NoReplyAction._consecutive_count + 1}次)，使用{form_type}形式，原因: {reason}")

            # 增加连续计数（在确定要执行no_reply时才增加）
            NoReplyAction._consecutive_count += 1

            if form_type == "waiting":
                return await self._execute_waiting_form(start_time, check_interval)
            else:
                return await self._execute_breaking_form(start_time, check_interval)

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

    def _determine_form_type(self) -> str:
        """判断使用哪种形式的no_reply"""
        # 如果连续no_reply次数少于3次，使用waiting形式
        if NoReplyAction._consecutive_count < 3:
            return "waiting"
        
        # 如果最近三次记录不足，使用waiting形式
        if len(NoReplyAction._recent_interest_records) < 3:
            return "waiting"
        
        # 计算最近三次记录的兴趣度总和
        total_recent_interest = sum(NoReplyAction._recent_interest_records)
        
        # 获取当前聊天频率和意愿系数
        talk_frequency = global_config.chat.get_current_talk_frequency(self.chat_id)
        willing_amplifier = global_config.chat.willing_amplifier
        
        # 计算调整后的阈值
        adjusted_threshold = self._interest_exit_threshold / talk_frequency / willing_amplifier
        
        logger.info(f"{self.log_prefix} 最近三次兴趣度总和: {total_recent_interest:.2f}, 调整后阈值: {adjusted_threshold:.2f}")
        
        # 如果兴趣度总和小于阈值，进入breaking形式
        if total_recent_interest < adjusted_threshold:
            logger.info(f"{self.log_prefix} 兴趣度不足，进入breaking形式")
            return "breaking"
        else:
            logger.info(f"{self.log_prefix} 兴趣度充足，继续使用waiting形式")
            return "waiting"

    async def _execute_waiting_form(self, start_time: float, check_interval: float) -> Tuple[bool, str]:
        """执行waiting形式的no_reply"""
        import asyncio
        
        logger.info(f"{self.log_prefix} 进入waiting形式，等待任何新消息")
        
        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            # 检查新消息
            recent_messages_dict = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=start_time,
                end_time=current_time,
                filter_mai=True,
                filter_command=True,
            )
            new_message_count = len(recent_messages_dict)

            # waiting形式：只要有新消息就结束
            if new_message_count > 0:
                # 计算新消息的总兴趣度
                total_interest = 0.0
                for msg_dict in recent_messages_dict:
                    interest_value = msg_dict.get("interest_value", 0.0)
                    if msg_dict.get("processed_plain_text", ""):
                        total_interest += interest_value * global_config.chat.willing_amplifier

                # 记录到最近兴趣度列表
                NoReplyAction._recent_interest_records.append(total_interest)
                
                logger.info(
                    f"{self.log_prefix} waiting形式检测到{new_message_count}条新消息，总兴趣度: {total_interest:.2f}，结束等待"
                )
                
                exit_reason = f"{global_config.bot.nickname}（你）看到了{new_message_count}条新消息，可以考虑一下是否要进行回复"
                await self.store_action_info(
                    action_build_into_prompt=False,
                    action_prompt_display=exit_reason,
                    action_done=True,
                )
                return True, f"waiting形式检测到{new_message_count}条新消息，结束等待 (等待时间: {elapsed_time:.1f}秒)"

            # 每10秒输出一次等待状态
            if int(elapsed_time) > 0 and int(elapsed_time) % 10 == 0:
                logger.debug(f"{self.log_prefix} waiting形式已等待{elapsed_time:.0f}秒，继续等待新消息...")
                await asyncio.sleep(1)

            # 短暂等待后继续检查
            await asyncio.sleep(check_interval)

    async def _execute_breaking_form(self, start_time: float, check_interval: float) -> Tuple[bool, str]:
        """执行breaking形式的no_reply（原有逻辑）"""
        import asyncio
        
        # 随机生成本次等待需要的新消息数量阈值
        exit_message_count_threshold = random.randint(self._min_exit_message_count, self._max_exit_message_count)
        
        logger.info(f"{self.log_prefix} 进入breaking形式，需要{exit_message_count_threshold}条消息或足够兴趣度")

        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            # 检查新消息
            recent_messages_dict = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=start_time,
                end_time=current_time,
                filter_mai=True,
                filter_command=True,
            )
            new_message_count = len(recent_messages_dict)

            # 检查消息数量是否达到阈值
            talk_frequency = global_config.chat.get_current_talk_frequency(self.chat_id)
            modified_exit_count_threshold = (exit_message_count_threshold / talk_frequency) / global_config.chat.willing_amplifier
            
            if new_message_count >= modified_exit_count_threshold:
                # 记录兴趣度到列表
                total_interest = 0.0
                for msg_dict in recent_messages_dict:
                    interest_value = msg_dict.get("interest_value", 0.0)
                    if msg_dict.get("processed_plain_text", ""):
                        total_interest += interest_value * global_config.chat.willing_amplifier
                
                NoReplyAction._recent_interest_records.append(total_interest)
                
                logger.info(
                    f"{self.log_prefix} breaking形式累计消息数量达到{new_message_count}条(>{modified_exit_count_threshold})，结束等待"
                )
                exit_reason = f"{global_config.bot.nickname}（你）看到了{new_message_count}条新消息，可以考虑一下是否要进行回复"
                await self.store_action_info(
                    action_build_into_prompt=False,
                    action_prompt_display=exit_reason,
                    action_done=True,
                )
                return True, f"breaking形式累计消息数量达到{new_message_count}条，结束等待 (等待时间: {elapsed_time:.1f}秒)"

            # 检查累计兴趣值
            if new_message_count > 0:
                accumulated_interest = 0.0
                for msg_dict in recent_messages_dict:
                    text = msg_dict.get("processed_plain_text", "")
                    interest_value = msg_dict.get("interest_value", 0.0)
                    if text:
                        accumulated_interest += interest_value * global_config.chat.willing_amplifier
                
                # 只在兴趣值变化时输出log
                if not hasattr(self, "_last_accumulated_interest") or accumulated_interest != self._last_accumulated_interest:
                    logger.info(f"{self.log_prefix} breaking形式当前累计兴趣值: {accumulated_interest:.2f}, 当前聊天频率: {talk_frequency:.2f}")
                    self._last_accumulated_interest = accumulated_interest
                
                if accumulated_interest >= self._interest_exit_threshold / talk_frequency:
                    # 记录兴趣度到列表
                    NoReplyAction._recent_interest_records.append(accumulated_interest)
                    
                    logger.info(
                        f"{self.log_prefix} breaking形式累计兴趣值达到{accumulated_interest:.2f}(>{self._interest_exit_threshold / talk_frequency})，结束等待"
                    )
                    exit_reason = f"{global_config.bot.nickname}（你）感觉到了大家浓厚的兴趣（兴趣值{accumulated_interest:.1f}），决定重新加入讨论"
                    await self.store_action_info(
                        action_build_into_prompt=False,
                        action_prompt_display=exit_reason,
                        action_done=True,
                    )
                    return (
                        True,
                        f"breaking形式累计兴趣值达到{accumulated_interest:.2f}，结束等待 (等待时间: {elapsed_time:.1f}秒)",
                    )

            # 每10秒输出一次等待状态
            if int(elapsed_time) > 0 and int(elapsed_time) % 10 == 0:
                logger.debug(
                    f"{self.log_prefix} breaking形式已等待{elapsed_time:.0f}秒，累计{new_message_count}条消息，继续等待..."
                )
                await asyncio.sleep(1)

            # 短暂等待后继续检查
            await asyncio.sleep(check_interval)

    @classmethod
    def reset_consecutive_count(cls):
        """重置连续计数器和兴趣度记录"""
        cls._consecutive_count = 0
        cls._recent_interest_records.clear()
        logger.debug("NoReplyAction连续计数器和兴趣度记录已重置")

    @classmethod
    def get_recent_interest_records(cls) -> List[float]:
        """获取最近的兴趣度记录"""
        return list(cls._recent_interest_records)

    @classmethod
    def get_consecutive_count(cls) -> int:
        """获取连续计数"""
        return cls._consecutive_count
