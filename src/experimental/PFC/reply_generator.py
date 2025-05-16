import random
from datetime import datetime
from .pfc_utils import retrieve_contextual_info
from typing import Optional
from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from .reply_checker import ReplyChecker
from src.individuality.individuality import Individuality
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from .pfc_utils import build_chat_history_text

logger = get_logger("pfc_reply")

PROMPT_GER_VARIATIONS = [
    ("不用输出或提及对方的网名或绰号", 0.50),
    ("如果当前对话比较轻松，可以尝试用轻松幽默或者略带调侃的语气回应，但要注意分寸", 0.8),
    ("避免使用过于正式或书面化的词语，多用生活化的口语表达", 0.8),
    ("如果对方的发言比较跳跃或难以理解，可以尝试用猜测或确认的语气回应", 0.8),
    ("如果感觉对话有点干巴，可以尝试引入一些轻松的相关小话题或者自己的小想法，但不要偏离太远", 0.8),
    ("注意观察对方的情绪（如果能从文字中判断），并作出适当的回应，比如安慰、鼓励或表示理解", 0.8),
    ("", 0.10),
]

REPLY_STYLE1_VARIATIONS = [
    ("整体风格可以平和、简短", 0.3),
    ("回复可以非常简洁，有时甚至用单个词、短语或者一个反问就能表达清楚", 0.10),
    ("尝试使用更自然的口语连接词，例如：然后/所以呢/不过嘛/倒是", 0.05),
    ("在表达观点时，可以说得主观一些，例如：我觉得.../我个人感觉.../要我说...", 0.10),
    ("**请省略主语，简短**", 0.4),
    ("回复得认真一些", 0.05),
]

REPLY_STYLE2_VARIATIONS = [
    ("结尾可以使用语气助词，例如：呀/噢/诶/哈/啦，让语气更生动", 0.10),
    ("不要输出任何语气词", 0.10),
    ("在适当的时候，可以用一些感叹词来表达情绪或态度，例如：哇/啊？/啧啧/哎呀", 0.05),
    ("可以模糊化表达，例如：'我记得...'", 0.10),
    ("对于一些无聊或者不想深入的话题，可以敷衍一下，例如：/哦这样啊/还行吧/随便啦", 0.10),
    ("尽量用简单句和短句", 0.25),
    ("不要输出任何标点符号，简短", 0.30),
]

# --- 定义 Prompt 模板 ---

# Prompt for direct_reply (首次回复)
PROMPT_DIRECT_REPLY = """
当前时间：{current_time_str}
{persona_text}。
你正在和{sender_name}在QQ上私聊。
你与对方的关系是：{relationship_text}
你现在的心情是：{current_emotion_text}
请根据以下信息生成一条回复：

当前对话目标：{goals_str}

你有以下这些知识：
{retrieved_knowledge_str}
请你**记住上面的知识**，在回复中有可能会用到。

你有以下记忆可供参考：
{retrieved_global_memory_str}

{retrieved_historical_chat_str}

最近的聊天记录：
{chat_history_text}

{last_rejection_info}



请根据上述信息，结合聊天记录，回复对方。该回复应该：
1. 符合对话目标，以"你"的角度发言（不要自己与自己对话！）
2. 符合你的性格特征和身份细节
3. 通俗易懂，自然流畅，像正常聊天一样，简短（通常20字以内，除非特殊情况）
4. 可以适当利用相关知识和回忆，但**不要生硬引用**，若无必要，也可以不利用
5. 自然、得体，结合聊天记录逻辑合理，没有重复表达同质内容，也没有复读你之前的发言

请注意把握聊天内容，不要回复的太有条理，可以有个性。请分清"你"和对方说的话，不要把"你"说的话当做对方说的话，这是你自己说的话。
可以回复得自然随意自然一些，就像真人一样，注意把握聊天内容，{reply_style1}，不要刻意突出自身学科背景，不要说你说过的话，{reply_style2}。
{prompt_ger}，请你注意不要输出多余内容(包括前后缀，冒号和引号，括号，表情等)，只输出回复内容。
不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )。

请直接输出回复内容，不需要任何额外格式。"""

