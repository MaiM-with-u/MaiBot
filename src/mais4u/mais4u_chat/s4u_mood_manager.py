import asyncio
import json
import time

from src.chat.message_receive.message import MessageRecv
from src.llm_models.utils_model import LLMRequest
from src.common.logger import get_logger
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_by_timestamp_with_chat_inclusive
from src.config.config import global_config, model_config
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.manager.async_task_manager import AsyncTask, async_task_manager
from src.plugin_system.apis import send_api
from src.mais4u.constant_s4u import ENABLE_S4U

"""
情绪管理系统使用说明：

1. 情绪数值系统：
   - 情绪包含四个维度：joy(喜), anger(怒), sorrow(哀), fear(惧)
   - 每个维度的取值范围为1-10
   - 当情绪发生变化时，会自动发送到ws端处理

2. 情绪更新机制：
   - 接收到新消息时会更新情绪状态
   - 定期进行情绪回归（冷静下来）
   - 每次情绪变化都会发送到ws端，格式为：
     type: "emotion"
     data: {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}

3. ws端处理：
   - 本地只负责情绪计算和发送情绪数值
   - 表情渲染和动作由ws端根据情绪数值处理
"""

logger = get_logger("mood")


def init_prompt():
    Prompt(
        """
{chat_talking_prompt}
以上是直播间里正在进行的对话

{indentify_block}
你刚刚的情绪状态是：{mood_state}

现在，发送了消息，引起了你的注意，你对其进行了阅读和思考，请你输出一句话描述你新的情绪状态，不要输出任何其他内容
请只输出情绪状态，不要输出其他内容：
""",
        "change_mood_prompt_vtb",
    )
    Prompt(
        """
{chat_talking_prompt}
以上是直播间里最近的对话

{indentify_block}
你之前的情绪状态是：{mood_state}

距离你上次关注直播间消息已经过去了一段时间，你冷静了下来，请你输出一句话描述你现在的情绪状态
请只输出情绪状态，不要输出其他内容：
""",
        "regress_mood_prompt_vtb",
    )
    Prompt(
        """
{chat_talking_prompt}
以上是直播间里正在进行的对话

{indentify_block}
你刚刚的情绪状态是：{mood_state}
具体来说，从1-10分，你的情绪状态是：
喜(Joy): {joy}
怒(Anger): {anger}
哀(Sorrow): {sorrow}
惧(Fear): {fear}

现在，发送了消息，引起了你的注意，你对其进行了阅读和思考。请基于对话内容，评估你新的情绪状态。
请以JSON格式输出你新的情绪状态，包含"喜怒哀惧"四个维度，每个维度的取值范围为1-10。
键值请使用英文: "joy", "anger", "sorrow", "fear".
例如: {{"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}}
不要输出任何其他内容，只输出JSON。
""",
        "change_mood_numerical_prompt",
    )
    Prompt(
        """
{chat_talking_prompt}
以上是直播间里最近的对话

{indentify_block}
你之前的情绪状态是：{mood_state}
具体来说，从1-10分，你的情绪状态是：
喜(Joy): {joy}
怒(Anger): {anger}
哀(Sorrow): {sorrow}
惧(Fear): {fear}

距离你上次关注直播间消息已经过去了一段时间，你冷静了下来。请基于此，评估你现在的情绪状态。
请以JSON格式输出你新的情绪状态，包含"喜怒哀惧"四个维度，每个维度的取值范围为1-10。
键值请使用英文: "joy", "anger", "sorrow", "fear".
例如: {{"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}}
不要输出任何其他内容，只输出JSON。
""",
        "regress_mood_numerical_prompt",
    )


