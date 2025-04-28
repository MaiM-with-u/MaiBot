# PFC/pfc_heartflow.py
import traceback
import time
from typing import Optional, Dict, Any
from src.individuality.individuality import Individuality
from src.common.logger import get_module_logger
from src.common.utils_llm import LLMRequest # Assuming utils_model is renamed or refactored
from ...config.config import global_config # Make sure path is correct
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from maim_message import UserInfo # Assuming maim_message provides UserInfo
from src.plugins.utils.chat_message_builder import build_readable_messages # Make sure path is correct

logger = get_module_logger("pfc_heartflow")

# --- Heartflow Generation Prompts ---

# Prompt (1): Initial Heartflow Generation (When conversation starts or bot receives first message)
PROMPT_INITIAL = """{persona_text}
现在你正在上网，{sender_name} ({sender_id}) 刚刚给你发来了一条QQ私聊消息。

【最近的聊天记录】(包括你已成功发送的消息 和 新收到的消息)
{chat_history_text}
--- 消息结束 ---

请根据以上信息，结合你的性格，设想一下你收到这条消息时的第一反应和真实想法。
要求：
- 输出一段连贯、自然、真实的内心独白。
- 像一个真人在思考，语气可以随意一些，符合你的性格。
- 不要分点输出，不要包含任何动作描写、表情符号、引号或括号。
- **只输出内心独白内容，不要任何额外文字或格式。**
"""

# Prompt (2): Heartflow Update After Successfully Sending a Message
PROMPT_AFTER_SEND = """{persona_text}
你正在和 {sender_name} ({sender_id}) QQ私聊。
你刚才的想法是：『{previous_heartflow}』
刚刚你成功发送了一条消息给对方："{last_bot_message}"

【你们最近的聊天记录】(包括你刚发送的消息)
{chat_history_text}
--- 消息结束 ---

现在，请结合你刚才的想法、你发送的消息以及聊天记录，继续思考。
- 你的想法可以是对刚才发送内容的补充、延伸，或者思考对方可能的反应，或者计划下一步说什么。
- 保持想法的连贯性，但也要注意话题的推进，不要停留在完全相同的想法上，除非你觉得有必要强调。
- 输出一段连贯、自然、真实的内心独白。
- 像一个真人在思考，语气可以随意一些，符合你的性格。
- 不要分点输出，不要包含任何动作描写、表情符号、引号或括号。
- **只输出内心独白内容，不要任何额外文字或格式。**
"""

# Prompt (3): Heartflow Update After Reply Check Failed (e.g., message rejected)
PROMPT_AFTER_FAIL = """{persona_text}
你正在和 {sender_name} ({sender_id}) QQ私聊。
你之前的想法是：『{previous_heartflow}』
你本来想发送一条消息："{failed_message}"
但是，这个想法/回复因为『{fail_reason}』被你自己否定了/觉得不合适。

【你们最近的聊天记录】
{chat_history_text}
--- 消息结束 ---

现在，请结合你之前的想法、被否定的消息以及失败原因，重新思考。
- 你可能会反思为什么刚才的想法不合适，或者思考替代的说法，或者决定暂时不回复。
- 保持想法的连贯性，但要根据失败原因调整思路。
- 输出一段连贯、自然、真实的内心独白。
- 像一个真人在思考，语气可以随意一些，符合你的性格。
- 不要分点输出，不要包含任何动作描写、表情符号、引号或括号。
- **只输出内心独白内容，不要任何额外文字或格式。**
"""

# Prompt (4): Heartflow Update After Waiting Timeout
PROMPT_AFTER_TIMEOUT = """{persona_text}
你正在和 {sender_name} ({sender_id}) QQ私聊。
你之前的想法是：『{previous_heartflow}』
你上次发言是 {time_since_last_bot_speak:.1f} 秒前。
你已经等待了对方 {wait_duration:.1f} 分钟没有回应。 ({timeout_reason})

【你们最近的聊天记录】
{chat_history_text}
--- 消息结束 ---

对方长时间没有回复，你现在的想法是什么？
- 你可能会思考对方为什么没回，是在忙吗？还是对话结束了？
- 你可能会考虑是否要发点什么打破沉默，或者就此结束对话。
- 结合你之前的想法和等待的情况进行思考。
- 输出一段连贯、自然、真实的内心独白。
- 像一个真人在思考，语气可以随意一些，符合你的性格。
- 不要分点输出，不要包含任何动作描写、表情符号、引号或括号。
- **只输出内心独白内容，不要任何额外文字或格式。**
"""

