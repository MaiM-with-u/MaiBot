import time
import traceback
from typing import Tuple, Optional, Dict, Any, List
from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from .pfc_utils import get_items_from_json, build_chat_history_text
from .chat_observer import ChatObserver
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo

logger = get_logger("pfc_action_planner")


# --- 定义 Prompt 模板  ---

# Prompt(1): 首次回复或非连续回复时的决策 Prompt
PROMPT_INITIAL_REPLY = """
当前时间：{current_time_str}
现在[{persona_text}]正在与[{sender_name}]在qq上私聊
他们的关系是：{relationship_text}
[{persona_text}]现在的心情是：{current_emotion_text}
你现在需要操控[{persona_text}]，判断当前氛围和双方的意图，并根据以下【所有信息】灵活，合理的决策{persona_text}的下一步行动，需要符合正常人的社交流程，可以回复，可以倾听，甚至可以屏蔽对方：

【当前对话目标】
{goals_str}
【最近行动历史概要】
{action_history_summary}
【上一次行动的详细情况和结果】
{last_action_context}
【时间和超时提示】
{time_since_last_bot_message_info}{timeout_context}
【最近的对话记录】(包括你已成功发送的消息 和 新收到的消息)
{chat_history_text}


------
可选行动类型以及解释：
listening: 倾听对方发言，当你认为对方话才说到一半，发言明显未结束时选择
direct_reply: 直接回复对方
send_memes: 发送一个符合当前聊天氛围或{persona_text}心情的表情包，当你觉得用表情包回应更合适，或者想要活跃气氛时选择。
rethink_goal: 思考一个对话目标，当你觉得目前对话需要目标，或当前目标不再适用，或话题卡住时选择。注意私聊的环境是灵活的，有可能需要经常选择
end_conversation: 结束对话，对方长时间没回复，繁忙，或者当你觉得对话告一段落时可以选择
block_and_ignore: 更加极端的结束对话方式，直接结束对话并在一段时间内无视对方所有发言（屏蔽），当你觉得对话让[{persona_text}]感到十分不适，或[{persona_text}]遭到各类骚扰时选择

请以JSON格式输出你的决策：
{{
    "action": "选择的行动类型 (必须是上面列表中的一个)",
    "reason": "选择该行动的原因 ",
    "emoji_query": "string" // 可选。如果行动是 'send_memes'，必须提供表情主题(填写表情包的适用场合或情感描述)；如果行动是 'direct_reply' 且你想附带表情，也在此提供表情主题，否则留空字符串 ""
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。"""

# Prompt(2): 上一次成功回复后，决定继续发言时的决策 Prompt
PROMPT_FOLLOW_UP = """
当前时间：{current_time_str}
现在[{persona_text}]正在与[{sender_name}]在qq上私聊，**并且刚刚[{persona_text}]已经回复了对方**
他们的关系是：{relationship_text}
{persona_text}现在的心情是：{current_emotion_text}
你现在需要操控[{persona_text}]，判断当前氛围和双方的意图，并根据以下【所有信息】灵活，合理的决策[{persona_text}]的下一步行动，需要符合正常人的社交流程，可以发送新消息，可以等待，可以倾听，可以结束对话，甚至可以屏蔽对方：

【当前对话目标】
{goals_str}
【最近行动历史概要】
{action_history_summary}
【上一次行动的详细情况和结果】
{last_action_context}
【时间和超时提示】
{time_since_last_bot_message_info}{timeout_context}
【最近的对话记录】(包括你已成功发送的消息 和 新收到的消息)
{chat_history_text}

------
可选行动类型以及解释：
wait: 暂时不说话，留给对方交互空间，等待对方回复。
listening: 倾听对方发言（虽然你刚发过言，但如果对方立刻回复且明显话没说完，可以选择这个）
send_new_message: 发送一条新消息，当你觉得[{persona_text}]还有话要说，或现在适合/需要发送消息时可以选择
send_memes: 发送一个符合当前聊天氛围或{persona_text}心情的表情包，当你觉得用表情包回应更合适，或者想要活跃气氛时选择。
rethink_goal: 思考一个对话目标，当你觉得目前对话需要目标，或当前目标不再适用，或话题卡住时选择。注意私聊的环境是灵活的，有可能需要经常选择
end_conversation: 安全和平的结束对话，对方长时间没回复、繁忙、或你觉得对话告一段落时可以选择
block_and_ignore: 更加极端的结束对话方式，直接结束对话并在一段时间内无视对方所有发言（屏蔽），当你觉得对话让[{persona_text}]感到十分不适，或[{persona_text}]遭到各类骚扰时选择

请以JSON格式输出你的决策：
{{
    "action": "选择的行动类型 (必须是上面列表中的一个)",
    "reason": "选择该行动的原因",
    "emoji_query": "string" // 可选。如果行动是 'send_memes'，必须提供表情主题(填写表情包的适用场合或情感描述)；如果行动是 'send_new_message' 且你想附带表情，也在此提供表情主题，否则留空字符串 ""
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。"""

