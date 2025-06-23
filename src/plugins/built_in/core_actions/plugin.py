"""
核心动作插件

将系统核心动作（reply、no_reply、emoji）转换为新插件系统格式
这是系统的内置插件，提供基础的聊天交互功能
"""

import random
import time
import json
from typing import List, Tuple, Type

# 导入新插件系统
from src.plugin_system import BasePlugin, register_plugin, BaseAction, ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import emoji_api, generator_api, message_api, llm_api
from src.config.config import global_config
from datetime import datetime
from json_repair import repair_json

logger = get_logger("core_actions")

# 常量定义
WAITING_TIME_THRESHOLD = 1200  # 等待新消息时间阈值，单位秒


class ReplyAction(BaseAction):
    """回复动作 - 参与聊天回复"""

    # 激活设置
    focus_activation_type = ActionActivationType.ALWAYS
    normal_activation_type = ActionActivationType.NEVER
    mode_enable = ChatMode.FOCUS
    parallel_action = False

    # 动作基本信息
    action_name = "reply"
    action_description = "参与聊天回复，发送文本进行表达"

    # 动作参数定义
    action_parameters = {
        "reply_to": "你要回复的对方的发言内容，格式：（用户名:发言内容），可以为none",
        "reason": "回复的原因",
    }

    # 动作使用场景
    action_require = ["你想要闲聊或者随便附和", "有人提到你", "如果你刚刚进行了回复，不要对同一个话题重复回应"]

    # 关联类型
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行回复动作"""
        logger.info(f"{self.log_prefix} 决定回复: {self.reasoning}")

        start_time = self.action_data.get("loop_start_time", time.time())

        try:
            success, reply_set = await generator_api.generate_reply(
                action_data=self.action_data,
                chat_id=self.chat_id,
            )

            # 检查从start_time以来的新消息数量
            # 获取动作触发时间或使用默认值
            current_time = time.time()
            new_message_count = message_api.count_new_messages(
                chat_id=self.chat_id, start_time=start_time, end_time=current_time
            )

            # 根据新消息数量决定是否使用reply_to
            need_reply = new_message_count >= random.randint(2, 5)
            logger.info(
                f"{self.log_prefix} 从{start_time}到{current_time}共有{new_message_count}条新消息，{'使用' if need_reply else '不使用'}reply_to"
            )

            # 构建回复文本
            reply_text = ""
            first_replyed = False
            for reply_seg in reply_set:
                data = reply_seg[1]
                if not first_replyed:
                    if need_reply:
                        await self.send_text(content=data, reply_to=self.action_data.get("reply_to", ""), typing=False)
                        first_replyed = True
                    else:
                        await self.send_text(content=data, typing=False)
                        first_replyed = True
                else:
                    await self.send_text(content=data, typing=True)
                reply_text += data

            # 存储动作记录
            await self.store_action_info(
                action_build_into_prompt=False,
                action_prompt_display=reply_text,
                action_done=True,
            )

            # 重置NoReplyAction的连续计数器
            NoReplyAction.reset_consecutive_count()

            return success, reply_text

        except Exception as e:
            logger.error(f"{self.log_prefix} 回复动作执行失败: {e}")
            return False, f"回复失败: {str(e)}"


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
    _max_timeout = 1200  # 1200秒

    # 跳过LLM判断的配置
    _skip_judge_when_tired = True
    _skip_probability_light = 0.2  # 轻度疲惫跳过概率
    _skip_probability_medium = 0.4  # 中度疲惫跳过概率
    _skip_probability_heavy = 0.6  # 重度疲惫跳过概率

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
                    logger.info(f"{self.log_prefix} 达到最大等待时间{self._max_timeout}秒，结束等待")
                    exit_reason = (
                        f"{global_config.bot.nickname}（你）等待了{self._max_timeout}秒，可以考虑一下是否要进行回复"
                    )
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

                        # 参考simple_planner构建更完整的判断信息
                        # 获取时间信息
                        time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

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
                                if over_count <= 3:
                                    frequency_block = "你感觉稍微有些累，回复的有点多了。\n"
                                elif over_count <= 5:
                                    frequency_block = "你今天说话比较多，感觉有点疲惫，想要稍微休息一下。\n"
                                else:
                                    frequency_block = "你发现自己说话太多了，感觉很累，想要安静一会儿，除非有重要的事情否则不想回复。\n"
                                    skip_probability = self._skip_probability_heavy

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
                            start_time = current_time  # 更新开始时间，避免重复计算同样的消息
                            continue  # 跳过本次LLM判断，继续循环等待

                        # 构建判断上下文
                        judge_prompt = f"""
{time_block}
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
                if int(elapsed_time) % 10 == 0 and int(elapsed_time) > 0:
                    logger.info(f"{self.log_prefix} 已等待{elapsed_time:.0f}秒，等待新消息...")
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


