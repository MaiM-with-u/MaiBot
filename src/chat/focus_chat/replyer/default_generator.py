import traceback
from typing import List, Optional, Dict, Any, Tuple

from src.chat.message_receive.message import MessageRecv, MessageThinking, MessageSending
from src.chat.message_receive.message import Seg  # Local import needed after move
from src.chat.message_receive.message import UserInfo
from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.timer_calculator import Timer  # <--- Import Timer
from src.chat.focus_chat.heartFC_sender import HeartFCSender
from src.chat.utils.utils import process_llm_response
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.focus_chat.hfc_utils import parse_thinking_id_to_timestamp
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
from src.chat.express.exprssion_learner import get_expression_learner
import time
import random
from datetime import datetime
import re

logger = get_logger("replyer")


def init_prompt():
    Prompt(
        """
{expression_habits_block}
{structured_info_block}
{memory_block}
{relation_info_block}
{extra_info_block}
{time_block}
{chat_target}
{chat_info}
{reply_target_block}
{identity}

你需要使用合适的语言习惯和句法，参考聊天内容，组织一条日常且口语化的回复。注意不要复读你说过的话。
{config_expression_style}
{keywords_reaction_prompt}
请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。
不要浮夸，不要夸张修辞，请注意不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出一条回复就好。
现在，你说：
""",
        "default_generator_prompt",
    )

    Prompt(
        """
{expression_habits_block}
{structured_info_block}
{memory_block}
{relation_info_block}
{extra_info_block}
{time_block}
{chat_target}
{chat_info}
现在"{sender_name}"说:{target_message}。你想要回复对方的这条消息。
{identity}，
你需要使用合适的语法和句法，参考聊天内容，组织一条日常且口语化的回复。注意不要复读你说过的话。

{config_expression_style}
{keywords_reaction_prompt}
请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。
不要浮夸，不要夸张修辞，请注意不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出一条回复就好。
现在，你说：
""",
        "default_generator_private_prompt",
    )

    Prompt(
        """
你可以参考你的以下的语言习惯，如果情景合适就使用，不要盲目使用,不要生硬使用，而是结合到表达中：
{style_habbits}

你现在正在群里聊天，以下是群里正在进行的聊天内容：
{chat_info}

以上是聊天内容，你需要了解聊天记录中的内容

{chat_target}
你的名字是{bot_name}，{prompt_personality}，在这聊天中，"{sender_name}"说的"{target_message}"引起了你的注意，对这句话，你想表达：{raw_reply},原因是：{reason}。你现在要思考怎么回复
你需要使用合适的语法和句法，参考聊天内容，组织一条日常且口语化的回复。请你修改你想表达的原句，符合你的表达风格和语言习惯
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
你的名字是{bot_name}，{prompt_personality}，在这聊天中，"{sender_name}"说的"{target_message}"引起了你的注意，对这句话，你想表达：{raw_reply},原因是：{reason}。你现在要思考怎么回复
你需要使用合适的语法和句法，参考聊天内容，组织一条日常且口语化的回复。
请你根据情景使用以下句法：
{grammar_habbits}
{config_expression_style}，你可以完全重组回复，保留最基本的表达含义就好，但重组后保持语意通顺。
不要浮夸，不要夸张修辞，平淡且不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )，只输出一条回复就好。
现在，你说：
""",
        "default_expressor_private_prompt",  # New template for private FOCUSED chat
    )


