import json
import time

from json_repair import repair_json
from src.chat.message_receive.message import MessageRecv
from src.llm_models.utils_model import LLMRequest
from src.common.logger import get_logger
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_by_timestamp_with_chat_inclusive
from src.config.config import global_config, model_config
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.manager.async_task_manager import AsyncTask, async_task_manager
from src.plugin_system.apis import send_api

from src.mais4u.s4u_config import s4u_config

logger = get_logger("action")

HEAD_CODE = {
    "看向上方": "(0,0.5,0)",
    "看向下方": "(0,-0.5,0)",
    "看向左边": "(-1,0,0)",
    "看向右边": "(1,0,0)",
    "随意朝向": "random",
    "看向摄像机": "camera",
    "注视对方": "(0,0,0)",
    "看向正前方": "(0,0,0)",
}

BODY_CODE = {
    "双手背后向前弯腰": "010_0070",
    "歪头双手合十": "010_0100",
    "标准文静站立": "010_0101",
    "双手交叠腹部站立": "010_0150",
    "帅气的姿势": "010_0190",
    "另一个帅气的姿势": "010_0191",
    "手掌朝前可爱": "010_0210",
    "平静，双手后放": "平静，双手后放",
    "思考": "思考",
    "优雅，左手放在腰上": "优雅，左手放在腰上",
    "一般": "一般",
    "可爱，双手前放": "可爱，双手前放",
}


def init_prompt():
    Prompt(
        """
{chat_talking_prompt}
以上是群里正在进行的聊天记录

{indentify_block}
你现在的动作状态是：
- 身体动作：{body_action}

现在，因为你发送了消息，或者群里其他人发送了消息，引起了你的注意，你对其进行了阅读和思考，请你更新你的动作状态。
身体动作可选：
{all_actions}

请只按照以下json格式输出，描述你新的动作状态，确保每个字段都存在：
{{
  "body_action": "..."
}}
""",
        "change_action_prompt",
    )
    Prompt(
        """
{chat_talking_prompt}
以上是群里最近的聊天记录

{indentify_block}
你之前的动作状态是
- 身体动作：{body_action}

身体动作可选：
{all_actions}

距离你上次关注群里消息已经过去了一段时间，你冷静了下来，你的动作会趋于平缓或静止，请你输出你现在新的动作状态，用中文。
请只按照以下json格式输出，描述你新的动作状态，确保每个字段都存在：
{{
  "body_action": "..."
}}
""",
        "regress_action_prompt",
    )


