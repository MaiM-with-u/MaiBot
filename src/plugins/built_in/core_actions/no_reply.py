import random
import time
import json
from typing import Tuple

# 导入新插件系统
from src.plugin_system import BaseAction, ActionActivationType, ChatMode

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import message_api, llm_api
from src.config.config import global_config
from json_repair import repair_json

logger = get_logger("core_actions")


class NoReplyAction(BaseAction):
    """不回复动作，使用智能判断机制决定何时结束等待

    新的等待逻辑：
    - 每0.2秒检查是否有新消息（提高响应性）
    - 如果累计消息数量达到阈值（默认20条），直接结束等待
    - 有新消息时进行LLM判断，但最快1秒一次（防止过于频繁）
    - 如果判断需要回复，则结束等待；否则继续等待
    - 达到最大超时时间后强制结束
    """

    focus_activation_type = ActionActivationType.ALWAYS
    # focus_activation_type = ActionActivationType.RANDOM
    normal_activation_type = ActionActivationType.NEVER
    mode_enable = ChatMode.FOCUS
    parallel_action = False

    # 动作基本信息
    action_name = "no_reply"
    action_description = "暂时不回复消息"

    # 连续no_reply计数器
    _consecutive_count = 0

    # LLM判断的最小间隔时间
    _min_judge_interval = 1.0  # 最快1秒一次LLM判断

    # 自动结束的消息数量阈值
    _auto_exit_message_count = 20  # 累计20条消息自动结束

    # 最大等待超时时间
    _max_timeout = 600  # 1200秒

    # 跳过LLM判断的配置
    _skip_judge_when_tired = True
    _skip_probability = 0.5

    # 新增：回复频率退出专注模式的配置
    _frequency_check_window = 600  # 频率检查窗口时间（秒）

    # 动作参数定义
    action_parameters = {"reason": "不回复的原因"}

    # 动作使用场景
    action_require = ["你发送了消息，目前无人回复"]

    # 关联类型
    associated_types = []

    async def execute(self) -> Tuple[bool, str]:
        """执行不回复动作，有新消息时进行判断，但最快1秒一次"""
        import asyncio

        try:
            # 增加连续计数
            NoReplyAction._consecutive_count += 1
            count = NoReplyAction._consecutive_count

            reason = self.action_data.get("reason", "")
            start_time = time.time()
            last_judge_time = 0  # 上次进行LLM判断的时间
            min_judge_interval = self._min_judge_interval  # 最小判断间隔，从配置获取
            check_interval = 0.2  # 检查新消息的间隔，设为0.2秒提高响应性

            # 累积判断历史
            judge_history = []  # 存储每次判断的结果和理由

            # 获取no_reply开始时的上下文消息（10条），用于后续记录
            context_messages = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=start_time - 600,  # 获取开始前10分钟内的消息
                end_time=start_time,
                limit=10,
                limit_mode="latest",
            )

            # 构建上下文字符串
            context_str = ""
            if context_messages:
                context_str = message_api.build_readable_messages(
                    messages=context_messages, timestamp_mode="normal_no_YMD", truncate=False, show_actions=True
                )
                context_str = f"当时选择no_reply前的聊天上下文：\n{context_str}\n"

            logger.info(f"{self.log_prefix} 选择不回复(第{count}次)，开始智能等待，原因: {reason}")

            while True:
                current_time = time.time()
                elapsed_time = current_time - start_time

                # 检查是否超时
                if elapsed_time >= self._max_timeout:
                    logger.info(f"{self.log_prefix} 达到最大等待时间{self._max_timeout}秒，退出专注模式")
                    # 标记退出专注模式
                    self.action_data["_system_command"] = "stop_focus_chat"
                    exit_reason = f"{global_config.bot.nickname}（你）等待了{self._max_timeout}秒，感觉群里没有新内容，决定退出专注模式，稍作休息"
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=exit_reason,
                        action_done=True,
                    )
                    return True, exit_reason

                # **新增**：检查回复频率，决定是否退出专注模式
                should_exit_focus = await self._check_frequency_and_exit_focus(current_time)
                if should_exit_focus:
                    logger.info(f"{self.log_prefix} 检测到回复频率过高，退出专注模式")
                    # 标记退出专注模式
                    self.action_data["_system_command"] = "stop_focus_chat"
                    exit_reason = f"{global_config.bot.nickname}（你）发现自己回复太频繁了，决定退出专注模式，稍作休息"
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=exit_reason,
                        action_done=True,
                    )
                    return True, exit_reason

                # **新增**：检查过去10分钟是否完全没有发言，如果是则退出专注模式
                should_exit_no_activity = await self._check_no_activity_and_exit_focus(current_time)
                if should_exit_no_activity:
                    logger.info(f"{self.log_prefix} 检测到过去10分钟完全没有发言，退出专注模式")
                    # 标记退出专注模式
                    self.action_data["_system_command"] = "stop_focus_chat"
                    exit_reason = f"{global_config.bot.nickname}（你）发现自己过去10分钟完全没有说话，感觉可能不太活跃，决定退出专注模式"
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=exit_reason,
                        action_done=True,
                    )
                    return True, exit_reason

                # 检查是否有新消息
                new_message_count = message_api.count_new_messages(
                    chat_id=self.chat_id, start_time=start_time, end_time=current_time
                )

                # 如果累计消息数量达到阈值，直接结束等待
                if new_message_count >= self._auto_exit_message_count:
                    logger.info(f"{self.log_prefix} 累计消息数量达到{new_message_count}条，直接结束等待")
                    exit_reason = f"{global_config.bot.nickname}（你）看到了{new_message_count}条新消息，可以考虑一下是否要进行回复"
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=exit_reason,
                        action_done=True,
                    )
                    return True, f"累计消息数量达到{new_message_count}条，直接结束等待 (等待时间: {elapsed_time:.1f}秒)"

                # 判定条件：累计3条消息或等待超过5秒且有新消息
                time_since_last_judge = current_time - last_judge_time
                should_judge = (
                    new_message_count >= 3  # 累计3条消息
                    or (new_message_count > 0 and time_since_last_judge >= 5.0)  # 等待超过5秒且有新消息
                )

                if should_judge and time_since_last_judge >= min_judge_interval:
                    # 判断触发原因
                    trigger_reason = ""
                    if new_message_count >= 3:
                        trigger_reason = f"累计{new_message_count}条消息"
                    elif time_since_last_judge >= 5.0:
                        trigger_reason = f"等待{time_since_last_judge:.1f}秒且有{new_message_count}条新消息"

                    logger.info(f"{self.log_prefix} 触发判定({trigger_reason})，进行智能判断...")

                    # 获取最近的消息内容用于判断
                    recent_messages = message_api.get_messages_by_time_in_chat(
                        chat_id=self.chat_id,
                        start_time=start_time,
                        end_time=current_time,
                    )

                    if recent_messages:
                        # 使用message_api构建可读的消息字符串
                        messages_text = message_api.build_readable_messages(
                            messages=recent_messages, timestamp_mode="normal_no_YMD", truncate=False, show_actions=False
                        )

                        # 获取身份信息
                        bot_name = global_config.bot.nickname
                        bot_nickname = ""
                        if global_config.bot.alias_names:
                            bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
                        bot_core_personality = global_config.personality.personality_core
                        identity_block = f"你的名字是{bot_name}{bot_nickname}，你{bot_core_personality}"

                        # 构建判断历史字符串（最多显示3条）
                        history_block = ""
                        if judge_history:
                            history_block = "之前的判断历史：\n"
                            # 只取最近的3条历史记录
                            recent_history = judge_history[-3:] if len(judge_history) > 3 else judge_history
                            for i, (timestamp, judge_result, reason) in enumerate(recent_history, 1):
                                elapsed_seconds = int(timestamp - start_time)
                                history_block += f"{i}. 等待{elapsed_seconds}秒时判断：{judge_result}，理由：{reason}\n"
                            history_block += "\n"

                        # 检查过去10分钟的发言频率
                        frequency_block = ""
                        should_skip_llm_judge = False  # 是否跳过LLM判断

                        try:
                            # 获取过去10分钟的所有消息
                            past_10min_time = current_time - 600  # 10分钟前
                            all_messages_10min = message_api.get_messages_by_time_in_chat(
                                chat_id=self.chat_id,
                                start_time=past_10min_time,
                                end_time=current_time,
                            )

                            # 手动过滤bot自己的消息
                            bot_message_count = 0
                            if all_messages_10min:
                                user_id = global_config.bot.qq_account

                                for message in all_messages_10min:
                                    # 检查消息发送者是否是bot
                                    sender_id = message.get("user_id", "")

                                    if sender_id == user_id:
                                        bot_message_count += 1

                            talk_frequency_threshold = global_config.chat.talk_frequency * 10

                            if bot_message_count > talk_frequency_threshold:
                                over_count = bot_message_count - talk_frequency_threshold

                                # 根据超过的数量设置不同的提示词和跳过概率
                                skip_probability = 0
                                if over_count <= 3:
                                    frequency_block = "你感觉稍微有些累，回复的有点多了。\n"
                                elif over_count <= 5:
                                    frequency_block = "你今天说话比较多，感觉有点疲惫，想要稍微休息一下。\n"
                                else:
                                    frequency_block = "你发现自己说话太多了，感觉很累，想要安静一会儿，除非有重要的事情否则不想回复。\n"
                                    skip_probability = self._skip_probability

                                # 根据配置和概率决定是否跳过LLM判断
                                if self._skip_judge_when_tired and random.random() < skip_probability:
                                    should_skip_llm_judge = True
                                    logger.info(
                                        f"{self.log_prefix} 发言过多(超过{over_count}条)，随机决定跳过此次LLM判断(概率{skip_probability * 100:.0f}%)"
                                    )

                                logger.info(
                                    f"{self.log_prefix} 过去10分钟发言{bot_message_count}条，超过阈值{talk_frequency_threshold}，添加疲惫提示"
                                )
                            else:
                                # 回复次数少时的正向提示
                                under_count = talk_frequency_threshold - bot_message_count

                                if under_count >= talk_frequency_threshold * 0.8:  # 回复很少（少于20%）
                                    frequency_block = "你感觉精力充沛，状态很好。\n"
                                elif under_count >= talk_frequency_threshold * 0.5:  # 回复较少（少于50%）
                                    frequency_block = "你感觉状态不错。\n"
                                else:  # 刚好达到阈值
                                    frequency_block = ""

                                logger.info(
                                    f"{self.log_prefix} 过去10分钟发言{bot_message_count}条，未超过阈值{talk_frequency_threshold}，添加正向提示"
                                )

                        except Exception as e:
                            logger.warning(f"{self.log_prefix} 检查发言频率时出错: {e}")
                            frequency_block = ""

                        # 如果决定跳过LLM判断，直接更新时间并继续等待
                        if should_skip_llm_judge:
                            last_judge_time = time.time()  # 更新判断时间，避免立即重新判断
                            continue  # 跳过本次LLM判断，继续循环等待

                        # 构建判断上下文
                        judge_prompt = f"""
{identity_block}

你现在正在QQ群参与聊天，以下是聊天内容：
{context_str}
在以上的聊天中，你选择了暂时不回复，现在，你看到了新的聊天消息如下：
{messages_text}

{history_block}
请注意：{frequency_block}
请你判断，是否要结束不回复的状态，重新加入聊天讨论。

判断标准：
1. 如果有人直接@你、提到你的名字或明确向你询问，应该回复
2. 如果话题发生重要变化，需要你参与讨论，应该回复
3. 如果只是普通闲聊、重复内容或与你无关的讨论，不需要回复
4. 如果消息内容过于简单（如单纯的表情、"哈哈"等），不需要回复
5. 参考之前的判断历史，如果情况有明显变化或持续等待时间过长，考虑调整判断

请用JSON格式回复你的判断，严格按照以下格式：
{{
    "should_reply": true/false,
    "reason": "详细说明你的判断理由"
}}
"""

                        try:
                            # 获取可用的模型配置
                            available_models = llm_api.get_available_models()

                            # 使用 utils_small 模型
                            small_model = getattr(available_models, "utils_small", None)

                            print(judge_prompt)

                            if small_model:
                                # 使用小模型进行判断
                                success, response, reasoning, model_name = await llm_api.generate_with_model(
                                    prompt=judge_prompt,
                                    model_config=small_model,
                                    request_type="plugin.no_reply_judge",
                                    temperature=0.7,  # 进一步降低温度，提高JSON输出的一致性和准确性
                                )

                                # 更新上次判断时间
                                last_judge_time = time.time()

                                if success and response:
                                    response = response.strip()
                                    logger.info(f"{self.log_prefix} 模型({model_name})原始JSON响应: {response}")

                                    # 解析LLM的JSON响应，提取判断结果和理由
                                    judge_result, reason = self._parse_llm_judge_response(response)

                                    logger.info(
                                        f"{self.log_prefix} JSON解析结果 - 判断: {judge_result}, 理由: {reason}"
                                    )

                                    # 将判断结果保存到历史中
                                    judge_history.append((current_time, judge_result, reason))

                                    if judge_result == "需要回复":
                                        logger.info(f"{self.log_prefix} 模型判断需要回复，结束等待")

                                        full_prompt = f"{global_config.bot.nickname}（你）的想法是：{reason}"
                                        await self.store_action_info(
                                            action_build_into_prompt=True,
                                            action_prompt_display=full_prompt,
                                            action_done=True,
                                        )
                                        return True, f"检测到需要回复的消息，结束等待 (等待时间: {elapsed_time:.1f}秒)"
                                    else:
                                        logger.info(f"{self.log_prefix} 模型判断不需要回复，理由: {reason}，继续等待")
                                        # 更新开始时间，避免重复判断同样的消息
                                        start_time = current_time
                                else:
                                    logger.warning(f"{self.log_prefix} 模型判断失败，继续等待")
                            else:
                                logger.warning(f"{self.log_prefix} 未找到可用的模型配置，继续等待")
                                last_judge_time = time.time()  # 即使失败也更新时间，避免频繁重试

                        except Exception as e:
                            logger.error(f"{self.log_prefix} 模型判断异常: {e}，继续等待")
                            last_judge_time = time.time()  # 异常时也更新时间，避免频繁重试

                # 每10秒输出一次等待状态
                if elapsed_time < 60:
                    if int(elapsed_time) % 10 == 0 and int(elapsed_time) > 0:
                        logger.info(f"{self.log_prefix} 已等待{elapsed_time:.0f}秒，等待新消息...")
                        await asyncio.sleep(1)
                else:
                    if int(elapsed_time) % 60 == 0 and int(elapsed_time) > 0:
                        logger.info(f"{self.log_prefix} 已等待{elapsed_time / 60:.0f}分钟，等待新消息...")
                        await asyncio.sleep(1)

                # 短暂等待后继续检查
                await asyncio.sleep(check_interval)

        except Exception as e:
            logger.error(f"{self.log_prefix} 不回复动作执行失败: {e}")
            # 即使执行失败也要记录
            exit_reason = f"执行异常: {str(e)}"
            full_prompt = f"{context_str}{exit_reason}，你思考是否要进行回复"
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=full_prompt,
                action_done=True,
            )
            return False, f"不回复动作执行失败: {e}"

    async def _check_frequency_and_exit_focus(self, current_time: float) -> bool:
        """检查回复频率，决定是否退出专注模式

        Args:
            current_time: 当前时间戳

        Returns:
            bool: 是否应该退出专注模式
        """
        try:
            # 只在auto模式下进行频率检查
            if global_config.chat.chat_mode != "auto":
                return False

            # 获取检查窗口内的所有消息
            window_start_time = current_time - self._frequency_check_window
            all_messages = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=window_start_time,
                end_time=current_time,
            )

            if not all_messages:
                return False

            # 统计bot自己的回复数量
            bot_message_count = 0
            user_id = global_config.bot.qq_account

            for message in all_messages:
                sender_id = message.get("user_id", "")
                if sender_id == user_id:
                    bot_message_count += 1

            # 计算当前回复频率（每分钟回复数）
            window_minutes = self._frequency_check_window / 60
            current_frequency = bot_message_count / window_minutes

            # 计算阈值频率：使用 exit_focus_threshold * 1.5
            threshold_multiplier = global_config.chat.exit_focus_threshold * 1.5
            threshold_frequency = global_config.chat.talk_frequency * threshold_multiplier

            # 判断是否超过阈值
            if current_frequency > threshold_frequency:
                logger.info(
                    f"{self.log_prefix} 回复频率检查：当前频率 {current_frequency:.2f}/分钟，超过阈值 {threshold_frequency:.2f}/分钟 (exit_threshold={global_config.chat.exit_focus_threshold} * 1.5)，准备退出专注模式"
                )
                return True
            else:
                logger.debug(
                    f"{self.log_prefix} 回复频率检查：当前频率 {current_frequency:.2f}/分钟，未超过阈值 {threshold_frequency:.2f}/分钟 (exit_threshold={global_config.chat.exit_focus_threshold} * 1.5)"
                )
                return False

        except Exception as e:
            logger.error(f"{self.log_prefix} 检查回复频率时出错: {e}")
            return False

    async def _check_no_activity_and_exit_focus(self, current_time: float) -> bool:
        """检查过去10分钟是否完全没有发言，决定是否退出专注模式

        Args:
            current_time: 当前时间戳

        Returns:
            bool: 是否应该退出专注模式
        """
        try:
            # 只在auto模式下进行检查
            if global_config.chat.chat_mode != "auto":
                return False

            # 获取过去10分钟的所有消息
            past_10min_time = current_time - 600  # 10分钟前
            all_messages = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=past_10min_time,
                end_time=current_time,
            )

            if not all_messages:
                # 如果完全没有消息，也不需要退出专注模式
                return False

            # 统计bot自己的回复数量
            bot_message_count = 0
            user_id = global_config.bot.qq_account

            for message in all_messages:
                sender_id = message.get("user_id", "")
                if sender_id == user_id:
                    bot_message_count += 1

            # 如果过去10分钟bot一条消息也没有发送，退出专注模式
            if bot_message_count == 0:
                logger.info(f"{self.log_prefix} 过去10分钟bot完全没有发言，准备退出专注模式")
                return True
            else:
                logger.debug(f"{self.log_prefix} 过去10分钟bot发言{bot_message_count}条，继续保持专注模式")
                return False

        except Exception as e:
            logger.error(f"{self.log_prefix} 检查无活动状态时出错: {e}")
            return False

    def _parse_llm_judge_response(self, response: str) -> tuple[str, str]:
        """解析LLM判断响应，使用JSON格式提取判断结果和理由

        Args:
            response: LLM的原始JSON响应

        Returns:
            tuple: (判断结果, 理由)
        """
        try:
            # 使用repair_json修复可能有问题的JSON格式
            fixed_json_string = repair_json(response)
            logger.debug(f"{self.log_prefix} repair_json修复后的响应: {fixed_json_string}")

            # 如果repair_json返回的是字符串，需要解析为Python对象
            if isinstance(fixed_json_string, str):
                result_json = json.loads(fixed_json_string)
            else:
                # 如果repair_json直接返回了字典对象，直接使用
                result_json = fixed_json_string

            # 从JSON中提取判断结果和理由
            should_reply = result_json.get("should_reply", False)
            reason = result_json.get("reason", "无法获取判断理由")

            # 转换布尔值为中文字符串
            judge_result = "需要回复" if should_reply else "不需要回复"

            logger.debug(f"{self.log_prefix} JSON解析成功 - 判断: {judge_result}, 理由: {reason}")
            return judge_result, reason

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"{self.log_prefix} JSON解析失败，尝试文本解析: {e}")

            # 如果JSON解析失败，回退到简单的关键词匹配
            try:
                response_lower = response.lower()

                if "true" in response_lower or "需要回复" in response:
                    judge_result = "需要回复"
                    reason = "从响应文本中检测到需要回复的指示"
                elif "false" in response_lower or "不需要回复" in response:
                    judge_result = "不需要回复"
                    reason = "从响应文本中检测到不需要回复的指示"
                else:
                    judge_result = "不需要回复"  # 默认值
                    reason = f"无法解析响应格式，使用默认判断。原始响应: {response[:100]}..."

                logger.debug(f"{self.log_prefix} 文本解析结果 - 判断: {judge_result}, 理由: {reason}")
                return judge_result, reason

            except Exception as fallback_e:
                logger.error(f"{self.log_prefix} 文本解析也失败: {fallback_e}")
                return "不需要回复", f"解析异常: {str(e)}, 回退解析也失败: {str(fallback_e)}"

        except Exception as e:
            logger.error(f"{self.log_prefix} 解析LLM响应时出错: {e}")
            return "不需要回复", f"解析异常: {str(e)}"

    @classmethod
    def reset_consecutive_count(cls):
        """重置连续计数器"""
        cls._consecutive_count = 0
        logger.debug("NoReplyAction连续计数器已重置")