# 新增：Prompt(3): 决定是否在结束对话前发送告别语
PROMPT_END_DECISION = """
当前时间：{current_time_str}
现在{persona_text}与{sender_name}刚刚结束了一场qq私聊
他们的关系是：{relationship_text}
你现在需要操控{persona_text}，根据以下【所有信息】灵活，合理的决策{persona_text}的下一步行动，需要符合正常人的社交流程：


【他们之前的聊天记录】
{chat_history_text}

你觉得他们的对话已经完整结束了吗？有时候，在对话自然结束后再说点什么可能会有点奇怪，但有时也可能需要一条简短的消息来圆满结束。
如果觉得确实有必要再发一条简短、自然的告别消息（比如 "好，下次再聊~" 或 "嗯，先这样吧"），就输出 "yes"。
如果觉得当前状态下直接结束对话更好，没有必要再发消息，就输出 "no"。

请以 JSON 格式输出你的选择：
{{
    "say_bye": "yes/no",
    "reason": "选择 yes 或 no 的原因和 (简要说明)"
}}

注意：请严格按照 JSON 格式输出，不要包含任何其他内容。"""

# Prompt(4): 当 reply_generator 决定不发送消息后的反思决策 Prompt
PROMPT_REFLECT_AND_ACT = """
当前时间：{current_time_str}
现在{persona_text}正在与{sender_name}在qq上私聊，刚刚{persona_text}打算发一条新消息，想了想还是不发了
他们的关系是：{relationship_text}
{persona_text}现在的心情是是：{current_emotion_text}
你现在需要操控{persona_text}，根据以下【所有信息】灵活，合理的决策{persona_text}的下一步行动，需要符合正常人的社交流程，可以等待，可以倾听，可以结束对话，甚至可以屏蔽对方：

【当前对话目标】
{goals_str}
【最近行动历史概要】
{action_history_summary}
【上一次行动的详细情况和结果】
{last_action_context}
【时间和超时提示】
{time_since_last_bot_message_info}{timeout_context}
【最近的对话记录】(包括你已成功发送的消息 和 新收到的消息)
{chat_history_text}


------
可选行动类型以及解释：
wait: 等待，暂时不说话。
listening: 倾听对方发言（虽然你刚发过言，但如果对方立刻回复且明显话没说完，可以选择这个）
rethink_goal: 思考一个对话目标，当你觉得目前对话需要目标，或当前目标不再适用，或话题卡住时选择。注意私聊的环境是灵活的，有可能需要经常选择
end_conversation: 安全和平的结束对话，对方长时间没回复、繁忙、已经不再回复你消息、明显暗示或表达想结束聊天时，可以果断选择
block_and_ignore: 更加极端的结束对话方式，直接结束对话并在一段时间内无视对方所有发言（屏蔽），当对话让你感到十分不适，或你遭到各类骚扰时选择

请以JSON格式输出你的决策：
{{
    "action": "选择的行动类型 (必须是上面列表中的一个)",
    "reason": "选择该行动的原因"
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。"""