class ChatAction:
    def __init__(self, chat_id: str):
        self.chat_id: str = chat_id
        self.body_action: str = "一般"
        self.head_action: str = "注视摄像机"

        self.regression_count: int = 0
        # 新增：body_action冷却池，key为动作名，value为剩余冷却次数
        self.body_action_cooldown: dict[str, int] = {}

        print(s4u_config.models.motion)
        print(model_config.model_task_config.emotion)

        self.action_model = LLMRequest(model_set=model_config.model_task_config.emotion, request_type="motion")

        self.last_change_time: float = 0

    async def send_action_update(self):
        """发送动作更新到前端"""

        body_code = BODY_CODE.get(self.body_action, "")
        await send_api.custom_to_stream(
            message_type="body_action",
            content=body_code,
            stream_id=self.chat_id,
            storage_message=False,
            show_log=True,
        )

    async def update_action_by_message(self, message: MessageRecv):
        self.regression_count = 0

        message_time: float = message.message_info.time  # type: ignore
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

        try:
            # 冷却池处理：过滤掉冷却中的动作
            self._update_body_action_cooldown()
            available_actions = [k for k in BODY_CODE.keys() if k not in self.body_action_cooldown]
            all_actions = "\n".join(available_actions)

            prompt = await global_prompt_manager.format_prompt(
                "change_action_prompt",
                chat_talking_prompt=chat_talking_prompt,
                indentify_block=indentify_block,
                body_action=self.body_action,
                all_actions=all_actions,
            )

            logger.info(f"prompt: {prompt}")
            response, (reasoning_content, _, _) = await self.action_model.generate_response_async(
                prompt=prompt, temperature=0.7
            )
            logger.info(f"response: {response}")
            logger.info(f"reasoning_content: {reasoning_content}")

            if action_data := json.loads(repair_json(response)):
                # 记录原动作，切换后进入冷却
                prev_body_action = self.body_action
                new_body_action = action_data.get("body_action", self.body_action)
                if new_body_action != prev_body_action and prev_body_action:
                    self.body_action_cooldown[prev_body_action] = 3
                self.body_action = new_body_action
                self.head_action = action_data.get("head_action", self.head_action)
                # 发送动作更新
                await self.send_action_update()

            self.last_change_time = message_time
        except Exception as e:
            logger.error(f"update_action_by_message error: {e}")

    async def regress_action(self):
        message_time = time.time()
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
        try:
            # 冷却池处理：过滤掉冷却中的动作
            self._update_body_action_cooldown()
            available_actions = [k for k in BODY_CODE.keys() if k not in self.body_action_cooldown]
            all_actions = "\n".join(available_actions)

            prompt = await global_prompt_manager.format_prompt(
                "regress_action_prompt",
                chat_talking_prompt=chat_talking_prompt,
                indentify_block=indentify_block,
                body_action=self.body_action,
                all_actions=all_actions,
            )

            logger.info(f"prompt: {prompt}")
            response, (reasoning_content, _, _) = await self.action_model.generate_response_async(
                prompt=prompt, temperature=0.7
            )
            logger.info(f"response: {response}")
            logger.info(f"reasoning_content: {reasoning_content}")

            if action_data := json.loads(repair_json(response)):
                prev_body_action = self.body_action
                new_body_action = action_data.get("body_action", self.body_action)
                if new_body_action != prev_body_action and prev_body_action:
                    self.body_action_cooldown[prev_body_action] = 6
                self.body_action = new_body_action
                # 发送动作更新
                await self.send_action_update()

            self.regression_count += 1
            self.last_change_time = message_time
        except Exception as e:
            logger.error(f"regress_action error: {e}")

    # 新增：冷却池维护方法
    def _update_body_action_cooldown(self):
        remove_keys = []
        for k in self.body_action_cooldown:
            self.body_action_cooldown[k] -= 1
            if self.body_action_cooldown[k] <= 0:
                remove_keys.append(k)
        for k in remove_keys:
            del self.body_action_cooldown[k]


class ActionRegressionTask(AsyncTask):
    def __init__(self, action_manager: "ActionManager"):
        super().__init__(task_name="ActionRegressionTask", run_interval=3)
        self.action_manager = action_manager

    async def run(self):
        logger.debug("Running action regression task...")
        now = time.time()
        for action_state in self.action_manager.action_state_list:
            if action_state.last_change_time == 0:
                continue

            if now - action_state.last_change_time > 10:
                if action_state.regression_count >= 3:
                    continue

                logger.info(f"chat {action_state.chat_id} 开始动作回归, 这是第 {action_state.regression_count + 1} 次")
                await action_state.regress_action()


class ActionManager:
    def __init__(self):
        self.action_state_list: list[ChatAction] = []
        """当前动作状态"""
        self.task_started: bool = False

    async def start(self):
        """启动动作回归后台任务"""
        if self.task_started:
            return

        logger.info("启动动作回归任务...")
        task = ActionRegressionTask(self)
        await async_task_manager.add_task(task)
        self.task_started = True
        logger.info("动作回归任务已启动")

    def get_action_state_by_chat_id(self, chat_id: str) -> ChatAction:
        for action_state in self.action_state_list:
            if action_state.chat_id == chat_id:
                return action_state

        new_action_state = ChatAction(chat_id)
        self.action_state_list.append(new_action_state)
        return new_action_state


init_prompt()

action_manager = ActionManager()
"""全局动作管理器"""