class EmojiAction(BaseAction):
    """表情动作 - 发送表情包"""

    # 激活设置
    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.RANDOM
    mode_enable = ChatMode.ALL
    parallel_action = True
    random_activation_probability = 0.1  # 默认值，可通过配置覆盖

    # 动作基本信息
    action_name = "emoji"
    action_description = "发送表情包辅助表达情绪"

    # LLM判断提示词
    llm_judge_prompt = """
    判定是否需要使用表情动作的条件：
    1. 用户明确要求使用表情包
    2. 这是一个适合表达强烈情绪的场合
    3. 不要发送太多表情包，如果你已经发送过多个表情包则回答"否"
    
    请回答"是"或"否"。
    """

    # 动作参数定义
    action_parameters = {"description": "文字描述你想要发送的表情包内容"}

    # 动作使用场景
    action_require = ["表达情绪时可以选择使用", "重点：不要连续发，如果你已经发过[表情包]，就不要选择此动作"]

    # 关联类型
    associated_types = ["emoji"]

    async def execute(self) -> Tuple[bool, str]:
        """执行表情动作"""
        logger.info(f"{self.log_prefix} 决定发送表情")

        try:
            # 1. 根据描述选择表情包
            description = self.action_data.get("description", "")
            emoji_result = await emoji_api.get_by_description(description)

            if not emoji_result:
                logger.warning(f"{self.log_prefix} 未找到匹配描述 '{description}' 的表情包")
                return False, f"未找到匹配 '{description}' 的表情包"

            emoji_base64, emoji_description, matched_emotion = emoji_result
            logger.info(f"{self.log_prefix} 找到表情包: {emoji_description}, 匹配情感: {matched_emotion}")

            # 使用BaseAction的便捷方法发送表情包
            success = await self.send_emoji(emoji_base64)

            if not success:
                logger.error(f"{self.log_prefix} 表情包发送失败")
                return False, "表情包发送失败"

            # 重置NoReplyAction的连续计数器
            NoReplyAction.reset_consecutive_count()

            return True, f"发送表情包: {emoji_description}"

        except Exception as e:
            logger.error(f"{self.log_prefix} 表情动作执行失败: {e}")
            return False, f"表情发送失败: {str(e)}"


class ExitFocusChatAction(BaseAction):
    """退出专注聊天动作 - 从专注模式切换到普通模式"""

    # 激活设置
    focus_activation_type = ActionActivationType.NEVER
    normal_activation_type = ActionActivationType.NEVER
    mode_enable = ChatMode.FOCUS
    parallel_action = False

    # 动作基本信息
    action_name = "exit_focus_chat"
    action_description = "退出专注聊天，从专注模式切换到普通模式"

    # LLM判断提示词
    llm_judge_prompt = """
    判定是否需要退出专注聊天的条件：
    1. 很长时间没有回复，应该退出专注聊天
    2. 当前内容不需要持续专注关注
    3. 聊天内容已经完成，话题结束
    
    请回答"是"或"否"。
    """

    # 动作参数定义
    action_parameters = {}

    # 动作使用场景
    action_require = [
        "很长时间没有回复，你决定退出专注聊天",
        "当前内容不需要持续专注关注，你决定退出专注聊天",
        "聊天内容已经完成，你决定退出专注聊天",
    ]

    # 关联类型
    associated_types = []

    async def execute(self) -> Tuple[bool, str]:
        """执行退出专注聊天动作"""
        logger.info(f"{self.log_prefix} 决定退出专注聊天: {self.reasoning}")

        try:
            # 标记状态切换请求
            self._mark_state_change()

            # 重置NoReplyAction的连续计数器
            NoReplyAction.reset_consecutive_count()

            status_message = "决定退出专注聊天模式"
            return True, status_message

        except Exception as e:
            logger.error(f"{self.log_prefix} 退出专注聊天动作执行失败: {e}")
            return False, f"退出专注聊天失败: {str(e)}"

    def _mark_state_change(self):
        """标记状态切换请求"""
        # 通过action_data传递状态切换命令
        self.action_data["_system_command"] = "stop_focus_chat"
        logger.info(f"{self.log_prefix} 已标记状态切换命令: stop_focus_chat")


