import math
import random
import time

from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_by_timestamp_with_chat_inclusive
from src.llm_models.utils_model import LLMRequest
from src.manager.async_task_manager import AsyncTask, async_task_manager


logger = get_logger("mood")


def init_prompt():
    Prompt(
        """
{chat_talking_prompt}
以上是群里正在进行的聊天记录

{identity_block}
你先前的情绪状态是：{mood_state}
你的情绪特点是:{emotion_style}

现在，请你根据先前的情绪状态和现在的聊天内容，总结推断你现在的情绪状态
请只输出新的情绪状态，不要输出其他内容：
""",
        "get_mood_prompt",
    )

    Prompt(
        """
{chat_talking_prompt}
以上是群里最近的聊天记录

{identity_block}
你之前的情绪状态是：{mood_state}

距离你上次关注群里消息已经过去了一段时间，你冷静了下来，请你输出一句话描述你现在的情绪状态
你的情绪特点是:{emotion_style}
请只输出新的情绪状态，不要输出其他内容：
""",
        "regress_mood_prompt",
    )


class ChatMood:
    def __init__(self, chat_id: str):
        self.chat_id: str = chat_id

        chat_manager = get_chat_manager()
        self.chat_stream = chat_manager.get_stream(self.chat_id)

        if not self.chat_stream:
            raise ValueError(f"Chat stream for chat_id {chat_id} not found")

        self.log_prefix = f"[{self.chat_stream.group_info.group_name if self.chat_stream.group_info else self.chat_stream.user_info.user_nickname}]"

        self.mood_state: str = "感觉很平静"

        self.regression_count: int = 0

        self.mood_model = LLMRequest(model_set=model_config.model_task_config.utils, request_type="mood")

        self.last_change_time: float = 0

    async def get_mood(self) -> str:
        self.regression_count = 0

        current_time = time.time()

        logger.info(f"{self.log_prefix} 获取情绪状态")
        message_list_before_now = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_change_time,
            timestamp_end=current_time,
            limit=int(global_config.chat.max_context_size / 3),
            limit_mode="last",
        )

        chat_talking_prompt = build_readable_messages(
            message_list_before_now,
            replace_bot_name=True,
            timestamp_mode="normal_no_YMD",
            read_mark=0.0,
            truncate=True,
            show_actions=True,
        )

        bot_name = global_config.bot.nickname
        if global_config.bot.alias_names:
            bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
        else:
            bot_nickname = ""

        identity_block = f"你的名字是{bot_name}{bot_nickname}"

        prompt = await global_prompt_manager.format_prompt(
            "get_mood_prompt",
            chat_talking_prompt=chat_talking_prompt,
            identity_block=identity_block,
            mood_state=self.mood_state,
            emotion_style=global_config.mood.emotion_style,
        )

        response, (reasoning_content, _, _) = await self.mood_model.generate_response_async(
            prompt=prompt, temperature=0.7
        )
        if global_config.debug.show_prompt:
            logger.info(f"{self.log_prefix} prompt: {prompt}")
            logger.info(f"{self.log_prefix} response: {response}")
            logger.info(f"{self.log_prefix} reasoning_content: {reasoning_content}")

        logger.info(f"{self.log_prefix} 情绪状态更新为: {response}")

        self.mood_state = response

        self.last_change_time = current_time

        return response

    async def regress_mood(self):
        message_time = time.time()
        message_list_before_now = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_change_time,
            timestamp_end=message_time,
            limit=15,
            limit_mode="last",
        )

        chat_talking_prompt = build_readable_messages(
            message_list_before_now,
            replace_bot_name=True,
            timestamp_mode="normal_no_YMD",
            read_mark=0.0,
            truncate=True,
            show_actions=True,
        )

        bot_name = global_config.bot.nickname
        if global_config.bot.alias_names:
            bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
        else:
            bot_nickname = ""

        identity_block = f"你的名字是{bot_name}{bot_nickname}"

        prompt = await global_prompt_manager.format_prompt(
            "regress_mood_prompt",
            chat_talking_prompt=chat_talking_prompt,
            identity_block=identity_block,
            mood_state=self.mood_state,
            emotion_style=global_config.mood.emotion_style,
        )

        response, (reasoning_content, _, _) = await self.mood_model.generate_response_async(
            prompt=prompt, temperature=0.7
        )

        if global_config.debug.show_prompt:
            logger.info(f"{self.log_prefix} prompt: {prompt}")
            logger.info(f"{self.log_prefix} response: {response}")
            logger.info(f"{self.log_prefix} reasoning_content: {reasoning_content}")

        logger.info(f"{self.log_prefix} 情绪状态转变为: {response}")

        self.mood_state = response

        self.regression_count += 1


class MoodRegressionTask(AsyncTask):
    def __init__(self, mood_manager: "MoodManager"):
        super().__init__(task_name="MoodRegressionTask", run_interval=45)
        self.mood_manager = mood_manager

    async def run(self):
        logger.debug("开始情绪回归任务...")
        now = time.time()
        for mood in self.mood_manager.mood_list:
            if mood.last_change_time == 0:
                continue

            if now - mood.last_change_time > 200:
                if mood.regression_count >= 2:
                    continue

                logger.debug(f"{mood.log_prefix} 开始情绪回归, 第 {mood.regression_count + 1} 次")
                await mood.regress_mood()


class MoodManager:
    def __init__(self):
        self.mood_list: list[ChatMood] = []
        """当前情绪状态"""
        self.task_started: bool = False

    async def start(self):
        """启动情绪回归后台任务"""
        if self.task_started:
            return

        task = MoodRegressionTask(self)
        await async_task_manager.add_task(task)
        self.task_started = True
        logger.info("情绪回归任务已启动")

    def get_mood_by_chat_id(self, chat_id: str) -> ChatMood:
        for mood in self.mood_list:
            if mood.chat_id == chat_id:
                return mood

        new_mood = ChatMood(chat_id)
        self.mood_list.append(new_mood)
        return new_mood

    def reset_mood_by_chat_id(self, chat_id: str):
        for mood in self.mood_list:
            if mood.chat_id == chat_id:
                mood.mood_state = "感觉很平静"
                mood.regression_count = 0
                return
        self.mood_list.append(ChatMood(chat_id))


init_prompt()

mood_manager = MoodManager()
"""全局情绪管理器"""