# Prompt for send_new_message (追问/补充)
PROMPT_SEND_NEW_MESSAGE = """
当前时间：{current_time_str}
{persona_text}。
你正在和{sender_name}在QQ上私聊，**并且刚刚你已经发送了一条或多条消息**
你与对方的关系是：{relationship_text}
你现在的心情是：{current_emotion_text}
现在请根据以下信息判断你是否要继续发一条新消息，当然，如果你决定继续发消息不合适，也可以不发：

当前对话目标：{goals_str}

你有以下这些知识：
{retrieved_knowledge_str}
请你**记住上面的知识**，在发消息时有可能会用到。

你有以下记忆可供参考：
{retrieved_global_memory_str}

{retrieved_historical_chat_str}

最近的聊天记录：
{chat_history_text}

{last_rejection_info}

请根据上述信息，判断你是否要继续发一条新消息（例如对之前消息的补充，深入话题，或追问等等）。如果你觉得要发送，该消息应该：
1. 符合对话目标，以"你"的角度发言（不要自己与自己对话！）
2. 符合你的性格特征和身份细节
3. 通俗易懂，自然流畅，像正常聊天一样，简短（通常20字以内，除非特殊情况）
4. 可以适当利用相关知识和回忆，但**不要生硬引用**，若无必要，也可以不利用
5. 跟之前你发的消息自然的衔接，逻辑合理，没有重复表达同质内容或部分重叠内容，也没有复读你之前的发言

请注意把握聊天内容，不用太有条理，可以有个性。请分清"你"和对方说的话，不要把"你"说的话当做对方说的话，这是你自己说的话。
这条消息可以自然随意自然一些，就像真人一样，注意把握聊天内容，{reply_style1}，不要刻意突出自身学科背景，不要说你说过的话，{reply_style2}。
{prompt_ger}。
如果你决定继续发消息不合适，也可以不发送。

请严格按照以下JSON格式输出你的选择和消息内容，不要包含任何其他说明或非JSON文本：
{{
  "send": "yes/no",
  "txt": "如果选择发送，这里是具体的消息文本。如果选择不发送，这里也填写 'no'。"
}}
"""

# Prompt for say_goodbye (告别语生成)
PROMPT_FAREWELL = """
当前时间：{current_time_str}
{persona_text}。
你正在和{sender_name}在QQ上私聊，现在你们的对话似乎已经结束。
你与对方的关系是：{relationship_text}
你现在的心情是：{current_emotion_text}
现在你决定再发一条最后的消息来圆满结束。

最近的聊天记录：
{chat_history_text}

请根据上述信息，结合聊天记录，构思一条**简短、自然、符合你人设**的最后的消息。
这条消息应该：
1. 从你自己的角度发言。
2. 符合你的性格特征和身份细节。
3. 通俗易懂，自然流畅，通常很简短。
4. 自然地为这场对话画上句号，避免开启新话题或显得冗长、刻意。

请像真人一样随意自然，**简洁是关键**。
不要输出多余内容（包括前后缀、冒号、引号、括号、表情包、at或@等）。

请直接输出最终的告别消息内容，不需要任何额外格式。"""