class ActionPlanner:
    """行动规划器"""

    def __init__(self, stream_id: str, private_name: str):
        """初始化行动规划器"""
        self.stream_id = stream_id
        self.private_name = private_name
        # 初始化 LLM 请求对象
        try:
            llm_config = global_config.llm_PFC_action_planner
            if not isinstance(llm_config, dict):
                raise TypeError(f"LLM config 'llm_PFC_action_planner' is not a dictionary: {llm_config}")

            self.llm = LLMRequest(
                model=llm_config,
                temperature=llm_config.get("temp", 0.7),
                max_tokens=1500,
                request_type="action_planning",
            )
        except TypeError as e:
            logger.error(f"[私聊][{self.private_name}] 初始化 LLMRequest 时配置错误: {e}")
            raise
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 初始化 LLMRequest 时发生未知错误: {e}")
            raise

        # 获取个性化信息和机器人名称
        # self.personality_info = Individuality.get_instance().get_prompt(x_person=2, level=3)
        self.name = global_config.BOT_NICKNAME
        # 获取 ChatObserver 实例 (单例模式)
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)

    async def plan(
        self,
        observation_info: ObservationInfo,
        conversation_info: ConversationInfo,
        last_successful_reply_action: Optional[str],
        use_reflect_prompt: bool = False,  # 新增参数，用于指示是否使用PROMPT_REFLECT_AND_ACT
    ) -> Tuple[str, str]:
        """
        规划下一步行动。

        Args:
            observation_info: 观察信息，包含聊天记录、未读消息等。
            conversation_info: 对话信息，包含目标、历史动作等。
            last_successful_reply_action: 上一次成功的回复动作类型 ('direct_reply' 或 'send_new_message' 或 None)。

        Returns:
            Tuple[str, str]: (规划的行动类型, 行动原因)。
        """
        logger.info(f"[私聊][{self.private_name}] 开始规划行动...")
        plan_start_time = time.time()

        # --- 1. 准备 Prompt 输入信息 ---
        try:
            time_since_last_bot_message_info = self._get_bot_last_speak_time_info(observation_info)
            timeout_context = self._get_timeout_context(conversation_info)
            goals_str = self._build_goals_string(conversation_info)
            chat_history_text = await build_chat_history_text(observation_info, self.private_name)
            # 获取 sender_name, relationship_text, current_emotion_text
            sender_name_str = self.private_name
            if not sender_name_str:
                sender_name_str = "对方"  # 再次确保有默认值

            relationship_text_str = getattr(conversation_info, "relationship_text", "你们还不熟悉。")
            current_emotion_text_str = getattr(conversation_info, "current_emotion_text", "心情平静。")

            persona_text = f"{self.name}"
            action_history_summary, last_action_context = self._build_action_history_context(conversation_info)
            # retrieved_memory_str, retrieved_knowledge_str = await retrieve_contextual_info(
            #     chat_history_text, self.private_name
            # )
            # logger.info(
            #     f"[私聊][{self.private_name}] (ActionPlanner) 检索完成。记忆: {'有' if '回忆起' in retrieved_memory_str else '无'} / 知识: {'有' if retrieved_knowledge_str and '无相关知识' not in retrieved_knowledge_str and '出错' not in retrieved_knowledge_str else '无'}"
            # )
        except Exception as prep_err:
            logger.error(f"[私聊][{self.private_name}] 准备 Prompt 输入时出错: {prep_err}")
            logger.error(traceback.format_exc())
            return "wait", f"准备行动规划输入时出错: {prep_err}"

        # --- 2. 选择并格式化 Prompt ---
        try:
            if use_reflect_prompt:  # 新增的判断
                prompt_template = PROMPT_REFLECT_AND_ACT
                log_msg = "使用 PROMPT_REFLECT_AND_ACT (反思决策)"

            elif last_successful_reply_action in ["direct_reply", "send_new_message", "send_memes"]:
                prompt_template = PROMPT_FOLLOW_UP
                log_msg = "使用 PROMPT_FOLLOW_UP (追问决策)"

            else:
                prompt_template = PROMPT_INITIAL_REPLY
                log_msg = "使用 PROMPT_INITIAL_REPLY (首次/非连续回复决策)"
                # spam_warning_message = ""  # 初始回复时通常不需要刷屏警告

            logger.debug(f"[私聊][{self.private_name}] {log_msg}")

            current_time_value = "获取时间失败"
            if observation_info and hasattr(observation_info, "current_time_str") and observation_info.current_time_str:
                current_time_value = observation_info.current_time_str

            # if spam_warning_message:
            # spam_warning_message = f"\n{spam_warning_message}\n"

            prompt = prompt_template.format(
                persona_text=persona_text,
                goals_str=goals_str if goals_str.strip() else "- 目前没有明确对话目标，请考虑设定一个。",
                action_history_summary=action_history_summary,
                last_action_context=last_action_context,
                time_since_last_bot_message_info=time_since_last_bot_message_info,
                timeout_context=timeout_context,
                chat_history_text=chat_history_text if chat_history_text.strip() else "还没有聊天记录。",
                # retrieved_memory_str=retrieved_memory_str if retrieved_memory_str else "无相关记忆。",
                # retrieved_knowledge_str=retrieved_knowledge_str if retrieved_knowledge_str else "无相关知识。",
                current_time_str=current_time_value,
                # spam_warning_info=spam_warning_message,
                sender_name=sender_name_str,
                relationship_text=relationship_text_str,
                current_emotion_text=current_emotion_text_str,
            )
            logger.debug(f"[私聊][{self.private_name}] 发送到LLM的最终提示词:\n------\n{prompt}\n------")
        except KeyError as fmt_key_err:
            logger.error(f"[私聊][{self.private_name}] 格式化 Prompt 时缺少键: {fmt_key_err}")
            return "wait", f"格式化 Prompt 时出错 (缺少键: {fmt_key_err})"
        except Exception as fmt_err:
            logger.error(f"[私聊][{self.private_name}] 格式化 Prompt 时发生未知错误: {fmt_err}")
            return "wait", f"格式化 Prompt 时出错: {fmt_err}"

        # --- 3. 调用 LLM 进行初步规划 ---
        try:
            llm_start_time = time.time()
            content, _ = await self.llm.generate_response_async(prompt)
            llm_duration = time.time() - llm_start_time
            logger.debug(f"[私聊][{self.private_name}] LLM (行动规划) 耗时: {llm_duration:.3f} 秒, 原始返回: {content}")

            success, initial_result = get_items_from_json(
                content,
                self.private_name,
                "action",
                "reason",
                "emoji_query",
                default_values={"action": "wait", "reason": "LLM返回格式错误或未提供原因，默认等待", "emoji_query": ""},
                allow_empty_string_fields=["emoji_query"],
            )

            initial_action = initial_result.get("action", "wait")
            initial_reason = initial_result.get("reason", "LLM未提供原因，默认等待")
            current_emoji_query = initial_result.get("emoji_query", "")  # 获取 emoji_query
            logger.info(
                f"[私聊][{self.private_name}] LLM 初步规划行动: {initial_action}, 原因: {initial_reason}表情查询: '{current_emoji_query}'"
            )
            if conversation_info:  # 确保 conversation_info 存在
                conversation_info.current_emoji_query = current_emoji_query
        except Exception as llm_err:
            logger.error(f"[私聊][{self.private_name}] 调用 LLM 或解析初步规划结果时出错: {llm_err}")
            logger.error(traceback.format_exc())
            return "wait", f"行动规划 LLM 调用或解析出错: {llm_err}"

        # --- 4. 处理特殊动作 (end_conversation) ---
        final_action = initial_action
        final_reason = initial_reason

        if initial_action == "end_conversation":
            try:
                time_str_for_end_decision = "获取时间失败"
                if (
                    observation_info
                    and hasattr(observation_info, "current_time_str")
                    and observation_info.current_time_str
                ):
                    time_str_for_end_decision = observation_info.current_time_str
                final_action, final_reason = await self._handle_end_conversation_decision(
                    persona_text,
                    chat_history_text,
                    initial_reason,
                    time_str_for_end_decision,
                    sender_name_str=sender_name_str,
                    relationship_text_str=relationship_text_str,
                )
            except Exception as end_dec_err:
                logger.error(f"[私聊][{self.private_name}] 处理结束对话决策时出错: {end_dec_err}")
                logger.warning(f"[私聊][{self.private_name}] 结束决策出错，将按原计划执行 end_conversation")
                final_action = "end_conversation"  # 保持原计划
                final_reason = initial_reason

        # --- [移除] 不再需要在这里检查 wait 动作的约束 ---
        # elif initial_action == "wait":
        #     # ... (移除之前的检查逻辑) ...
        #     final_action = "wait"
        #     final_reason = initial_reason

        # --- 5. 验证最终行动类型 ---
        valid_actions_default = [
            "direct_reply",
            "send_new_message",
            "send_memes",
            "wait",
            "listening",
            "rethink_goal",
            "end_conversation",
            "block_and_ignore",
            "say_goodbye",
        ]
        valid_actions_reflect = [  # PROMPT_REFLECT_AND_ACT 的动作
            "wait",
            "listening",
            "rethink_goal",
            "end_conversation",
            "block_and_ignore",
            # PROMPT_REFLECT_AND_ACT 也可以 end_conversation，然后也可能触发 say_goodbye
            "say_goodbye",
        ]

        current_valid_actions = valid_actions_reflect if use_reflect_prompt else valid_actions_default

        if final_action not in current_valid_actions:
            logger.warning(f"[私聊][{self.private_name}] LLM 返回了未知的行动类型: '{final_action}'，强制改为 wait")
            final_reason = f"(原始行动'{final_action}'无效，已强制改为wait) {final_reason}"
            final_action = "wait"  # 遇到无效动作，默认等待

        plan_duration = time.time() - plan_start_time
        logger.success(f"[私聊][{self.private_name}] 最终规划行动: {final_action} (总耗时: {plan_duration:.3f} 秒)")
        logger.info(f"[私聊][{self.private_name}] 行动原因: {final_reason}")
        return final_action, final_reason

    # --- Helper methods for preparing prompt inputs  ---

    def _get_bot_last_speak_time_info(self, observation_info: ObservationInfo) -> str:
        """获取机器人上次发言时间提示"""

        time_info = ""
        try:
            if not observation_info or not observation_info.bot_id:
                return ""
            bot_id_str = str(observation_info.bot_id)
            if hasattr(observation_info, "chat_history") and observation_info.chat_history:
                for msg in reversed(observation_info.chat_history):
                    if not isinstance(msg, dict):
                        continue
                    sender_info = msg.get("user_info", {})
                    sender_id = str(sender_info.get("user_id")) if isinstance(sender_info, dict) else None
                    msg_time = msg.get("time")
                    if sender_id == bot_id_str and msg_time:
                        time_diff = time.time() - msg_time
                        if time_diff < 60.0:
                            time_info = f"提示：你上一条成功发送的消息是在 {time_diff:.1f} 秒前。\n"
                        break
        except AttributeError as e:
            logger.warning(f"[私聊][{self.private_name}] 获取 Bot 上次发言时间时属性错误: {e}")
        except Exception as e:
            logger.warning(f"[私聊][{self.private_name}] 获取 Bot 上次发言时间时出错: {e}")
        return time_info

    def _get_timeout_context(self, conversation_info: ConversationInfo) -> str:
        """获取超时提示信息"""

        timeout_context = ""
        try:
            if hasattr(conversation_info, "goal_list") and conversation_info.goal_list:
                last_goal_item = conversation_info.goal_list[-1]
                last_goal_text = ""
                if isinstance(last_goal_item, dict):
                    last_goal_text = last_goal_item.get("goal", "")
                elif isinstance(last_goal_item, str):
                    last_goal_text = last_goal_item
                if (
                    isinstance(last_goal_text, str)
                    and "分钟，" in last_goal_text
                    and "思考接下来要做什么" in last_goal_text
                ):
                    wait_time_str = last_goal_text.split("分钟，")[0].replace("你等待了", "").strip()
                    timeout_context = f"重要提示：对方已经长时间（约 {wait_time_str} 分钟）没有回复你的消息了，对方可能去忙了，也可能在对方看来对话已经结束。请基于此情况规划下一步。\n"
                    logger.debug(f"[私聊][{self.private_name}] 检测到超时目标: {last_goal_text}")
        except AttributeError as e:
            logger.warning(f"[私聊][{self.private_name}] 检查超时目标时属性错误: {e}")
        except Exception as e:
            logger.warning(f"[私聊][{self.private_name}] 检查超时目标时出错: {e}")
        return timeout_context

    def _build_goals_string(self, conversation_info: ConversationInfo) -> str:
        """构建对话目标字符串"""

        goals_str = ""
        try:
            if hasattr(conversation_info, "goal_list") and conversation_info.goal_list:
                recent_goals = conversation_info.goal_list[-3:]
                for goal_item in recent_goals:
                    goal = "目标内容缺失"
                    reasoning = "没有明确原因"
                    if isinstance(goal_item, dict):
                        goal = goal_item.get("goal", goal)
                        reasoning = goal_item.get("reasoning", reasoning)
                    elif isinstance(goal_item, str):
                        goal = goal_item
                    goal = str(goal) if goal is not None else "目标内容缺失"
                    reasoning = str(reasoning) if reasoning is not None else "没有明确原因"
                    goals_str += f"- 目标：{goal}\n  原因：{reasoning}\n"
                if not goals_str:
                    goals_str = "- 目前没有明确对话目标，请考虑设定一个。\n"
            else:
                goals_str = "- 目前没有明确对话目标，请考虑设定一个。\n"
        except AttributeError as e:
            logger.warning(f"[私聊][{self.private_name}] 构建对话目标字符串时属性错误: {e}")
            goals_str = "- 获取对话目标时出错。\n"
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 构建对话目标字符串时出错: {e}")
            goals_str = "- 构建对话目标时出错。\n"
        return goals_str

    def _build_action_history_context(self, conversation_info: ConversationInfo) -> Tuple[str, str]:
        """构建行动历史概要和上一次行动详细情况"""

        action_history_summary = "你最近执行的行动历史：\n"
        last_action_context = "关于你【上一次尝试】的行动：\n"
        action_history_list: List[Dict[str, Any]] = []
        try:
            if hasattr(conversation_info, "done_action") and conversation_info.done_action:
                action_history_list = conversation_info.done_action[-5:]
        except AttributeError as e:
            logger.warning(f"[私聊][{self.private_name}] 获取行动历史时属性错误: {e}")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 访问行动历史时出错: {e}")
        if not action_history_list:
            action_history_summary += "- 还没有执行过行动。\n"
            last_action_context += "- 这是你规划的第一个行动。\n"
        else:
            for i, action_data in enumerate(action_history_list):
                if not isinstance(action_data, dict):
                    logger.warning(f"[私聊][{self.private_name}] 行动历史记录格式错误，跳过: {action_data}")
                    continue
                action_type = action_data.get("action", "未知动作")
                plan_reason = action_data.get("plan_reason", "未知规划原因")
                status = action_data.get("status", "未知状态")
                final_reason = action_data.get("final_reason", "")
                action_time = action_data.get("time", "未知时间")
                reason_text = f", 最终原因: “{final_reason}”" if final_reason else ""
                summary_line = f"- 时间:{action_time}, 尝试:'{action_type}', 状态:{status}{reason_text}"
                action_history_summary += summary_line + "\n"
                if i == len(action_history_list) - 1:
                    last_action_context += f"- 上次【规划】的行动是: '{action_type}'\n"
                    last_action_context += f"- 当时规划的【原因】是: {plan_reason}\n"
                    if status == "done":
                        last_action_context += "- 该行动已【成功执行】。\n"
                    elif status == "recall" or status == "error" or status.startswith("cancelled"):
                        last_action_context += "- 但该行动最终【未能成功执行/被取消/出错】。\n"
                        if final_reason:
                            last_action_context += f"- 【重要】失败/取消/错误原因是: “{final_reason}”\n"
                        else:
                            last_action_context += "- 【重要】失败/取消/错误原因未明确记录。\n"
                    elif status == "start":
                        last_action_context += "- 该行动【正在执行中】或【未完成】。\n"
                    else:
                        last_action_context += f"- 该行动当前状态未知: {status}\n"
        return action_history_summary, last_action_context

    # --- Helper method for handling end_conversation decision  ---

    async def _handle_end_conversation_decision(
        self,
        persona_text: str,
        chat_history_text: str,
        initial_reason: str,
        current_time_str: str,
        sender_name_str: str,
        relationship_text_str: str,
    ) -> Tuple[str, str]:
        """处理结束对话前的告别决策"""
        logger.info(f"[私聊][{self.private_name}] 初步规划结束对话，进入告别决策...")
        end_decision_prompt = PROMPT_END_DECISION.format(
            persona_text=persona_text,
            chat_history_text=chat_history_text,
            current_time_str=current_time_str,
            sender_name=sender_name_str,
            relationship_text=relationship_text_str,
        )
        logger.debug(f"[私聊][{self.private_name}] 发送到LLM的结束决策提示词:\n------\n{end_decision_prompt}\n------")
        llm_start_time = time.time()
        end_content, _ = await self.llm.generate_response_async(end_decision_prompt)
        llm_duration = time.time() - llm_start_time
        logger.debug(f"[私聊][{self.private_name}] LLM (结束决策) 耗时: {llm_duration:.3f} 秒, 原始返回: {end_content}")
        end_success, end_result = get_items_from_json(
            end_content,
            self.private_name,
            "say_bye",
            "reason",
            default_values={"say_bye": "no", "reason": "结束决策LLM返回格式错误，默认不告别"},
            required_types={"say_bye": str, "reason": str},
        )
        say_bye_decision = end_result.get("say_bye", "no").lower()
        end_decision_reason = end_result.get("reason", "未提供原因")
        if end_success and say_bye_decision == "yes":
            logger.info(f"[私聊][{self.private_name}] 结束决策: yes, 准备生成告别语. 原因: {end_decision_reason}")
            final_action = "say_goodbye"
            final_reason = f"决定发送告别语 (原因: {end_decision_reason})。原结束理由: {initial_reason}"
            return final_action, final_reason
        else:
            logger.info(f"[私聊][{self.private_name}] 结束决策: no, 直接结束对话. 原因: {end_decision_reason}")
            return "end_conversation", initial_reason