@register_plugin
class CoreActionsPlugin(BasePlugin):
    """核心动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name = "core_actions"  # 内部标识符
    enable_plugin = True
    config_file_name = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "components": "核心组件启用配置",
        "no_reply": "不回复动作配置（智能等待机制）",
        "emoji": "表情动作配置",
    }

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="0.0.9", description="配置文件版本"),
        },
        "components": {
            "enable_reply": ConfigField(type=bool, default=True, description="是否启用'回复'动作"),
            "enable_no_reply": ConfigField(type=bool, default=True, description="是否启用'不回复'动作"),
            "enable_emoji": ConfigField(type=bool, default=True, description="是否启用'表情'动作"),
            "enable_change_to_focus": ConfigField(type=bool, default=True, description="是否启用'切换到专注模式'动作"),
            "enable_exit_focus": ConfigField(type=bool, default=True, description="是否启用'退出专注模式'动作"),
        },
        "no_reply": {
            "max_timeout": ConfigField(type=int, default=1200, description="最大等待超时时间（秒）"),
            "min_judge_interval": ConfigField(
                type=float, default=1.0, description="LLM判断的最小间隔时间（秒），防止过于频繁"
            ),
            "auto_exit_message_count": ConfigField(
                type=int, default=20, description="累计消息数量达到此阈值时自动结束等待"
            ),
            "random_probability": ConfigField(
                type=float, default=0.8, description="Focus模式下，随机选择不回复的概率（0.0到1.0）", example=0.8
            ),
            "skip_judge_when_tired": ConfigField(
                type=bool, default=True, description="当发言过多时是否启用跳过LLM判断机制"
            ),
            "skip_probability_light": ConfigField(
                type=float, default=0.3, description="轻度疲惫时跳过LLM判断的概率", example=0.2
            ),
            "skip_probability_medium": ConfigField(
                type=float, default=0.5, description="中度疲惫时跳过LLM判断的概率", example=0.4
            ),
            "skip_probability_heavy": ConfigField(
                type=float, default=0.7, description="重度疲惫时跳过LLM判断的概率", example=0.6
            ),
        },
        "emoji": {
            "random_probability": ConfigField(
                type=float, default=0.1, description="Normal模式下，随机发送表情的概率（0.0到1.0）", example=0.15
            )
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 从配置动态设置Action/Command ---
        emoji_chance = self.get_config("emoji.random_probability", 0.1)
        EmojiAction.random_activation_probability = emoji_chance

        no_reply_probability = self.get_config("no_reply.random_probability", 0.8)
        NoReplyAction.random_activation_probability = no_reply_probability

        min_judge_interval = self.get_config("no_reply.min_judge_interval", 1.0)
        NoReplyAction._min_judge_interval = min_judge_interval

        auto_exit_message_count = self.get_config("no_reply.auto_exit_message_count", 20)
        NoReplyAction._auto_exit_message_count = auto_exit_message_count

        max_timeout = self.get_config("no_reply.max_timeout", 1200)
        NoReplyAction._max_timeout = max_timeout

        skip_judge_when_tired = self.get_config("no_reply.skip_judge_when_tired", True)
        NoReplyAction._skip_judge_when_tired = skip_judge_when_tired

        skip_probability_light = self.get_config("no_reply.skip_probability_light", 0.2)
        NoReplyAction._skip_probability_light = skip_probability_light

        skip_probability_medium = self.get_config("no_reply.skip_probability_medium", 0.4)
        NoReplyAction._skip_probability_medium = skip_probability_medium

        skip_probability_heavy = self.get_config("no_reply.skip_probability_heavy", 0.6)
        NoReplyAction._skip_probability_heavy = skip_probability_heavy

        # --- 根据配置注册组件 ---
        components = []
        if self.get_config("components.enable_reply", True):
            components.append((ReplyAction.get_action_info(), ReplyAction))
        if self.get_config("components.enable_no_reply", True):
            components.append((NoReplyAction.get_action_info(), NoReplyAction))
        if self.get_config("components.enable_emoji", True):
            components.append((EmojiAction.get_action_info(), EmojiAction))
        if self.get_config("components.enable_exit_focus", True):
            components.append((ExitFocusChatAction.get_action_info(), ExitFocusChatAction))

        # components.append((DeepReplyAction.get_action_info(), DeepReplyAction))

        return components


# class DeepReplyAction(BaseAction):
#     """回复动作 - 参与聊天回复"""

#     # 激活设置
#     focus_activation_type = ActionActivationType.ALWAYS
#     normal_activation_type = ActionActivationType.NEVER
#     mode_enable = ChatMode.FOCUS
#     parallel_action = False

#     # 动作基本信息
#     action_name = "deep_reply"
#     action_description = "参与聊天回复，关注某个话题，对聊天内容进行深度思考，给出回复"

#     # 动作参数定义
#     action_parameters = {
#         "topic": "想要思考的话题"
#     }

#     # 动作使用场景
#     action_require = ["有些问题需要深度思考", "某个问题可能涉及多个方面", "某个问题涉及专业领域或者需要专业知识","这个问题讨论的很激烈，需要深度思考"]

#     # 关联类型
#     associated_types = ["text"]

#     async def execute(self) -> Tuple[bool, str]:
#         """执行回复动作"""
#         logger.info(f"{self.log_prefix} 决定深度思考")

#         try:
#             # 获取聊天观察
#             chatting_observation = self._get_chatting_observation()
#             if not chatting_observation:
#                 return False, "未找到聊天观察"

#             talking_message_str = chatting_observation.talking_message_str

#             # 处理回复目标
#             chat_stream = self.api.get_service("chat_stream")
#             anchor_message = await create_empty_anchor_message(chat_stream.platform, chat_stream.group_info, chat_stream)


#             llm_model = self.api.get_available_models().replyer_1

#             prompt = f"""
#             {talking_message_str}

