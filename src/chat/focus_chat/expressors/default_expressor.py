import traceback
from typing import List, Optional, Dict, Any, Tuple
from src.chat.message_receive.message import MessageRecv, MessageThinking, MessageSending
from src.chat.message_receive.message import Seg  # Local import needed after move
from src.chat.message_receive.message import UserInfo
from src.chat.message_receive.chat_stream import chat_manager
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.utils_image import image_path_to_base64  # Local import needed after move
from src.chat.utils.timer_calculator import Timer  # <--- Import Timer
from src.chat.emoji_system.emoji_manager import emoji_manager
from src.chat.focus_chat.heartFC_sender import HeartFCSender
from src.chat.utils.utils import process_llm_response
from src.chat.utils.info_catcher import info_catcher_manager
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.focus_chat.hfc_utils import parse_thinking_id_to_timestamp
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
import time
from src.chat.focus_chat.expressors.exprssion_learner import expression_learner
import random

logger = get_logger("expressor")


def init_prompt():
    Prompt(
        """
你可以参考以下的语言习惯，如果情景合适就使用，不要盲目使用,不要生硬使用，而是结合到表达中：
{style_habbits}

你现在正在群里聊天，以下是群里正在进行的聊天内容：
{chat_info}

以上是聊天内容，你需要了解聊天记录中的内容

{chat_target}
你的名字是{bot_name}，{prompt_personality}，在这聊天中，"{target_message}"引起了你的注意，对这句话，你想表达：{in_mind_reply},原因是：{reason}。你现在要思考怎么回复
你需要使用合适的语法和句法，参考聊天内容，组织一条日常且口语化的回复。
请你根据情景使用以下句法：
{grammar_habbits}
{config_expression_style}，你可以完全重组回复，保留最基本的表达含义就好，但重组后保持语意通顺。
不要浮夸，不要夸张修辞，平淡且不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )，只输出一条回复就好。
现在，你说：
""",
        "default_expressor_prompt",
    )

    Prompt(
        """
你可以参考以下的语言习惯，如果情景合适就使用，不要盲目使用,不要生硬使用，而是结合到表达中：
{style_habbits}

你现在正在群里聊天，以下是群里正在进行的聊天内容：
{chat_info}

以上是聊天内容，你需要了解聊天记录中的内容

{chat_target}
你的名字是{bot_name}，{prompt_personality}，在这聊天中，"{target_message}"引起了你的注意，对这句话，你想表达：{in_mind_reply},原因是：{reason}。你现在要思考怎么回复
你需要使用合适的语法和句法，参考聊天内容，组织一条日常且口语化的回复。
请你根据情景使用以下句法：
{grammar_habbits}
{config_expression_style}，你可以完全重组回复，保留最基本的表达含义就好，但重组后保持语意通顺。
不要浮夸，不要夸张修辞，平淡且不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )，只输出一条回复就好。
现在，你说：
""",
        "default_expressor_private_prompt",  # New template for private FOCUSED chat
    )


