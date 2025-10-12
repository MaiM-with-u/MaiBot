from typing import Tuple

# 导入新插件系统
from src.plugin_system import BaseAction, ActionActivationType

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块
from src.plugin_system.apis import frequency_api, send_api, config_api, generator_api

logger = get_logger("frequency_adjust")


class FrequencyAdjustAction(BaseAction):
    """频率调节动作 - 调整聊天发言频率"""

    activation_type = ActionActivationType.LLM_JUDGE
    parallel_action = False

    # 动作基本信息
    action_name = "frequency_adjust"
    
    action_description = "调整当前聊天的发言频率"

    # 动作参数定义
    action_parameters = {
        "direction": "调整方向：'increase'（增加）或'decrease'（降低）",
    }

    # 动作使用场景
    bot_name = config_api.get_global_config("bot.nickname")
    
    
    action_require = [
        f"当用户提到 {bot_name} 太安静或太活跃时使用",
        f"有人提到 {bot_name} 的发言太多或太少",
        f"需要根据聊天氛围调整 {bot_name} 的活跃度",
    ]

    # 关联类型
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行频率调节动作"""
        try:
            # 1. 获取动作参数
            direction = self.action_data.get("direction")
            # multiply = 1.2
            # multiply = self.action_data.get("multiply")

            if not direction:
                error_msg = "缺少必要的参数：direction或multiply"
                logger.error(f"{self.log_prefix} {error_msg}")
                return False, error_msg

            # 2. 获取当前频率值
            current_frequency = frequency_api.get_current_talk_frequency(self.chat_id)

            # 3. 计算新的频率值（使用比率而不是绝对值）
            # calculated_frequency = current_frequency * multiply
            if direction == "increase":
                calculated_frequency = current_frequency * 1.2
                if calculated_frequency > 1.0:
                    new_frequency = 1.0
                    action_desc = f"增加到最大值"
                    # 记录超出限制的action
                    logger.warning(f"{self.log_prefix} 尝试调整频率超出最大值: current={current_frequency:.2f}, calculated={calculated_frequency:.2f}")
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=f"你尝试调整发言频率到{calculated_frequency:.2f}，但最大值只能为1.0，已设置为最大值",
                        action_done=True,
                    )
                    return True, f"调整发言频率超出限制: {current_frequency:.2f} → {new_frequency:.2f}"
                else:
                    new_frequency = calculated_frequency
                    action_desc = f"增加"
            elif direction == "decrease":
                calculated_frequency = current_frequency * 0.8
                new_frequency = max(0.0, calculated_frequency)
                action_desc = f"降低"
            else:
                error_msg = f"无效的调整方向: {direction}"
                logger.error(f"{self.log_prefix} {error_msg}")
                return False, error_msg

            # 4. 设置新的频率值
            frequency_api.set_talk_frequency_adjust(self.chat_id, new_frequency)

            # 5. 发送反馈消息
            feedback_msg = f"已{action_desc}发言频率：{current_frequency:.2f} → {new_frequency:.2f}"
            result_status, data = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                reply_data={
                    "raw_reply": feedback_msg,
                    "reason": "表达自己已经调整了发言频率，不一定要说具体数值，可以有趣一些",
                },
            )
            

            if result_status:
                for reply_seg in data.reply_set.reply_data:
                    send_data = reply_seg.content
                    await self.send_text(send_data)
                    logger.info(f"{self.log_prefix} {send_data}")

            # 6. 存储动作信息（仅在未超出限制时）
            if calculated_frequency <= 1.0:
                await self.store_action_info(
                    action_build_into_prompt=True,
                    action_prompt_display=f"你{action_desc}了发言频率，从{current_frequency:.2f}调整到{new_frequency:.2f}",
                    action_done=True,
                )

            return True, f"成功调整发言频率: {current_frequency:.2f} → {new_frequency:.2f}"

        except Exception as e:
            error_msg = f"频率调节失败: {str(e)}"
            logger.error(f"{self.log_prefix} {error_msg}", exc_info=True)
            await self.send_text("频率调节失败")
            return False, error_msg