class ReplyGenerator:
    """回复生成器"""

    def __init__(self, stream_id: str, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_PFC_chat,
            temperature=global_config.llm_PFC_chat["temp"],
            max_tokens=300,  # 对于JSON输出，这个可能需要适当调整，但一般回复短，JSON结构也简单
            request_type="reply_generation",
        )
        self.personality_info = Individuality.get_instance().get_prompt(x_person=2, level=3)
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.reply_checker = ReplyChecker(stream_id, private_name)

    # 修改 generate 方法签名，增加 action_type 参数
    async def generate(
        self, observation_info: ObservationInfo, conversation_info: ConversationInfo, action_type: str
    ) -> str:
        """生成回复

        Args:
            observation_info: 观察信息
            conversation_info: 对话信息
            action_type: 当前执行的动作类型 ('direct_reply', 'send_new_message', 'say_goodbye')

        Returns:
            str: 生成的回复。
                对于 'direct_reply' 和 'say_goodbye'，返回纯文本回复。
                对于 'send_new_message'，返回包含决策和文本的JSON字符串。
        """
        logger.debug(
            f"[私聊][{self.private_name}]开始生成回复 (动作类型: {action_type})：当前目标: {conversation_info.goal_list}"
        )

        chosen_prompt_ger = random.choices(
            [style[0] for style in PROMPT_GER_VARIATIONS], weights=[style[1] for style in PROMPT_GER_VARIATIONS], k=1
        )[0]
        chosen_reply_style1 = random.choices(
            [style[0] for style in REPLY_STYLE1_VARIATIONS],
            weights=[style[1] for style in REPLY_STYLE1_VARIATIONS],
            k=1,
        )[0]
        chosen_reply_style2 = random.choices(
            [style[0] for style in REPLY_STYLE2_VARIATIONS],
            weights=[style[1] for style in REPLY_STYLE2_VARIATIONS],
            k=1,
        )[0]

        # --- 构建通用 Prompt 参数 ---
        goals_str = ""
        if conversation_info.goal_list:
            for goal_reason in conversation_info.goal_list:
                if isinstance(goal_reason, dict):
                    goal = goal_reason.get("goal", "目标内容缺失")
                    reasoning = goal_reason.get("reasoning", "没有明确原因")
                else:
                    goal = str(goal_reason)
                    reasoning = "没有明确原因"

                goal = str(goal) if goal is not None else "目标内容缺失"
                reasoning = str(reasoning) if reasoning is not None else "没有明确原因"
                goals_str += f"- 目标：{goal}\n  原因：{reasoning}\n"
        else:
            goals_str = "- 目前没有明确对话目标\n"

        chat_history_for_prompt_builder: list = []
        recent_history_start_time_for_exclusion: Optional[float] = None

        # 我们需要知道 build_chat_history_text 函数大致会用 observation_info.chat_history 的多少条记录
        # 或者 build_chat_history_text 内部的逻辑。
        # 假设 build_chat_history_text 主要依赖 observation_info.chat_history_str，
        # 而 observation_info.chat_history_str 是基于 observation_info.chat_history 的最后一部分（比如20条）生成的。
        # 为了准确，我们应该直接从 observation_info.chat_history 中获取这个片段的起始时间。
        # 请确保这里的 MAX_RECENT_HISTORY_FOR_PROMPT 与 observation_info.py 或 build_chat_history_text 中
        # 用于生成 chat_history_str 的消息数量逻辑大致吻合。
        # 如果 build_chat_history_text 总是用 observation_info.chat_history 的最后 N 条，那么这个 N 就是这里的数字。
        # 如果 observation_info.chat_history_str 是由 observation_info.py 中的 update_from_message 等方法维护的，
        # 并且总是代表一个固定长度（比如最后30条）的聊天记录字符串，那么我们就需要从 observation_info.chat_history
        # 取出这部分原始消息来确定起始时间。

        # 我们先做一个合理的假设： “最近聊天记录” 字符串 chat_history_text 是基于
        # observation_info.chat_history 的一个有限的尾部片段生成的。
        # 假设这个片段的长度由 global_config.pfc_recent_history_display_count 控制，默认为20条。
        recent_history_display_count = getattr(global_config, "pfc_recent_history_display_count", 20)

        if observation_info and observation_info.chat_history and len(observation_info.chat_history) > 0:
            # 获取用于生成“最近聊天记录”的实际消息片段
            # 如果 observation_info.chat_history 长度小于 display_count，则取全部
            start_index = max(0, len(observation_info.chat_history) - recent_history_display_count)
            chat_history_for_prompt_builder = observation_info.chat_history[start_index:]

            if chat_history_for_prompt_builder:  # 如果片段不为空
                try:
                    first_message_in_display_slice = chat_history_for_prompt_builder[0]
                    recent_history_start_time_for_exclusion = first_message_in_display_slice.get("time")
                    if recent_history_start_time_for_exclusion:
                        # 导入 datetime (如果 reply_generator.py 文件顶部没有的话)
                        # from datetime import datetime # 通常建议放在文件顶部
                        logger.debug(
                            f"[{self.private_name}] (ReplyGenerator) “最近聊天记录”片段(共{len(chat_history_for_prompt_builder)}条)的最早时间戳: "
                            f"{recent_history_start_time_for_exclusion} "
                            f"(即 {datetime.fromtimestamp(recent_history_start_time_for_exclusion).strftime('%Y-%m-%d %H:%M:%S')})"
                        )
                    else:
                        logger.warning(f"[{self.private_name}] (ReplyGenerator) “最近聊天记录”片段的首条消息无时间戳。")
                except (IndexError, KeyError, TypeError) as e:
                    logger.warning(f"[{self.private_name}] (ReplyGenerator) 获取“最近聊天记录”起始时间失败: {e}")
                    recent_history_start_time_for_exclusion = None
        else:
            logger.debug(
                f"[{self.private_name}] (ReplyGenerator) observation_info.chat_history 为空，无法确定“最近聊天记录”起始时间。"
            )
        # --- [新代码结束] ---

        chat_history_text = await build_chat_history_text(observation_info, self.private_name)

        sender_name_str = self.private_name

        relationship_text_str = getattr(conversation_info, "relationship_text", "你们还不熟悉。")
        current_emotion_text_str = getattr(conversation_info, "current_emotion_text", "心情平静。")

        persona_text = f"你的名字是{self.name}，{self.personality_info}。"
        historical_chat_query = ""
        num_recent_messages_for_query = 3  # 例如，取最近3条作为查询引子
        if observation_info.chat_history and len(observation_info.chat_history) > 0:
            # 从 chat_history (已处理并存入 ObservationInfo 的历史) 中取最新N条
            # 或者，如果 observation_info.unprocessed_messages 更能代表“当前上下文”，也可以考虑用它
            # 我们先用 chat_history，因为它包含了双方的对话历史，可能更稳定
            recent_messages_for_query_list = observation_info.chat_history[-num_recent_messages_for_query:]

            # 将这些消息的文本内容合并
            query_texts_list = []
            for msg_dict in recent_messages_for_query_list:
                text_content = msg_dict.get("processed_plain_text", "")
                if text_content.strip():  # 只添加有内容的文本
                    # 可以选择是否添加发送者信息到查询文本中，例如：
                    # sender_nickname = msg_dict.get("user_info", {}).get("user_nickname", "用户")
                    # query_texts_list.append(f"{sender_nickname}: {text_content}")
                    query_texts_list.append(text_content)  # 简单合并文本内容

            if query_texts_list:
                historical_chat_query = " ".join(query_texts_list).strip()
                logger.debug(
                    f"[私聊][{self.private_name}] (ReplyGenerator) 生成的私聊历史查询文本 (最近{num_recent_messages_for_query}条): '{historical_chat_query[:100]}...'"
                )
            else:
                logger.debug(
                    f"[私聊][{self.private_name}] (ReplyGenerator) 最近{num_recent_messages_for_query}条消息无有效文本内容，不进行私聊历史查询。"
                )
        else:
            logger.debug(f"[私聊][{self.private_name}] (ReplyGenerator) 无聊天历史可用于生成私聊历史查询文本。")

        current_chat_id = self.chat_observer.stream_id if self.chat_observer else None
        if not current_chat_id:
            logger.error(f"[私聊][{self.private_name}] (ReplyGenerator) 无法获取 current_chat_id，跳过所有上下文检索！")
            retrieved_global_memory_str = "[获取全局记忆出错：chat_id 未知]"
            retrieved_knowledge_str = "[获取知识出错：chat_id 未知]"
            retrieved_historical_chat_str = "[获取私聊历史回忆出错：chat_id 未知]"
        else:
            # retrieval_context 之前是用 chat_history_text，现在也用它作为全局记忆和知识的检索上下文
            retrieval_context_for_global_and_knowledge = chat_history_text

            (
                retrieved_global_memory_str,
                retrieved_knowledge_str,
                retrieved_historical_chat_str,  # << 新增接收私聊历史回忆
            ) = await retrieve_contextual_info(
                text=retrieval_context_for_global_and_knowledge,  # 用于全局记忆和知识
                private_name=self.private_name,
                chat_id=current_chat_id,  # << 传递 chat_id
                historical_chat_query_text=historical_chat_query,  # << 传递专门的查询文本
                current_short_term_history_earliest_time=recent_history_start_time_for_exclusion,  # <--- 新增传递的参数
            )
        # === 调用修改结束 ===

        logger.info(
            f"[私聊][{self.private_name}] (ReplyGenerator) 上下文检索完成。\n"
            f"  全局记忆: {'有内容' if '回忆起' in retrieved_global_memory_str else '无或出错'}\n"
            f"  知识: {'有内容' if '出错' not in retrieved_knowledge_str and '无相关知识' not in retrieved_knowledge_str and retrieved_knowledge_str.strip() else '无或出错'}\n"
            f"  私聊历史回忆: {'有内容' if '回忆起一段相关的历史聊天' in retrieved_historical_chat_str else '无或出错'}"
        )

        last_rejection_info_str = ""
        last_reason = getattr(conversation_info, "last_reply_rejection_reason", None)
        last_content = getattr(conversation_info, "last_rejected_reply_content", None)

        if last_reason and last_content:
            if last_reason == "机器人尝试发送重复消息":  # 这是我们从 ReplyChecker 设置的特定原因
                last_rejection_info_str = (
                    f"\n------\n"
                    f"【重要提示：你上一次尝试发送的消息 “{last_content}” 与你更早之前发送过的某条消息完全相同。这属于复读行为，请避免。】\n"
                    f"请根据此提示调整你的新回复，确保内容新颖，不要重复你已经说过的话。\n"
                    f"------\n"
                )
                logger.debug(
                    f"[私聊][{self.private_name}] (ReplyGenerator) 检测到自身复读，将加入特定警告到 Prompt:\n"
                    f"  内容: {last_content}"
                )
            else:  # 其他类型的拒绝原因，保持原有格式
                last_rejection_info_str = (
                    f"\n------\n"
                    f"【重要提示：你上一次尝试回复时失败了，以下是详细信息】\n"
                    f"上次试图发送的消息内容： “{last_content}”\n"
                    f"失败原因： “{last_reason}”\n"
                    f"请根据【消息内容】和【失败原因】调整你的新回复，避免重复之前的错误。\n"
                    f"------\n"
                )
                logger.debug(
                    f"[私聊][{self.private_name}] (ReplyGenerator) 检测到上次回复失败信息，将加入 Prompt:\n"
                    f"  内容: {last_content}\n"
                    f"  原因: {last_reason}"
                )

        # 新增：构建刷屏警告信息 for PROMPT_SEND_NEW_MESSAGE
        # spam_warning_message = ""
        # if action_type == "send_new_message":  # 只在 send_new_message 时构建刷屏警告
        # if conversation_info.my_message_count > 5:
        # spam_warning_message = f"⚠️【警告】**你已连续发送{str(conversation_info.my_message_count)}条消息！请谨慎考虑是否继续发送！以免刷屏对造成对方困扰！**"
        # elif conversation_info.my_message_count > 2:
        # spam_warning_message = f"💬【提示】**你已连续发送{str(conversation_info.my_message_count)}条消息。如果非必要，请避免连续发送，以免给对方造成困扰。**"
        # if spam_warning_message:
        # spam_warning_message = f"\n{spam_warning_message}\n"

        # --- 选择 Prompt ---
        if action_type == "send_new_message":
            prompt_template = PROMPT_SEND_NEW_MESSAGE
            logger.info(f"[私聊][{self.private_name}]使用 PROMPT_SEND_NEW_MESSAGE (追问/补充生成, 期望JSON输出)")
        elif action_type == "say_goodbye":
            prompt_template = PROMPT_FAREWELL
            logger.info(f"[私聊][{self.private_name}]使用 PROMPT_FAREWELL (告别语生成)")
        else:
            prompt_template = PROMPT_DIRECT_REPLY
            logger.info(f"[私聊][{self.private_name}]使用 PROMPT_DIRECT_REPLY (首次/非连续回复生成)")

        # --- 格式化最终的 Prompt ---
        try:
            current_time_value = "获取时间失败"
            if observation_info and hasattr(observation_info, "current_time_str") and observation_info.current_time_str:
                current_time_value = observation_info.current_time_str

            base_format_params = {
                "persona_text": persona_text,
                "goals_str": goals_str,
                "chat_history_text": chat_history_text
                if chat_history_text.strip()
                else "还没有聊天记录。",  # 当前短期历史
                "retrieved_global_memory_str": retrieved_global_memory_str
                if retrieved_global_memory_str.strip()
                else "无相关全局记忆。",
                "retrieved_knowledge_str": retrieved_knowledge_str
                if retrieved_knowledge_str.strip()
                else "无相关知识。",
                "retrieved_historical_chat_str": retrieved_historical_chat_str
                if retrieved_historical_chat_str.strip()
                else "无相关私聊历史回忆。",  # << 新增
                "last_rejection_info": last_rejection_info_str,
                "current_time_str": current_time_value,
                "sender_name": sender_name_str,
                "relationship_text": relationship_text_str,
                "current_emotion_text": current_emotion_text_str,
                "reply_style1": chosen_reply_style1,
                "reply_style2": chosen_reply_style2,
                "prompt_ger": chosen_prompt_ger,
            }

            if action_type == "send_new_message":
                current_format_params = base_format_params.copy()
                # current_format_params["spam_warning_info"] = spam_warning_message
                prompt = prompt_template.format(**current_format_params)
            elif action_type == "say_goodbye":
                farewell_params = {
                    k: v
                    for k, v in base_format_params.items()
                    if k
                    in [
                        "persona_text",
                        "chat_history_text",
                        "current_time_str",
                        "sender_name",
                        "relationship_text",
                        "current_emotion_text",
                    ]
                }

                prompt = prompt_template.format(**farewell_params)
            else:  # direct_reply
                current_format_params = base_format_params.copy()
                prompt = prompt_template.format(**current_format_params)

        except KeyError as e:
            logger.error(
                f"[私聊][{self.private_name}]格式化 Prompt 时出错，缺少键: {e}。请检查 Prompt 模板和传递的参数。"
            )
            return "抱歉，准备回复时出了点问题，请检查一下我的代码..."  # 对于JSON期望的场景，这里可能也需要返回一个固定的错误JSON
        except Exception as fmt_err:
            logger.error(f"[私聊][{self.private_name}]格式化 Prompt 时发生未知错误: {fmt_err}")
            return "抱歉，准备回复时出了点内部错误，请检查一下我的代码..."

        # --- 调用 LLM 生成 ---
        logger.debug(f"[私聊][{self.private_name}]发送到LLM的生成提示词:\n------\n{prompt}\n------")
        try:
            content, _ = await self.llm.generate_response_async(prompt)
            # 对于 PROMPT_SEND_NEW_MESSAGE，我们期望 content 是一个 JSON 字符串
            # 对于其他 prompts，content 是纯文本回复
            # 该方法现在直接返回 LLM 的原始输出，由调用者 (conversation._handle_action) 负责解析
            logger.debug(f"[私聊][{self.private_name}]LLM原始生成内容: {content}")
            return content

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]生成回复时出错: {e}")
            # 根据 action_type 返回不同的错误指示
            if action_type == "send_new_message":
                # 返回一个表示错误的JSON，让调用方知道出错了但仍能解析
                return """{{
                    "send": "no",
                    "txt": "LLM生成回复时出错"
                }}""".strip()
            else:
                return "抱歉，我现在有点混乱，让我重新思考一下..."