#             在上面的聊天中，你对{self.action_data.get("topic", "")}感兴趣，形成深刻观点，请你思考,总结成一份学术论文,APA标准格式
#             """

#             success, response, reasoning, model_name = await self.api.generate_with_model(prompt, llm_model)

#             print(prompt)
#             print(f"DeepReplyAction: {response}")

#             # prompt = f"""
#             # {talking_message_str}

#             # 在上面的聊天中，你对{self.action_data.get("topic", "")}感兴趣，请你思考
#             # """

#             extra_info_block = self.action_data.get("extra_info_block", "")
#             extra_info_block += response
#             # extra_info_block += f"\n--------------------------------\n注意，这是最重要的内容！！！！！你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以下方聊天记录的回复要求不再适用，请你自由的表达，不论字数长短限制\n\n--------------------------------\n注意，你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以下方聊天记录的回复要求不再适用，请你自由的表达，不论字数长短限制\n"
#             # extra_info_block += f"\n--------------------------------\n注意，优先关注这句！！！！你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以下方聊天记录的回复要求不再适用，请你自由的表达，不论字数长短限制\n\n--------------------------------\n注意，你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以其他的回复要求不再适用，请你自由的表达，不论字数长短限制\n"
#             self.action_data["extra_info_block"] = extra_info_block


#             # 获取回复器服务
#             # replyer = self.api.get_service("replyer")
#             # if not replyer:
#                 # logger.error(f"{self.log_prefix} 未找到回复器服务")
#                 # return False, "回复器服务不可用"

#             # await self.send_message_by_expressor(extra_info_block)
#             await self.send_text(extra_info_block)
#             # 执行回复
#             # success, reply_set = await replyer.deal_reply(
#             #     cycle_timers=self.cycle_timers,
#             #     action_data=self.action_data,
#             #     anchor_message=anchor_message,
#             #     reasoning=self.reasoning,
#             #     thinking_id=self.thinking_id,
#             # )

#             # 构建回复文本
#             reply_text = "self._build_reply_text(reply_set)"

#             # 存储动作记录
#             await self.api.store_action_info(
#                 action_build_into_prompt=False,
#                 action_prompt_display=reply_text,
#                 action_done=True,
#                 thinking_id=self.thinking_id,
#                 action_data=self.action_data,
#             )

#             # 重置NoReplyAction的连续计数器
#             NoReplyAction.reset_consecutive_count()

#             return success, reply_text

#         except Exception as e:
#             logger.error(f"{self.log_prefix} 回复动作执行失败: {e}")
#             return False, f"回复失败: {str(e)}"

#     def _get_chatting_observation(self) -> Optional[ChattingObservation]:
#         """获取聊天观察对象"""
#         observations = self.api.get_service("observations") or []
#         for obs in observations:
#             if isinstance(obs, ChattingObservation):
#                 return obs
#         return None


#     def _build_reply_text(self, reply_set) -> str:
#         """构建回复文本"""
#         reply_text = ""
#         if reply_set:
#             for reply in reply_set:
#                 data = reply[1]
#                 reply_text += data
#         return reply_text