class DefaultExpressor:
    def __init__(self, chat_stream: ChatStream):
        self.log_prefix = "expressor"
        # TODO: API-Adapter修改标记
        self.express_model = LLMRequest(
            model=global_config.model.focus_expressor,
            # temperature=global_config.model.focus_expressor["temp"],
            max_tokens=256,
            request_type="focus.expressor",
        )
        self.heart_fc_sender = HeartFCSender()

        self.chat_id = chat_stream.stream_id
        self.chat_stream = chat_stream
        self.is_group_chat, self.chat_target_info = get_chat_type_and_target_info(self.chat_id)

    async def _create_thinking_message(self, anchor_message: Optional[MessageRecv], thinking_id: str):
        """创建思考消息 (尝试锚定到 anchor_message)"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self.log_prefix} 无法创建思考消息，缺少有效的锚点消息或聊天流。")
            return None

        chat = anchor_message.chat_stream
        messageinfo = anchor_message.message_info
        thinking_time_point = parse_thinking_id_to_timestamp(thinking_id)
        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=messageinfo.platform,
        )

        thinking_message = MessageThinking(
            message_id=thinking_id,
            chat_stream=chat,
            bot_user_info=bot_user_info,
            reply=anchor_message,  # 回复的是锚点消息
            thinking_start_time=thinking_time_point,
        )
        # logger.debug(f"创建思考消息thinking_message：{thinking_message}")

        await self.heart_fc_sender.register_thinking(thinking_message)

    async def deal_reply(
        self,
        cycle_timers: dict,
        action_data: Dict[str, Any],
        reasoning: str,
        anchor_message: MessageRecv,
        thinking_id: str,
    ) -> tuple[bool, Optional[List[Tuple[str, str]]]]:
        # 创建思考消息
        await self._create_thinking_message(anchor_message, thinking_id)

        reply = []  # 初始化 reply，防止未定义
        try:
            has_sent_something = False

            # 处理文本部分
            text_part = action_data.get("text", [])
            if text_part:
                with Timer("生成回复", cycle_timers):
                    # 可以保留原有的文本处理逻辑或进行适当调整
                    reply = await self.express(
                        in_mind_reply=text_part,
                        anchor_message=anchor_message,
                        thinking_id=thinking_id,
                        reason=reasoning,
                        action_data=action_data,
                    )

            with Timer("选择表情", cycle_timers):
                emoji_keyword = action_data.get("emojis", [])
                emoji_base64 = await self._choose_emoji(emoji_keyword)
                if emoji_base64:
                    reply.append(("emoji", emoji_base64))

            if reply:
                with Timer("发送消息", cycle_timers):
                    sent_msg_list = await self.send_response_messages(
                        anchor_message=anchor_message,
                        thinking_id=thinking_id,
                        response_set=reply,
                    )
                has_sent_something = True
            else:
                logger.warning(f"{self.log_prefix} 文本回复生成失败")

            if not has_sent_something:
                logger.warning(f"{self.log_prefix} 回复动作未包含任何有效内容")

            return has_sent_something, sent_msg_list

        except Exception as e:
            logger.error(f"回复失败: {e}")
            traceback.print_exc()
            return False, None

        # --- 回复器 (Replier) 的定义 --- #

    async def express(
        self,
        in_mind_reply: str,
        reason: str,
        anchor_message: MessageRecv,
        thinking_id: str,
        action_data: Dict[str, Any],
    ) -> Optional[List[str]]:
        """
        回复器 (Replier): 核心逻辑，负责生成回复文本。
        (已整合原 HeartFCGenerator 的功能)
        """
        try:
            # 1. 获取情绪影响因子并调整模型温度
            # arousal_multiplier = mood_manager.get_arousal_multiplier()
            # current_temp = float(global_config.model.normal["temp"]) * arousal_multiplier
            # self.express_model.params["temperature"] = current_temp  # 动态调整温度

            # 2. 获取信息捕捉器
            info_catcher = info_catcher_manager.get_info_catcher(thinking_id)

            # --- Determine sender_name for private chat ---
            sender_name_for_prompt = "某人"  # Default for group or if info unavailable
            if not self.is_group_chat and self.chat_target_info:
                # Prioritize person_name, then nickname
                sender_name_for_prompt = (
                    self.chat_target_info.get("person_name")
                    or self.chat_target_info.get("user_nickname")
                    or sender_name_for_prompt
                )
            # --- End determining sender_name ---

            target_message = action_data.get("target", "")

            # 3. 构建 Prompt
            with Timer("构建Prompt", {}):  # 内部计时器，可选保留
                prompt = await self.build_prompt_focus(
                    chat_stream=self.chat_stream,  # Pass the stream object
                    in_mind_reply=in_mind_reply,
                    reason=reason,
                    sender_name=sender_name_for_prompt,  # Pass determined name
                    target_message=target_message,
                    config_expression_style=global_config.expression.expression_style,
                )

            # 4. 调用 LLM 生成回复
            content = None
            reasoning_content = None
            model_name = "unknown_model"
            if not prompt:
                logger.error(f"{self.log_prefix}[Replier-{thinking_id}] Prompt 构建失败，无法生成回复。")
                return None

            try:
                with Timer("LLM生成", {}):  # 内部计时器，可选保留
                    # TODO: API-Adapter修改标记
                    # logger.info(f"{self.log_prefix}[Replier-{thinking_id}]\nPrompt:\n{prompt}\n")
                    content, (reasoning_content, model_name) = await self.express_model.generate_response_async(prompt)

                    # logger.info(f"{self.log_prefix}\nPrompt:\n{prompt}\n---------------------------\n")

                    logger.info(f"想要表达：{in_mind_reply}||理由：{reason}")
                    logger.info(f"最终回复: {content}\n")

                info_catcher.catch_after_llm_generated(
                    prompt=prompt, response=content, reasoning_content=reasoning_content, model_name=model_name
                )

            except Exception as llm_e:
                # 精简报错信息
                logger.error(f"{self.log_prefix}LLM 生成失败: {llm_e}")
                return None  # LLM 调用失败则无法生成回复

            processed_response = process_llm_response(content)

            # 5. 处理 LLM 响应
            if not content:
                logger.warning(f"{self.log_prefix}LLM 生成了空内容。")
                return None
            if not processed_response:
                logger.warning(f"{self.log_prefix}处理后的回复为空。")
                return None

            reply_set = []
            for str in processed_response:
                reply_seg = ("text", str)
                reply_set.append(reply_seg)

            return reply_set

        except Exception as e:
            logger.error(f"{self.log_prefix}回复生成意外失败: {e}")
            traceback.print_exc()
            return None

    async def build_prompt_focus(
        self,
        reason,
        chat_stream,
        sender_name,
        in_mind_reply,
        target_message,
        config_expression_style,
    ) -> str:
        is_group_chat = bool(chat_stream.group_info)

        message_list_before_now = get_raw_msg_before_timestamp_with_chat(
            chat_id=chat_stream.stream_id,
            timestamp=time.time(),
            limit=global_config.focus_chat.observation_context_size,
        )
        chat_talking_prompt = build_readable_messages(
            message_list_before_now,
            replace_bot_name=True,
            merge_messages=True,
            timestamp_mode="relative",
            read_mark=0.0,
            truncate=True,
        )

        (
            learnt_style_expressions,
            learnt_grammar_expressions,
            personality_expressions,
        ) = await expression_learner.get_expression_by_chat_id(chat_stream.stream_id)

        style_habbits = []
        grammar_habbits = []
        # 1. learnt_expressions加权随机选3条
        if learnt_style_expressions:
            weights = [expr["count"] for expr in learnt_style_expressions]
            selected_learnt = weighted_sample_no_replacement(learnt_style_expressions, weights, 3)
            for expr in selected_learnt:
                if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                    style_habbits.append(f"当{expr['situation']}时，使用 {expr['style']}")
        # 2. learnt_grammar_expressions加权随机选3条
        if learnt_grammar_expressions:
            weights = [expr["count"] for expr in learnt_grammar_expressions]
            selected_learnt = weighted_sample_no_replacement(learnt_grammar_expressions, weights, 3)
            for expr in selected_learnt:
                if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                    grammar_habbits.append(f"当{expr['situation']}时，使用 {expr['style']}")
        # 3. personality_expressions随机选1条
        if personality_expressions:
            expr = random.choice(personality_expressions)
            if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                style_habbits.append(f"当{expr['situation']}时，使用 {expr['style']}")

        style_habbits_str = "\n".join(style_habbits)
        grammar_habbits_str = "\n".join(grammar_habbits)

        logger.debug("开始构建 focus prompt")

        # --- Choose template based on chat type ---
        if is_group_chat:
            template_name = "default_expressor_prompt"
            # Group specific formatting variables (already fetched or default)
            chat_target_1 = await global_prompt_manager.get_prompt_async("chat_target_group1")
            # chat_target_2 = await global_prompt_manager.get_prompt_async("chat_target_group2")

            prompt = await global_prompt_manager.format_prompt(
                template_name,
                style_habbits=style_habbits_str,
                grammar_habbits=grammar_habbits_str,
                chat_target=chat_target_1,
                chat_info=chat_talking_prompt,
                bot_name=global_config.bot.nickname,
                prompt_personality="",
                reason=reason,
                in_mind_reply=in_mind_reply,
                target_message=target_message,
                config_expression_style=config_expression_style,
            )
        else:  # Private chat
            template_name = "default_expressor_private_prompt"
            chat_target_1 = "你正在和人私聊"
            prompt = await global_prompt_manager.format_prompt(
                template_name,
                style_habbits=style_habbits_str,
                grammar_habbits=grammar_habbits_str,
                chat_target=chat_target_1,
                chat_info=chat_talking_prompt,
                bot_name=global_config.bot.nickname,
                prompt_personality="",
                reason=reason,
                in_mind_reply=in_mind_reply,
                target_message=target_message,
                config_expression_style=config_expression_style,
            )

        return prompt

        # --- 发送器 (Sender) --- #

    async def send_response_messages(
        self,
        anchor_message: Optional[MessageRecv],
        response_set: List[Tuple[str, str]],
        thinking_id: str = "",
        display_message: str = "",
    ) -> Optional[MessageSending]:
        """发送回复消息 (尝试锚定到 anchor_message)，使用 HeartFCSender"""
        chat = self.chat_stream
        chat_id = self.chat_id
        if chat is None:
            logger.error(f"{self.log_prefix} 无法发送回复，chat_stream 为空。")
            return None
        if not anchor_message:
            logger.error(f"{self.log_prefix} 无法发送回复，anchor_message 为空。")
            return None

        stream_name = chat_manager.get_stream_name(chat_id) or chat_id  # 获取流名称用于日志

        # 检查思考过程是否仍在进行，并获取开始时间
        if thinking_id:
            thinking_start_time = await self.heart_fc_sender.get_thinking_start_time(chat_id, thinking_id)
        else:
            thinking_id = "ds" + str(round(time.time(), 2))
            thinking_start_time = time.time()

        if thinking_start_time is None:
            logger.error(f"[{stream_name}]expressor思考过程未找到或已结束，无法发送回复。")
            return None

        mark_head = False
        # first_bot_msg: Optional[MessageSending] = None
        reply_message_ids = []  # 记录实际发送的消息ID

        sent_msg_list = []

        for i, msg_text in enumerate(response_set):
            # 为每个消息片段生成唯一ID
            type = msg_text[0]
            data = msg_text[1]

            if global_config.experimental.debug_show_chat_mode and type == "text":
                data += "ᶠ"

            part_message_id = f"{thinking_id}_{i}"
            message_segment = Seg(type=type, data=data)

            if type == "emoji":
                is_emoji = True
            else:
                is_emoji = False
            reply_to = not mark_head

            bot_message = await self._build_single_sending_message(
                anchor_message=anchor_message,
                message_id=part_message_id,
                message_segment=message_segment,
                display_message=display_message,
                reply_to=reply_to,
                is_emoji=is_emoji,
                thinking_id=thinking_id,
                thinking_start_time=thinking_start_time,
            )

            try:
                if not mark_head:
                    mark_head = True
                    # first_bot_msg = bot_message  # 保存第一个成功发送的消息对象
                    typing = False
                else:
                    typing = True

                if type == "emoji":
                    typing = False

                if anchor_message.raw_message:
                    set_reply = True
                else:
                    set_reply = False
                sent_msg = await self.heart_fc_sender.send_message(
                    bot_message, has_thinking=True, typing=typing, set_reply=set_reply
                )

                reply_message_ids.append(part_message_id)  # 记录我们生成的ID

                sent_msg_list.append((type, sent_msg))

            except Exception as e:
                logger.error(f"{self.log_prefix}发送回复片段 {i} ({part_message_id}) 时失败: {e}")
                traceback.print_exc()
                # 这里可以选择是继续发送下一个片段还是中止

        # 在尝试发送完所有片段后，完成原始的 thinking_id 状态
        try:
            await self.heart_fc_sender.complete_thinking(chat_id, thinking_id)

        except Exception as e:
            logger.error(f"{self.log_prefix}完成思考状态 {thinking_id} 时出错: {e}")

        return sent_msg_list

    async def _choose_emoji(self, send_emoji: str):
        """
        选择表情，根据send_emoji文本选择表情，返回表情base64
        """
        emoji_base64 = ""
        emoji_raw = await emoji_manager.get_emoji_for_text(send_emoji)
        if emoji_raw:
            emoji_path, _description, _emotion = emoji_raw
            emoji_base64 = image_path_to_base64(emoji_path)
        return emoji_base64

    async def _build_single_sending_message(
        self,
        anchor_message: MessageRecv,
        message_id: str,
        message_segment: Seg,
        reply_to: bool,
        is_emoji: bool,
        thinking_id: str,
        thinking_start_time: float,
        display_message: str,
    ) -> MessageSending:
        """构建单个发送消息"""

        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=self.chat_stream.platform,
        )

        bot_message = MessageSending(
            message_id=message_id,  # 使用片段的唯一ID
            chat_stream=self.chat_stream,
            bot_user_info=bot_user_info,
            sender_info=anchor_message.message_info.user_info,
            message_segment=message_segment,
            reply=anchor_message,  # 回复原始锚点
            is_head=reply_to,
            is_emoji=is_emoji,
            thinking_start_time=thinking_start_time,  # 传递原始思考开始时间
            display_message=display_message,
        )

        return bot_message


def weighted_sample_no_replacement(items, weights, k) -> list:
    """
    加权且不放回地随机抽取k个元素。

    参数：
        items: 待抽取的元素列表
        weights: 每个元素对应的权重（与items等长，且为正数）
        k: 需要抽取的元素个数
    返回：
        selected: 按权重加权且不重复抽取的k个元素组成的列表

        如果 items 中的元素不足 k 个，就只会返回所有可用的元素

    实现思路：
        每次从当前池中按权重加权随机选出一个元素，选中后将其从池中移除，重复k次。
        这样保证了：
        1. count越大被选中概率越高
        2. 不会重复选中同一个元素
    """
    selected = []
    pool = list(zip(items, weights))
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        r = random.uniform(0, total)
        upto = 0
        for idx, (item, weight) in enumerate(pool):
            upto += weight
            if upto >= r:
                selected.append(item)
                pool.pop(idx)
                break
    return selected


init_prompt()
