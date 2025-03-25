import random
import time
from typing import List, Optional, Tuple, Union

from nonebot import get_driver

from ...common.database import db
from ..models.utils_model import LLM_request
from .config import global_config
from .message import MessageRecv, MessageThinking, Message
from .prompt_builder import prompt_builder
from .utils import process_llm_response
from src.common.logger import get_module_logger, LogConfig, LLM_STYLE_CONFIG

# 定义日志配置
llm_config = LogConfig(
    # 使用消息发送专用样式
    console_format=LLM_STYLE_CONFIG["console_format"],
    file_format=LLM_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("llm_generator", config=llm_config)

driver = get_driver()
config = driver.config


class ResponseGenerator:
    def __init__(self):
        self.model_r1 = LLM_request(
            model=global_config.llm_reasoning,
            temperature=0.7,
            max_tokens=1000,
            stream=True,
            request_type="response",
        )
        self.model_v3 = LLM_request(
            model=global_config.llm_normal, temperature=0.7, max_tokens=3000, request_type="response"
        )
        self.model_r1_distill = LLM_request(
            model=global_config.llm_reasoning_minor, temperature=0.7, max_tokens=3000, request_type="response"
        )
        self.model_sum = LLM_request(
            model=global_config.llm_summary_by_topic, temperature=0.7, max_tokens=3000, request_type="relation"
        )
        self.current_model_type = "r1"  # 默认使用 R1
        self.current_model_name = "unknown model"

    async def generate_response(self, message: MessageThinking) -> Optional[Union[str, List[str]]]:
        """根据当前模型类型选择对应的生成函数"""
        # 从global_config中获取模型概率值并选择模型
        rand = random.random()
        if rand < global_config.MODEL_R1_PROBABILITY:
            self.current_model_type = "r1"
            current_model = self.model_r1
        elif rand < global_config.MODEL_R1_PROBABILITY + global_config.MODEL_V3_PROBABILITY:
            self.current_model_type = "v3"
            current_model = self.model_v3
        else:
            self.current_model_type = "r1_distill"
            current_model = self.model_r1_distill

        logger.info(f"{global_config.BOT_NICKNAME}{self.current_model_type}思考中")

        model_response = await self._generate_response_with_model(message, current_model)
        raw_content = model_response

        # print(f"raw_content: {raw_content}")
        # print(f"model_response: {model_response}")

        if model_response:
            logger.info(f"{global_config.BOT_NICKNAME}的回复是：{model_response}")
            model_response = await self._process_response(model_response)
            if model_response:
                return model_response, raw_content
        return None, raw_content

    async def _generate_response_with_model(self, message: MessageThinking, model: LLM_request) -> Optional[str]:
        """使用指定的模型生成回复"""
        sender_name = ""
        if message.chat_stream.user_info.user_cardname and message.chat_stream.user_info.user_nickname:
            sender_name = (
                f"[({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}]"
                f"{message.chat_stream.user_info.user_cardname}"
            )
        elif message.chat_stream.user_info.user_nickname:
            sender_name = f"({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}"
        else:
            sender_name = f"用户({message.chat_stream.user_info.user_id})"

        # 构建prompt
        prompt, prompt_check = await prompt_builder._build_prompt(
            message.chat_stream,
            message_txt=message.processed_plain_text,
            sender_name=sender_name,
            stream_id=message.chat_stream.stream_id,
        )

        # 读空气模块 简化逻辑，先停用
        # if global_config.enable_kuuki_read:
        #     content_check, reasoning_content_check = await self.model_v3.generate_response(prompt_check)
        #     print(f"\033[1;32m[读空气]\033[0m 读空气结果为{content_check}")
        #     if 'yes' not in content_check.lower() and random.random() < 0.3:
        #         self._save_to_db(
        #             message=message,
        #             sender_name=sender_name,
        #             prompt=prompt,
        #             prompt_check=prompt_check,
        #             content="",
        #             content_check=content_check,
        #             reasoning_content="",
        #             reasoning_content_check=reasoning_content_check
        #         )
        #         return None

        # 生成回复
        try:
            content, reasoning_content, self.current_model_name = await model.generate_response(prompt)
        except Exception:
            logger.exception("生成回复时出错")
            return None

        # 保存到数据库
        self._save_to_db(
            message=message,
            sender_name=sender_name,
            prompt=prompt,
            prompt_check=prompt_check,
            content=content,
            # content_check=content_check if global_config.enable_kuuki_read else "",
            reasoning_content=reasoning_content,
            # reasoning_content_check=reasoning_content_check if global_config.enable_kuuki_read else ""
        )

        return content

    # def _save_to_db(self, message: Message, sender_name: str, prompt: str, prompt_check: str,
    #                 content: str, content_check: str, reasoning_content: str, reasoning_content_check: str):
    def _save_to_db(
        self,
        message: MessageRecv,
        sender_name: str,
        prompt: str,
        prompt_check: str,
        content: str,
        reasoning_content: str,
    ):
        """保存对话记录到数据库"""
        db.reasoning_logs.insert_one(
            {
                "time": time.time(),
                "chat_id": message.chat_stream.stream_id,
                "user": sender_name,
                "message": message.processed_plain_text,
                "model": self.current_model_name,
                # 'reasoning_check': reasoning_content_check,
                # 'response_check': content_check,
                "reasoning": reasoning_content,
                "response": content,
                "prompt": prompt,
                "prompt_check": prompt_check,
            }
        )

    async def _get_emotion_tags(self, content: str, processed_plain_text: str):
        """提取情感标签，结合立场和情绪"""
        try:
            # 构建提示词，结合回复内容、被回复的内容以及立场分析
            prompt = f"""
            请根据以下对话内容，完成以下任务：
            1. 判断回复者的立场是"supportive"（支持）、"opposed"（反对）还是"neutrality"（中立）。
            2. 从"happy,angry,sad,surprised,disgusted,fearful,neutral"中选出最匹配的1个情感标签。
            3. 按照"立场-情绪"的格式输出结果，例如："supportive-happy"。

            被回复的内容：
            {processed_plain_text}

            回复内容：
            {content}

            请分析回复者的立场和情感倾向，并输出结果：
            """

            # 调用模型生成结果
            result, _, _ = await self.model_sum.generate_response(prompt)
            result = result.strip()

            # 解析模型输出的结果
            if "-" in result:
                stance, emotion = result.split("-", 1)
                valid_stances = ["supportive", "opposed", "neutrality"]
                valid_emotions = ["happy", "angry", "sad", "surprised", "disgusted", "fearful", "neutral"]
                if stance in valid_stances and emotion in valid_emotions:
                    return stance, emotion  # 返回有效的立场-情绪组合
                else:
                    return "neutrality", "neutral"  # 默认返回中立-中性
            else:
                return "neutrality", "neutral"  # 格式错误时返回默认值

        except Exception as e:
            print(f"获取情感标签时出错: {e}")
            return "neutrality", "neutral"  # 出错时返回默认值

    async def _process_response(self, content: str) -> Tuple[List[str], List[str]]:
        """处理响应内容，返回处理后的内容和情感标签"""
        if not content:
            return None, []

        processed_response = process_llm_response(content)

        # print(f"得到了处理后的llm返回{processed_response}")

        return processed_response


class InitiativeMessageGenerate:
    def __init__(self):
        self.model_r1 = LLM_request(model=global_config.llm_reasoning, temperature=0.7)
        self.model_v3 = LLM_request(model=global_config.llm_normal, temperature=0.7)
        self.model_r1_distill = LLM_request(model=global_config.llm_reasoning_minor, temperature=0.7)

    def gen_response(self, message: Message):
        topic_select_prompt, dots_for_select, prompt_template = prompt_builder._build_initiative_prompt_select(
            message.group_id
        )
        content_select, reasoning, _ = self.model_v3.generate_response(topic_select_prompt)
        logger.debug(f"{content_select} {reasoning}")
        topics_list = [dot[0] for dot in dots_for_select]
        if content_select:
            if content_select in topics_list:
                select_dot = dots_for_select[topics_list.index(content_select)]
            else:
                return None
        else:
            return None
        prompt_check, memory = prompt_builder._build_initiative_prompt_check(select_dot[1], prompt_template)
        content_check, reasoning_check, _ = self.model_v3.generate_response(prompt_check)
        logger.info(f"{content_check} {reasoning_check}")
        if "yes" not in content_check.lower():
            return None
        prompt = prompt_builder._build_initiative_prompt(select_dot, prompt_template, memory)
        content, reasoning = self.model_r1.generate_response_async(prompt)
        logger.debug(f"[DEBUG] {content} {reasoning}")
        return content