# Prompt (5): Heartflow Update When Rethinking Goal
PROMPT_WHEN_RETHINKING = """{persona_text}
你正在和 {sender_name} ({sender_id}) QQ私聊。
你之前的想法是：『{previous_heartflow}』
你觉得现在需要重新思考一下对话的目标或方向了。({rethink_reason})

【你们最近的聊天记录】
{chat_history_text}
--- 消息结束 ---

请结合你之前的想法和需要重新思考目标的原因，梳理一下你现在的思路。
- 你可能会回顾一下之前的对话，思考当前进展如何。
- 你可能会考虑开启新的话题，或者如何引导对话到你期望的方向。
- 输出一段连贯、自然、真实的内心独白。
- 像一个真人在思考，语气可以随意一些，符合你的性格。
- 不要分点输出，不要包含任何动作描写、表情符号、引号或括号。
- **只输出内心独白内容，不要任何额外文字或格式。**
"""


class HeartflowGenerator:
    """心流生成器"""

    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        # 这里假设你在 config.py 中定义了名为 'llm_PFC_heartflow' 的新 LLM 配置
        # 需要确保 global_config 中存在 llm_PFC_heartflow 这个键
        try:
            self.llm = LLMRequest(
                model=global_config.llm_PFC_heartflow, # 使用新的配置
                temperature=global_config.llm_PFC_heartflow.get("temp", 0.8), # 假设温度设置，可调整
                max_tokens=global_config.llm_PFC_heartflow.get("max_tokens", 200), # 限制心流长度，可调整
                request_type="heartflow_generation",
            )
        except AttributeError:
            logger.error("*"*20)
            logger.error("错误：无法找到 'llm_PFC_heartflow' 配置！")
            logger.error("请确保在 config.py 中定义了 llm_PFC_heartflow 的 LLM 配置。")
            logger.error("将使用 llm_normal 作为备用，但这可能不是最佳效果。")
            logger.error("*"*20)
            # 使用备用配置，但这可能不是最优选择
            self.llm = LLMRequest(
                model=global_config.llm_normal,
                temperature=0.8,
                max_tokens=200,
                request_type="heartflow_generation_fallback",
            )

        self.personality_info = Individuality.get_instance().get_prompt(type="personality", x_person=2, level=3)
        self.identity_detail_info = Individuality.get_instance().get_prompt(type="identity", x_person=2, level=2)
        self.name = global_config.BOT_NICKNAME
        self.bot_id = str(global_config.BOT_QQ)


    async def generate_heartflow(
        self,
        situation: str, # e.g., "initial", "after_send", "after_fail", "after_timeout", "rethinking"
        observation_info: ObservationInfo,
        conversation_info: ConversationInfo,
        context_data: Optional[Dict[str, Any]] = None # To pass specific data like failed_message, reason, etc.
    ) -> str:
        """根据不同情境生成心流

        Args:
            situation: 当前情境标识符
            observation_info: 观察信息
            conversation_info: 对话信息
            context_data: 传递特定情境所需的数据 (e.g., {'last_bot_message': '...', 'previous_heartflow': '...'})

        Returns:
            str: 生成的心流文本
        """
        if context_data is None:
            context_data = {}

        logger.info(f"开始生成心流，情境: {situation}")

        # --- 构建通用 Prompt 参数 ---
        # Persona Text (Character info)
        identity_details_only = self.identity_detail_info
        identity_addon = ""
        if isinstance(identity_details_only, str):
            pronouns = ["你", "我", "他"]
            for p in pronouns:
                if identity_details_only.startswith(p):
                    identity_details_only = identity_details_only[len(p) :]
                    break
            if identity_details_only.endswith("。"):
                identity_details_only = identity_details_only[:-1]
            cleaned_details = identity_details_only.strip(",， ")
            if cleaned_details:
                identity_addon = f"并且{cleaned_details}"
        persona_text = f"你的名字是{self.name}，{self.personality_info}{identity_addon}。"

        # Chat History
        chat_history_text = "还没有聊天记录。"
        sender_name = "对方"
        sender_id = "未知"
        if hasattr(observation_info, 'chat_history_str') and observation_info.chat_history_str:
            chat_history_text = observation_info.chat_history_str
            # Try to get sender info from the last message if available
            last_msg = observation_info.chat_history[-1] if observation_info.chat_history else None
            if isinstance(last_msg, dict):
                 user_info_dict = last_msg.get('user_info')
                 if isinstance(user_info_dict, dict):
                     user_info = UserInfo.from_dict(user_info_dict)
                     # Get info of the other person, not the bot itself
                     if str(user_info.user_id) != self.bot_id:
                         sender_name = user_info.user_nickname or "对方"
                         sender_id = str(user_info.user_id)

        # Previous Heartflow (handle None case)
        previous_heartflow = context_data.get('previous_heartflow', conversation_info.current_heartflow if hasattr(conversation_info, 'current_heartflow') else '')
        if not previous_heartflow:
            previous_heartflow = "你之前还没来得及形成具体的想法。" # Default if no previous thought

        # --- 选择并格式化 Prompt ---
        prompt_template = None
        format_params = {
            "persona_text": persona_text,
            "sender_name": sender_name,
            "sender_id": sender_id,
            "chat_history_text": chat_history_text,
            "previous_heartflow": previous_heartflow,
            "bot_name": self.name,
            # Add more common params if needed
        }

        if situation == "initial":
            prompt_template = PROMPT_INITIAL
            # 'initial' specific params (if any, likely none needed beyond common ones)
        elif situation == "after_send":
            prompt_template = PROMPT_AFTER_SEND
            format_params["last_bot_message"] = context_data.get("last_bot_message", "（未能获取到刚发送的消息）")
        elif situation == "after_fail":
            prompt_template = PROMPT_AFTER_FAIL
            format_params["failed_message"] = context_data.get("failed_message", "（未能获取到失败的消息）")
            format_params["fail_reason"] = context_data.get("fail_reason", "（未知原因）")
        elif situation == "after_timeout":
            prompt_template = PROMPT_AFTER_TIMEOUT
            # Calculate times - requires observation_info to be up-to-date
            now = time.time()
            time_since_last_bot_speak = (now - observation_info.last_bot_speak_time) if observation_info.last_bot_speak_time else float('inf')
            wait_duration_minutes = context_data.get("wait_duration", 0) / 60.0 # Expect duration in seconds from context_data
            format_params["time_since_last_bot_speak"] = time_since_last_bot_speak
            format_params["wait_duration"] = wait_duration_minutes
            format_params["timeout_reason"] = context_data.get("timeout_reason", "长时间未回应")
        elif situation == "rethinking":
             prompt_template = PROMPT_WHEN_RETHINKING
             format_params["rethink_reason"] = context_data.get("rethink_reason", "需要调整对话方向")
        else:
            logger.warning(f"未知的的心流生成情境: {situation}，将使用 'initial' 作为默认。")
            prompt_template = PROMPT_INITIAL

        # --- 调用 LLM 生成 ---
        try:
            final_prompt = prompt_template.format(**format_params)
            logger.debug(f"发送到LLM的心流生成提示词 (情境: {situation}):\n------\n{final_prompt}\n------")

            heartflow_content, _ = await self.llm.generate_response_async(final_prompt)
            # Clean up potential unwanted prefixes/suffixes if LLM adds them
            heartflow_content = heartflow_content.strip().strip('\"').strip('\'').strip("内心独白：").strip()
            logger.info(f"生成的心流 (情境: {situation}): 『{heartflow_content}』")
            return heartflow_content

        except KeyError as e:
             logger.error(f"格式化心流 Prompt 时缺少键: {e}")
             logger.error(f"可用参数: {format_params.keys()}")
             return f"（生成内心想法时出错：缺少参数 {e}）"
        except Exception as e:
            logger.error(f"生成心流时出错 (情境: {situation}): {e}")
            logger.error(traceback.format_exc())
            return "（生成内心想法时遇到错误）"