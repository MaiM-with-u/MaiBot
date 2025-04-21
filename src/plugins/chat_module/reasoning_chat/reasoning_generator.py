from typing import List, Optional, Tuple, Union
import random

from ...models.utils_model import LLMRequest
from ....config.config import global_config
from ...chat.message import MessageThinking
from .reasoning_prompt_builder import prompt_builder
from ...chat.utils import process_llm_response
from ...utils.timer_calculater import Timer
from src.common.logger import get_module_logger, LogConfig, LLM_STYLE_CONFIG
from src.plugins.respon_info_catcher.info_catcher import info_catcher_manager

# 定义日志配置
llm_config = LogConfig(
    # 使用消息发送专用样式
    console_format=LLM_STYLE_CONFIG["console_format"],
    file_format=LLM_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("llm_generator", config=llm_config)


class ResponseGenerator:
    def __init__(self):
        self.model_reasoning = LLMRequest(
            model=global_config.llm_reasoning,
            temperature=0.7,
            max_tokens=3000,
            request_type="response_reasoning",
        )
        self.model_normal = LLMRequest(
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=256,
            request_type="response_reasoning",
        )

        self.model_sum = LLMRequest(
            model=global_config.llm_summary_by_topic, temperature=0.7, max_tokens=3000, request_type="relation"
        )
        self.current_model_type = "r1"  # 默认使用 R1
        self.current_model_name = "unknown model"

    async def generate_response(self, message: MessageThinking, thinking_id: str) -> Optional[Union[str, List[str]]]:
        """根据当前模型类型选择对应的生成函数"""
        # 从global_config中获取模型概率值并选择模型
        if random.random() < global_config.MODEL_R1_PROBABILITY:
            self.current_model_type = "深深地"
            current_model = self.model_reasoning
        else:
            self.current_model_type = "浅浅的"
            current_model = self.model_normal

        logger.info(
            f"{self.current_model_type}思考:{message.processed_plain_text[:30] + '...' if len(message.processed_plain_text) > 30 else message.processed_plain_text}"
        )  # noqa: E501

        model_response = await self._generate_response_with_model(message, current_model, thinking_id)

        # print(f"raw_content: {model_response}")

        if model_response:
            logger.info(f"{global_config.BOT_NICKNAME}的回复是：{model_response}")
            model_response = await self._process_response(model_response)

            return model_response
        else:
            logger.info(f"{self.current_model_type}思考，失败")
            return None

    async def _generate_response_with_model(self, message: MessageThinking, model: LLMRequest, thinking_id: str):
        info_catcher = info_catcher_manager.get_info_catcher(thinking_id)

        if message.chat_stream.user_info.user_cardname and message.chat_stream.user_info.user_nickname:
            sender_name = (
                f"[({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}]"
                f"{message.chat_stream.user_info.user_cardname}"
            )
        elif message.chat_stream.user_info.user_nickname:
            sender_name = f"({message.chat_stream.user_info.user_id}){message.chat_stream.user_info.user_nickname}"
        else:
            sender_name = f"用户({message.chat_stream.user_info.user_id})"

        logger.debug("开始使用生成回复-2")
        # 构建prompt
        with Timer() as t_build_prompt:
            prompt = await prompt_builder._build_prompt(
                message.chat_stream,
                message_txt=message.processed_plain_text,
                sender_name=sender_name,
                stream_id=message.chat_stream.stream_id,
            )
        logger.info(f"构建prompt时间: {t_build_prompt.human_readable}")

        try:
            content, reasoning_content, self.current_model_name = await model.generate_response(prompt)

            info_catcher.catch_after_llm_generated(
                prompt=prompt, response=content, reasoning_content=reasoning_content, model_name=self.current_model_name
            )

        except Exception:
            logger.exception("生成回复时出错")
            return None

        # 保存到数据库
        # self._save_to_db(
        #     message=message,
        #     sender_name=sender_name,
        #     prompt=prompt,
        #     content=content,
        #     reasoning_content=reasoning_content,
        #     # reasoning_content_check=reasoning_content_check if global_config.enable_kuuki_read else ""
        # )

        return content

    # def _save_to_db(
    #     self,
    #     message: MessageRecv,
    #     sender_name: str,
    #     prompt: str,
    #     content: str,
    #     reasoning_content: str,
    # ):
    #     """保存对话记录到数据库"""
    #     db.reasoning_logs.insert_one(
    #         {
    #             "time": time.time(),
    #             "chat_id": message.chat_stream.stream_id,
    #             "user": sender_name,
    #             "message": message.processed_plain_text,
    #             "model": self.current_model_name,
    #             "reasoning": reasoning_content,
    #             "response": content,
    #             "prompt": prompt,
    #         }
    #     )

    async def _get_emotion_tags(self, content: str, processed_plain_text: str):
        """提取情感标签，结合立场和情绪"""
        try:
            # 构建提示词，结合回复内容、被回复的内容以及立场分析
            prompt = f"""
            请严格根据以下对话内容，完成以下任务：
            1. 判断回复者对被回复者观点的直接立场：
            - "支持"：明确同意或强化被回复者观点
            - "反对"：明确反驳或否定被回复者观点
            - "中立"：不表达明确立场或无关回应
            2. 从"开心,愤怒,悲伤,惊讶,平静,害羞,恐惧,厌恶,困惑"中选出最匹配的1个情感标签
            3. 按照"立场-情绪"的格式直接输出结果，例如："反对-愤怒"
            4. 考虑回复者的人格设定为{global_config.personality_core}

            对话示例：
            被回复：「A就是笨」
            回复：「A明明很聪明」 → 反对-愤怒

            当前对话：
            被回复：「{processed_plain_text}」
            回复：「{content}」

            输出要求：
            - 只需输出"立场-情绪"结果，不要解释
            - 严格基于文字直接表达的对立关系判断
            """

            # 调用模型生成结果
            result, _, _ = await self.model_sum.generate_response(prompt)
            result = result.strip()

            # 解析模型输出的结果
            if "-" in result:
                stance, emotion = result.split("-", 1)
                valid_stances = ["支持", "反对", "中立"]
                valid_emotions = ["开心", "愤怒", "悲伤", "惊讶", "害羞", "平静", "恐惧", "厌恶", "困惑"]
                if stance in valid_stances and emotion in valid_emotions:
                    return stance, emotion  # 返回有效的立场-情绪组合
                else:
                    logger.debug(f"无效立场-情感组合:{result}")
                    return "中立", "平静"  # 默认返回中立-平静
            else:
                logger.debug(f"立场-情感格式错误:{result}")
                return "中立", "平静"  # 格式错误时返回默认值

        except Exception as e:
            logger.debug(f"获取情感标签时出错: {e}")
            return "中立", "平静"  # 出错时返回默认值

    @staticmethod
    async def _process_response(content: str) -> Tuple[List[str], List[str]]:
        """处理响应内容，返回处理后的内容和情感标签"""
        if not content:
            return None, []

        processed_response = process_llm_response(content)

        # print(f"得到了处理后的llm返回{processed_response}")

        return processed_response