class ChatMood:
    def __init__(self, chat_id: str):
        self.chat_id: str = chat_id
        self.mood_state: str = "感觉很平静"
        self.mood_values: dict[str, int] = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}

        self.regression_count: int = 0

        self.mood_model = LLMRequest(model_set=model_config.model_task_config.emotion, request_type="mood_text")
        self.mood_model_numerical = LLMRequest(
            model_set=model_config.model_task_config.emotion, request_type="mood_numerical"
        )

        self.last_change_time: float = 0

        # 发送初始情绪状态到ws端
        asyncio.create_task(self.send_emotion_update(self.mood_values))

    def _parse_numerical_mood(self, response: str) -> dict[str, int] | None:
        try:
            # The LLM might output markdown with json inside
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]

            data = json.loads(response)

            # Validate
            required_keys = {"joy", "anger", "sorrow", "fear"}
            if not required_keys.issubset(data.keys()):
                logger.warning(f"Numerical mood response missing keys: {response}")
                return None

            for key in required_keys:
                value = data[key]
                if not isinstance(value, int) or not (1 <= value <= 10):
                    logger.warning(f"Numerical mood response invalid value for {key}: {value} in {response}")
                    return None

            return {key: data[key] for key in required_keys}

        except json.JSONDecodeError:
            logger.warning(f"Failed to parse numerical mood JSON: {response}")
            return None
        except Exception as e:
            logger.error(f"Error parsing numerical mood: {e}, response: {response}")
            return None

    async def update_mood_by_message(self, message: MessageRecv):
        self.regression_count = 0

        message_time: float = message.message_info.time  # type: ignore
        message_list_before_now = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_change_time,
            timestamp_end=message_time,
            limit=10,
            limit_mode="last",
        )
        chat_talking_prompt = build_readable_messages(
            message_list_before_now,
            replace_bot_name=True,
            merge_messages=False,
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

        prompt_personality = global_config.personality.personality_core
        indentify_block = f"你的名字是{bot_name}{bot_nickname}，你{prompt_personality}："

        async def _update_text_mood():
            prompt = await global_prompt_manager.format_prompt(
                "change_mood_prompt_vtb",
                chat_talking_prompt=chat_talking_prompt,
                indentify_block=indentify_block,
                mood_state=self.mood_state,
            )
            logger.debug(f"text mood prompt: {prompt}")
            response, (reasoning_content, _, _) = await self.mood_model.generate_response_async(
                prompt=prompt, temperature=0.7
            )
            logger.info(f"text mood response: {response}")
            logger.debug(f"text mood reasoning_content: {reasoning_content}")
            return response

        async def _update_numerical_mood():
            prompt = await global_prompt_manager.format_prompt(
                "change_mood_numerical_prompt",
                chat_talking_prompt=chat_talking_prompt,
                indentify_block=indentify_block,
                mood_state=self.mood_state,
                joy=self.mood_values["joy"],
                anger=self.mood_values["anger"],
                sorrow=self.mood_values["sorrow"],
                fear=self.mood_values["fear"],
            )
            logger.debug(f"numerical mood prompt: {prompt}")
            response, (reasoning_content, _, _) = await self.mood_model_numerical.generate_response_async(
                prompt=prompt, temperature=0.4
            )
            logger.info(f"numerical mood response: {response}")
            logger.debug(f"numerical mood reasoning_content: {reasoning_content}")
            return self._parse_numerical_mood(response)

        results = await asyncio.gather(_update_text_mood(), _update_numerical_mood())
        text_mood_response, numerical_mood_response = results

        if text_mood_response:
            self.mood_state = text_mood_response

        if numerical_mood_response:
            _old_mood_values = self.mood_values.copy()
            self.mood_values = numerical_mood_response

            # 发送情绪更新到ws端
            await self.send_emotion_update(self.mood_values)

            logger.info(f"[{self.chat_id}] 情绪变化: {_old_mood_values} -> {self.mood_values}")

        self.last_change_time = message_time

    async def regress_mood(self):
        message_time = time.time()
        message_list_before_now = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_change_time,
            timestamp_end=message_time,
            limit=5,
            limit_mode="last",
        )
        chat_talking_prompt = build_readable_messages(
            message_list_before_now,
            replace_bot_name=True,
            merge_messages=False,
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

        prompt_personality = global_config.personality.personality_core
        indentify_block = f"你的名字是{bot_name}{bot_nickname}，你{prompt_personality}："

        async def _regress_text_mood():
            prompt = await global_prompt_manager.format_prompt(
                "regress_mood_prompt_vtb",
                chat_talking_prompt=chat_talking_prompt,
                indentify_block=indentify_block,
                mood_state=self.mood_state,
            )
            logger.debug(f"text regress prompt: {prompt}")
            response, (reasoning_content, _, _) = await self.mood_model.generate_response_async(
                prompt=prompt, temperature=0.7
            )
            logger.info(f"text regress response: {response}")
            logger.debug(f"text regress reasoning_content: {reasoning_content}")
            return response

        async def _regress_numerical_mood():
            prompt = await global_prompt_manager.format_prompt(
                "regress_mood_numerical_prompt",
                chat_talking_prompt=chat_talking_prompt,
                indentify_block=indentify_block,
                mood_state=self.mood_state,
                joy=self.mood_values["joy"],
                anger=self.mood_values["anger"],
                sorrow=self.mood_values["sorrow"],
                fear=self.mood_values["fear"],
            )
            logger.debug(f"numerical regress prompt: {prompt}")
            response, (reasoning_content, _, _) = await self.mood_model_numerical.generate_response_async(
                prompt=prompt,
                temperature=0.4,
            )
            logger.info(f"numerical regress response: {response}")
            logger.debug(f"numerical regress reasoning_content: {reasoning_content}")
            return self._parse_numerical_mood(response)

        results = await asyncio.gather(_regress_text_mood(), _regress_numerical_mood())
        text_mood_response, numerical_mood_response = results

        if text_mood_response:
            self.mood_state = text_mood_response

        if numerical_mood_response:
            _old_mood_values = self.mood_values.copy()
            self.mood_values = numerical_mood_response

            # 发送情绪更新到ws端
            await self.send_emotion_update(self.mood_values)

            logger.info(f"[{self.chat_id}] 情绪回归: {_old_mood_values} -> {self.mood_values}")

        self.regression_count += 1

    async def send_emotion_update(self, mood_values: dict[str, int]):
        """发送情绪更新到ws端"""
        emotion_data = {
            "joy": mood_values.get("joy", 5),
            "anger": mood_values.get("anger", 1),
            "sorrow": mood_values.get("sorrow", 1),
            "fear": mood_values.get("fear", 1),
        }

        await send_api.custom_to_stream(
            message_type="emotion",
            content=emotion_data,
            stream_id=self.chat_id,
            storage_message=False,
            show_log=True,
        )

        logger.info(f"[{self.chat_id}] 发送情绪更新: {emotion_data}")


class MoodRegressionTask(AsyncTask):
    def __init__(self, mood_manager: "MoodManager"):
        super().__init__(task_name="MoodRegressionTask", run_interval=30)
        self.mood_manager = mood_manager
        self.run_count = 0

    async def run(self):
        self.run_count += 1
        logger.info(f"[回归任务] 第{self.run_count}次检查，当前管理{len(self.mood_manager.mood_list)}个聊天的情绪状态")

        now = time.time()
        regression_executed = 0

        for mood in self.mood_manager.mood_list:
            chat_info = f"chat {mood.chat_id}"

            if mood.last_change_time == 0:
                logger.debug(f"[回归任务] {chat_info} 尚未有情绪变化，跳过回归")
                continue

            time_since_last_change = now - mood.last_change_time

            # 检查是否有极端情绪需要快速回归
            high_emotions = {k: v for k, v in mood.mood_values.items() if v >= 8}
            has_extreme_emotion = len(high_emotions) > 0

            # 回归条件：1. 正常时间间隔(120s) 或 2. 有极端情绪且距上次变化>=30s
            should_regress = False
            regress_reason = ""

            if time_since_last_change > 120:
                should_regress = True
                regress_reason = f"常规回归(距上次变化{int(time_since_last_change)}秒)"
            elif has_extreme_emotion and time_since_last_change > 30:
                should_regress = True
                high_emotion_str = ", ".join([f"{k}={v}" for k, v in high_emotions.items()])
                regress_reason = f"极端情绪快速回归({high_emotion_str}, 距上次变化{int(time_since_last_change)}秒)"

            if should_regress:
                if mood.regression_count >= 3:
                    logger.debug(f"[回归任务] {chat_info} 已达到最大回归次数(3次)，停止回归")
                    continue

                logger.info(
                    f"[回归任务] {chat_info} 开始情绪回归 ({regress_reason}，第{mood.regression_count + 1}次回归)"
                )
                await mood.regress_mood()
                regression_executed += 1
            else:
                if has_extreme_emotion:
                    remaining_time = 5 - time_since_last_change
                    high_emotion_str = ", ".join([f"{k}={v}" for k, v in high_emotions.items()])
                    logger.debug(
                        f"[回归任务] {chat_info} 存在极端情绪({high_emotion_str})，距离快速回归还需等待{int(remaining_time)}秒"
                    )
                else:
                    remaining_time = 120 - time_since_last_change
                    logger.debug(f"[回归任务] {chat_info} 距离回归还需等待{int(remaining_time)}秒")

        if regression_executed > 0:
            logger.info(f"[回归任务] 本次执行了{regression_executed}个聊天的情绪回归")
        else:
            logger.debug("[回归任务] 本次没有符合回归条件的聊天")


class MoodManager:
    def __init__(self):
        self.mood_list: list[ChatMood] = []
        """当前情绪状态"""
        self.task_started: bool = False

    async def start(self):
        """启动情绪回归后台任务"""
        if self.task_started:
            return

        logger.info("启动情绪管理任务...")

        # 启动情绪回归任务
        regression_task = MoodRegressionTask(self)
        await async_task_manager.add_task(regression_task)

        self.task_started = True
        logger.info("情绪管理任务已启动（情绪回归）")

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
                mood.mood_values = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
                mood.regression_count = 0
                # 发送重置后的情绪状态到ws端
                asyncio.create_task(mood.send_emotion_update(mood.mood_values))
                return

        # 如果没有找到现有的mood，创建新的
        new_mood = ChatMood(chat_id)
        self.mood_list.append(new_mood)
        # 发送初始情绪状态到ws端
        asyncio.create_task(new_mood.send_emotion_update(new_mood.mood_values))


if ENABLE_S4U:
    init_prompt()
    mood_manager = MoodManager()
else:
    mood_manager = None

"""全局情绪管理器"""