class DefaultReplyer:
    def __init__(self, chat_stream: ChatStream):
        self.log_prefix = "replyer"
        # TODO: API-Adapter修改标记
        self.express_model = LLMRequest(
            model=global_config.model.replyer_1,
            request_type="focus.replyer",
        )
        self.heart_fc_sender = HeartFCSender()

        self.chat_stream = chat_stream
        self.is_group_chat, self.chat_target_info = get_chat_type_and_target_info(self.chat_stream.stream_id)

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
        return None

    async def generate_reply_with_context(
        self,
        reply_data: Dict[str, Any],
    ) -> Tuple[bool, Optional[List[str]]]:
        """
        回复器 (Replier): 核心逻辑，负责生成回复文本。
        (已整合原 HeartFCGenerator 的功能)
        """
        try:
            # 3. 构建 Prompt
            with Timer("构建Prompt", {}):  # 内部计时器，可选保留
                prompt = await self.build_prompt_reply_context(
                    reply_data=reply_data,  # 传递action_data
                )

            # 4. 调用 LLM 生成回复
            content = None
            reasoning_content = None
            model_name = "unknown_model"

            try:
                with Timer("LLM生成", {}):  # 内部计时器，可选保留
                    logger.info(f"{self.log_prefix}Prompt:\n{prompt}\n")
                    content, (reasoning_content, model_name) = await self.express_model.generate_response_async(prompt)

                    logger.info(f"最终回复: {content}")

            except Exception as llm_e:
                # 精简报错信息
                logger.error(f"{self.log_prefix}LLM 生成失败: {llm_e}")
                return False, None  # LLM 调用失败则无法生成回复

            processed_response = process_llm_response(content)

            # 5. 处理 LLM 响应
            if not content:
                logger.warning(f"{self.log_prefix}LLM 生成了空内容。")
                return False, None
            if not processed_response:
                logger.warning(f"{self.log_prefix}处理后的回复为空。")
                return False, None

            reply_set = []
            for str in processed_response:
                reply_seg = ("text", str)
                reply_set.append(reply_seg)

            return True, reply_set

        except Exception as e:
            logger.error(f"{self.log_prefix}回复生成意外失败: {e}")
            traceback.print_exc()
            return False, None

    async def rewrite_reply_with_context(
        self,
        reply_data: Dict[str, Any],
    ) -> Tuple[bool, Optional[List[str]]]:
        """
        表达器 (Expressor): 核心逻辑，负责生成回复文本。
        """
        try:
            reply_to = reply_data.get("reply_to", "")
            raw_reply = reply_data.get("raw_reply", "")
            reason = reply_data.get("reason", "")

            with Timer("构建Prompt", {}):  # 内部计时器，可选保留
                prompt = await self.build_prompt_rewrite_context(
                    raw_reply=raw_reply,
                    reason=reason,
                    reply_to=reply_to,
                )

            content = None
            reasoning_content = None
            model_name = "unknown_model"
            if not prompt:
                logger.error(f"{self.log_prefix}Prompt 构建失败，无法生成回复。")
                return False, None

            try:
                with Timer("LLM生成", {}):  # 内部计时器，可选保留
                    # TODO: API-Adapter修改标记
                    content, (reasoning_content, model_name) = await self.express_model.generate_response_async(prompt)

                    logger.info(f"想要表达：{raw_reply}||理由：{reason}")
                    logger.info(f"最终回复: {content}\n")

            except Exception as llm_e:
                # 精简报错信息
                logger.error(f"{self.log_prefix}LLM 生成失败: {llm_e}")
                return False, None  # LLM 调用失败则无法生成回复

            processed_response = process_llm_response(content)

            # 5. 处理 LLM 响应
            if not content:
                logger.warning(f"{self.log_prefix}LLM 生成了空内容。")
                return False, None
            if not processed_response:
                logger.warning(f"{self.log_prefix}处理后的回复为空。")
                return False, None

            reply_set = []
            for str in processed_response:
                reply_seg = ("text", str)
                reply_set.append(reply_seg)

            return True, reply_set

        except Exception as e:
            logger.error(f"{self.log_prefix}回复生成意外失败: {e}")
            traceback.print_exc()
            return False, None

    async def build_prompt_reply_context(
        self,
        reply_data=None,
    ) -> str:
        chat_stream = self.chat_stream

        is_group_chat = bool(chat_stream.group_info)

        self_info_block = reply_data.get("self_info_block", "")
        structured_info = reply_data.get("structured_info", "")
        relation_info_block = reply_data.get("relation_info_block", "")
        reply_to = reply_data.get("reply_to", "none")
        memory_block = reply_data.get("memory_block", "")

        # 优先使用 extra_info_block，没有则用 extra_info
        extra_info_block = reply_data.get("extra_info_block", "") or reply_data.get("extra_info", "")

        sender = ""
        target = ""
        if ":" in reply_to or "：" in reply_to:
            # 使用正则表达式匹配中文或英文冒号
            parts = re.split(pattern=r"[:：]", string=reply_to, maxsplit=1)
            if len(parts) == 2:
                sender = parts[0].strip()
                target = parts[1].strip()

        message_list_before_now = get_raw_msg_before_timestamp_with_chat(
            chat_id=chat_stream.stream_id,
            timestamp=time.time(),
            limit=global_config.focus_chat.observation_context_size,
        )
        # print(f"message_list_before_now: {message_list_before_now}")
        chat_talking_prompt = build_readable_messages(
            message_list_before_now,
            replace_bot_name=True,
            merge_messages=False,
            timestamp_mode="normal_no_YMD",
            read_mark=0.0,
            truncate=True,
            show_actions=True,
        )
        # print(f"chat_talking_prompt: {chat_talking_prompt}")

        style_habbits = []
        grammar_habbits = []

        # 使用从处理器传来的选中表达方式
        selected_expressions = reply_data.get("selected_expressions", []) if reply_data else []

        if selected_expressions:
            logger.info(f"{self.log_prefix} 使用处理器选中的{len(selected_expressions)}个表达方式")
            for expr in selected_expressions:
                if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                    expr_type = expr.get("type", "style")
                    if expr_type == "grammar":
                        grammar_habbits.append(f"当{expr['situation']}时，使用 {expr['style']}")
                    else:
                        style_habbits.append(f"当{expr['situation']}时，使用 {expr['style']}")
        else:
            logger.debug(f"{self.log_prefix} 没有从处理器获得表达方式，将使用空的表达方式")
            # 不再在replyer中进行随机选择，全部交给处理器处理

        style_habbits_str = "\n".join(style_habbits)
        grammar_habbits_str = "\n".join(grammar_habbits)

        # 动态构建expression habits块
        expression_habits_block = ""
        if style_habbits_str.strip():
            expression_habits_block += f"你可以参考以下的语言习惯，如果情景合适就使用，不要盲目使用,不要生硬使用，而是结合到表达中：\n{style_habbits_str}\n\n"
        if grammar_habbits_str.strip():
            expression_habits_block += f"请你根据情景使用以下句法：\n{grammar_habbits_str}\n"

        if structured_info:
            structured_info_block = f"以下是一些额外的信息，现在请你阅读以下内容，进行决策\n{structured_info}\n以上是一些额外的信息，现在请你阅读以下内容，进行决策"
        else:
            structured_info_block = ""

        if extra_info_block:
            extra_info_block = f"以下是你在回复时需要参考的信息，现在请你阅读以下内容，进行决策\n{extra_info_block}\n以上是你在回复时需要参考的信息，现在请你阅读以下内容，进行决策"
        else:
            extra_info_block = ""

        # 关键词检测与反应
        keywords_reaction_prompt = ""
        try:
            # 处理关键词规则
            for rule in global_config.keyword_reaction.keyword_rules:
                if any(keyword in target for keyword in rule.keywords):
                    logger.info(f"检测到关键词规则：{rule.keywords}，触发反应：{rule.reaction}")
                    keywords_reaction_prompt += f"{rule.reaction}，"

            # 处理正则表达式规则
            for rule in global_config.keyword_reaction.regex_rules:
                for pattern_str in rule.regex:
                    try:
                        pattern = re.compile(pattern_str)
                        if result := pattern.search(target):
                            reaction = rule.reaction
                            for name, content in result.groupdict().items():
                                reaction = reaction.replace(f"[{name}]", content)
                            logger.info(f"匹配到正则表达式：{pattern_str}，触发反应：{reaction}")
                            keywords_reaction_prompt += reaction + "，"
                            break
                    except re.error as e:
                        logger.error(f"正则表达式编译错误: {pattern_str}, 错误信息: {str(e)}")
                        continue
        except Exception as e:
            logger.error(f"关键词检测与反应时发生异常: {str(e)}", exc_info=True)

        time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        # logger.debug("开始构建 focus prompt")
        bot_name = global_config.bot.nickname
        if global_config.bot.alias_names:
            bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
        else:
            bot_nickname = ""
        bot_core_personality = global_config.personality.personality_core
        indentify_block = f"你的名字是{bot_name}{bot_nickname}，你{bot_core_personality}："

        if sender:
            reply_target_block = f"现在{sender}说的:{target}。引起了你的注意，你想要在群里发言或者回复这条消息。"
        elif target:
            reply_target_block = f"现在{target}引起了你的注意，你想要在群里发言或者回复这条消息。"
        else:
            reply_target_block = "现在，你想要在群里发言或者回复消息。"

        # --- Choose template based on chat type ---
        if is_group_chat:
            template_name = "default_generator_prompt"
            # Group specific formatting variables (already fetched or default)
            chat_target_1 = await global_prompt_manager.get_prompt_async("chat_target_group1")
            # chat_target_2 = await global_prompt_manager.get_prompt_async("chat_target_group2")

            prompt = await global_prompt_manager.format_prompt(
                template_name,
                expression_habits_block=expression_habits_block,
                chat_target=chat_target_1,
                chat_info=chat_talking_prompt,
                memory_block=memory_block,
                structured_info_block=structured_info_block,
                extra_info_block=extra_info_block,
                relation_info_block=relation_info_block,
                self_info_block=self_info_block,
                time_block=time_block,
                reply_target_block=reply_target_block,
                keywords_reaction_prompt=keywords_reaction_prompt,
                identity=indentify_block,
                target_message=target,
                sender_name=sender,
                config_expression_style=global_config.expression.expression_style,
            )
        else:  # Private chat
            template_name = "default_generator_private_prompt"
            # 在私聊时获取对方的昵称信息
            chat_target_name = "对方"
            if self.chat_target_info:
                chat_target_name = (
                    self.chat_target_info.get("person_name") or self.chat_target_info.get("user_nickname") or "对方"
                )
            chat_target_1 = f"你正在和 {chat_target_name} 聊天"
            prompt = await global_prompt_manager.format_prompt(
                template_name,
                expression_habits_block=expression_habits_block,
                chat_target=chat_target_1,
                chat_info=chat_talking_prompt,
                memory_block=memory_block,
                structured_info_block=structured_info_block,
                relation_info_block=relation_info_block,
                extra_info_block=extra_info_block,
                time_block=time_block,
                keywords_reaction_prompt=keywords_reaction_prompt,
                identity=indentify_block,
                target_message=target,
                sender_name=sender,
                config_expression_style=global_config.expression.expression_style,
            )

        return prompt

    async def build_prompt_rewrite_context(
        self,
        reason,
        raw_reply,
        reply_to,
    ) -> str:
        sender = ""
        target = ""
        if ":" in reply_to or "：" in reply_to:
            # 使用正则表达式匹配中文或英文冒号
            parts = re.split(pattern=r"[:：]", string=reply_to, maxsplit=1)
            if len(parts) == 2:
                sender = parts[0].strip()
                target = parts[1].strip()

        chat_stream = self.chat_stream

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

        expression_learner = get_expression_learner()
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
                raw_reply=raw_reply,
                sender_name=sender,
                target_message=target,
                config_expression_style=global_config.expression.expression_style,
            )
        else:  # Private chat
            template_name = "default_expressor_private_prompt"
            # 在私聊时获取对方的昵称信息
            chat_target_name = "对方"
            if self.chat_target_info:
                chat_target_name = (
                    self.chat_target_info.get("person_name") or self.chat_target_info.get("user_nickname") or "对方"
                )
            chat_target_1 = f"你正在和 {chat_target_name} 聊天"
            prompt = await global_prompt_manager.format_prompt(
                template_name,
                style_habbits=style_habbits_str,
                grammar_habbits=grammar_habbits_str,
                chat_target=chat_target_1,
                chat_info=chat_talking_prompt,
                bot_name=global_config.bot.nickname,
                prompt_personality="",
                reason=reason,
                raw_reply=raw_reply,
                sender_name=sender,
                target_message=target,
                config_expression_style=global_config.expression.expression_style,
            )

        return prompt

    async def send_response_messages(
        self,
        anchor_message: Optional[MessageRecv],
        response_set: List[Tuple[str, str]],
        thinking_id: str = "",
        display_message: str = "",
    ) -> Optional[MessageSending]:
        """发送回复消息 (尝试锚定到 anchor_message)，使用 HeartFCSender"""
        chat = self.chat_stream
        chat_id = self.chat_stream.stream_id
        if chat is None:
            logger.error(f"{self.log_prefix} 无法发送回复，chat_stream 为空。")
            return None
        if not anchor_message:
            logger.error(f"{self.log_prefix} 无法发送回复，anchor_message 为空。")
            return None

        stream_name = get_chat_manager().get_stream_name(chat_id) or chat_id  # 获取流名称用于日志

        # 检查思考过程是否仍在进行，并获取开始时间
        if thinking_id:
            # print(f"thinking_id: {thinking_id}")
            thinking_start_time = await self.heart_fc_sender.get_thinking_start_time(chat_id, thinking_id)
        else:
            print("thinking_id is None")
            # thinking_id = "ds" + str(round(time.time(), 2))
            thinking_start_time = time.time()

        if thinking_start_time is None:
            logger.error(f"[{stream_name}]replyer思考过程未找到或已结束，无法发送回复。")
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

            bot_message: MessageSending = await self._build_single_sending_message(
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
                if (
                    bot_message.is_private_message()
                    or bot_message.reply.processed_plain_text != "[System Trigger Context]"
                    or mark_head
                ):
                    set_reply = False
                else:
                    set_reply = True

                if not mark_head:
                    mark_head = True
                    typing = False
                else:
                    typing = True

                sent_msg = await self.heart_fc_sender.send_message(bot_message, typing=typing, set_reply=set_reply)

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

    async def _build_single_sending_message(
        self,
        message_id: str,
        message_segment: Seg,
        reply_to: bool,
        is_emoji: bool,
        thinking_start_time: float,
        display_message: str,
        anchor_message: MessageRecv = None,
    ) -> MessageSending:
        """构建单个发送消息"""

        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
            platform=self.chat_stream.platform,
        )

        # await anchor_message.process()
        if anchor_message:
            sender_info = anchor_message.message_info.user_info
        else:
            sender_info = None

        bot_message = MessageSending(
            message_id=message_id,  # 使用片段的唯一ID
            chat_stream=self.chat_stream,
            bot_user_info=bot_user_info,
            sender_info=sender_info,
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
