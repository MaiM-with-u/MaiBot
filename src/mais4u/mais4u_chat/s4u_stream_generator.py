import os
from typing import AsyncGenerator
from src.mais4u.openai_client import AsyncOpenAIClient
from src.config.config import global_config, model_config
from src.chat.message_receive.message import MessageRecvS4U
from src.mais4u.mais4u_chat.s4u_prompt import prompt_builder
from src.common.logger import get_logger
import asyncio
import re


logger = get_logger("s4u_stream_generator")


class S4UStreamGenerator:
    def __init__(self):
        replyer_1_config = model_config.model_task_config.replyer_1
        model_to_use = replyer_1_config.model_list[0]
        model_info = model_config.get_model_info(model_to_use)
        if not model_info:
            logger.error(f"模型 {model_to_use} 在配置中未找到")
            raise ValueError(f"模型 {model_to_use} 在配置中未找到")
        provider_name = model_info.api_provider
        provider_info = model_config.get_provider(provider_name)
        if not provider_info:
            logger.error("`replyer_1` 找不到对应的Provider")
            raise ValueError("`replyer_1` 找不到对应的Provider")

        api_key = provider_info.api_key
        base_url = provider_info.base_url

        if not api_key:
            logger.error(f"{provider_name}没有配置API KEY")
            raise ValueError(f"{provider_name}没有配置API KEY")

        self.client_1 = AsyncOpenAIClient(api_key=api_key, base_url=base_url)
        self.model_1_name = model_to_use
        self.replyer_1_config = replyer_1_config

        self.current_model_name = "unknown model"
        self.partial_response = ""

        # 正则表达式用于按句子切分，同时处理各种标点和边缘情况
        # 匹配常见的句子结束符，但会忽略引号内和数字中的标点
        self.sentence_split_pattern = re.compile(
            r'([^\s\w"\'([{]*["\'([{].*?["\'}\])][^\s\w"\'([{]*|'  # 匹配被引号/括号包裹的内容
            r'[^.。!?？！\n\r]+(?:[.。!?？！\n\r](?![\'"])|$))',  # 匹配直到句子结束符
            re.UNICODE | re.DOTALL,
        )

        self.chat_stream = None

    async def build_last_internal_message(self, message: MessageRecvS4U, previous_reply_context: str = ""):
        # person_id = PersonInfoManager.get_person_id(
        #     message.chat_stream.user_info.platform, message.chat_stream.user_info.user_id
        # )
        # person_info_manager = get_person_info_manager()
        # person_name = await person_info_manager.get_value(person_id, "person_name")

        # if message.chat_stream.user_info.user_nickname:
        #     if person_name:
        #         sender_name = f"[{message.chat_stream.user_info.user_nickname}]（你叫ta{person_name}）"
        #     else:
        #         sender_name = f"[{message.chat_stream.user_info.user_nickname}]"
        # else:
        #     sender_name = f"用户({message.chat_stream.user_info.user_id})"

        # 构建prompt
        if previous_reply_context:
            message_txt = f"""
            你正在回复用户的消息，但中途被打断了。这是已有的对话上下文:
            [你已经对上一条消息说的话]: {previous_reply_context}
            ---
            [这是用户发来的新消息, 你需要结合上下文，对此进行回复]:
            {message.processed_plain_text}
            """
            return True, message_txt
        else:
            message_txt = message.processed_plain_text
            return False, message_txt

    async def generate_response(
        self, message: MessageRecvS4U, previous_reply_context: str = ""
    ) -> AsyncGenerator[str, None]:
        """根据当前模型类型选择对应的生成函数"""
        # 从global_config中获取模型概率值并选择模型
        self.partial_response = ""
        message_txt = message.processed_plain_text
        if not message.is_internal:
            interupted, message_txt_added = await self.build_last_internal_message(message, previous_reply_context)
            if interupted:
                message_txt = message_txt_added

        message.chat_stream = self.chat_stream
        prompt = await prompt_builder.build_prompt_normal(
            message=message,
            message_txt=message_txt,
        )

        logger.info(
            f"{self.current_model_name}思考:{message_txt[:30] + '...' if len(message_txt) > 30 else message_txt}"
        )  # noqa: E501

        current_client = self.client_1
        self.current_model_name = self.model_1_name

        extra_kwargs = {}
        if self.replyer_1_config.get("enable_thinking") is not None:
            extra_kwargs["enable_thinking"] = self.replyer_1_config.get("enable_thinking")
        if self.replyer_1_config.get("thinking_budget") is not None:
            extra_kwargs["thinking_budget"] = self.replyer_1_config.get("thinking_budget")

        async for chunk in self._generate_response_with_model(
            prompt, current_client, self.current_model_name, **extra_kwargs
        ):
            yield chunk

    async def _generate_response_with_model(
        self,
        prompt: str,
        client: AsyncOpenAIClient,
        model_name: str,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        buffer = ""
        delimiters = "，。！？,.!?\n\r"  # For final trimming
        punctuation_buffer = ""

        async for content in client.get_stream_content(
            messages=[{"role": "user", "content": prompt}], model=model_name, **kwargs
        ):
            buffer += content

            # 使用正则表达式匹配句子
            last_match_end = 0
            for match in self.sentence_split_pattern.finditer(buffer):
                sentence = match.group(0).strip()
                if sentence:
                    # 如果句子看起来完整（即不只是等待更多内容），则发送
                    if match.end(0) < len(buffer) or sentence.endswith(tuple(delimiters)):
                        # 检查是否只是一个标点符号
                        if sentence in [",", "，", ".", "。", "!", "！", "?", "？"]:
                            punctuation_buffer += sentence
                        else:
                            # 发送之前累积的标点和当前句子
                            to_yield = punctuation_buffer + sentence
                            if to_yield.endswith((",", "，")):
                                to_yield = to_yield.rstrip(",，")

                            self.partial_response += to_yield
                            yield to_yield
                            punctuation_buffer = ""  # 清空标点符号缓冲区
                            await asyncio.sleep(0)  # 允许其他任务运行

                        last_match_end = match.end(0)

            # 从缓冲区移除已发送的部分
            if last_match_end > 0:
                buffer = buffer[last_match_end:]

        # 发送缓冲区中剩余的任何内容
        to_yield = (punctuation_buffer + buffer).strip()
        if to_yield:
            if to_yield.endswith(("，", ",")):
                to_yield = to_yield.rstrip("，,")
            if to_yield:
                self.partial_response += to_yield
                yield to_yield
