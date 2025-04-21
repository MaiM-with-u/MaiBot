from .observation import Observation, ChattingObservation
import asyncio
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
import time
from typing import Optional
from datetime import datetime
import traceback
from src.plugins.chat.utils import parse_text_timestamps

# from src.plugins.schedule.schedule_generator import bot_schedule
# from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.common.logger import get_module_logger, LogConfig, SUB_HEARTFLOW_STYLE_CONFIG  # noqa: E402

# from src.plugins.chat.utils import get_embedding
# from src.common.database import db
# from typing import Union
from src.individuality.individuality import Individuality
import random
from src.plugins.person_info.relationship_manager import relationship_manager
from ..plugins.utils.prompt_builder import Prompt, global_prompt_manager

subheartflow_config = LogConfig(
    # 使用海马体专用样式
    console_format=SUB_HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=SUB_HEARTFLOW_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("subheartflow", config=subheartflow_config)


def init_prompt():
    prompt = ""
    # prompt += f"麦麦的总体想法是：{self.main_heartflow_info}\n\n"
    prompt += "{extra_info}\n"
    # prompt += "{prompt_schedule}\n"
    # prompt += "{relation_prompt_all}\n"
    prompt += "{prompt_personality}\n"
    prompt += "刚刚你的想法是：\n我是{bot_name}，我想，{current_thinking_info}\n"
    prompt += "-----------------------------------\n"
    prompt += "现在是{time_now}，你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：\n{chat_observe_info}\n"
    prompt += "\n你现在{mood_info}\n"
    # prompt += "你注意到{sender_name}刚刚说：{message_txt}\n"
    prompt += "现在请你根据刚刚的想法继续思考，思考时可以想想如何对群聊内容进行回复，要不要对群里的话题进行回复，关注新话题，可以适当转换话题，大家正在说的话才是聊天的主题。\n"
    prompt += "回复的要求是：平淡一些，简短一些，说中文，如果你要回复，最好只回复一个人的一个话题\n"
    prompt += "请注意不要输出多余内容(包括前后缀，冒号和引号，括号， 表情，等)，不要带有括号和动作描写。不要回复自己的发言，尽量不要说你说过的话。"
    prompt += "现在请你{hf_do_next}，不要分点输出,生成内心想法，文字不要浮夸"

    Prompt(prompt, "sub_heartflow_prompt_before")


class CurrentState:
    def __init__(self):
        self.willing = 0
        self.current_state_info = ""

        self.mood_manager = MoodManager()
        self.mood = self.mood_manager.get_prompt()

    def update_current_state_info(self):
        self.current_state_info = self.mood_manager.get_current_mood()


class SubHeartflow:
    def __init__(self, subheartflow_id):
        self.subheartflow_id = subheartflow_id

        self.current_mind = ""
        self.past_mind = []
        self.current_state: CurrentState = CurrentState()
        self.llm_model = LLMRequest(
            model=global_config.llm_sub_heartflow,
            temperature=global_config.llm_sub_heartflow["temp"],
            max_tokens=800,
            request_type="sub_heart_flow",
        )

        self.main_heartflow_info = ""

        self.last_reply_time = time.time()
        self.last_active_time = time.time()  # 添加最后激活时间

        if not self.current_mind:
            self.current_mind = "你什么也没想"

        self.is_active = False

        self.observations: list[ChattingObservation] = []

        self.running_knowledges = []

        self.bot_name = global_config.BOT_NICKNAME

    async def subheartflow_start_working(self):
        while True:
            current_time = time.time()
            # --- 调整后台任务逻辑 --- #
            # 这个后台循环现在主要负责检查是否需要自我销毁
            # 不再主动进行思考或状态更新，这些由 HeartFC_Chat 驱动

            # 检查是否超过指定时间没有激活 (例如，没有被调用进行思考)
            if current_time - self.last_active_time > global_config.sub_heart_flow_stop_time:  # 例如 5 分钟
                logger.info(
                    f"子心流 {self.subheartflow_id} 超过 {global_config.sub_heart_flow_stop_time} 秒没有激活，正在销毁..."
                    f" (Last active: {datetime.fromtimestamp(self.last_active_time).strftime('%Y-%m-%d %H:%M:%S')})"
                )
                # 在这里添加实际的销毁逻辑，例如从主 Heartflow 管理器中移除自身
                # heartflow.remove_subheartflow(self.subheartflow_id) # 假设有这样的方法
                break  # 退出循环以停止任务

            await asyncio.sleep(global_config.sub_heart_flow_update_interval)  # 定期检查销毁条件

    async def ensure_observed(self):
        """确保在思考前执行了观察"""
        observation = self._get_primary_observation()
        if observation:
            try:
                await observation.observe()
                logger.trace(f"[{self.subheartflow_id}] Observation updated before thinking.")
            except Exception as e:
                logger.error(f"[{self.subheartflow_id}] Error during pre-thinking observation: {e}")
                logger.error(traceback.format_exc())

    async def do_thinking_before_reply(
        self,
        extra_info: str,
        obs_id: list[str] = None,  # 修改 obs_id 类型为 list[str]
    ):
        # --- 在思考前确保观察已执行 --- #
        # await self.ensure_observed()

        self.last_active_time = time.time()  # 更新最后激活时间戳

        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        observation = self._get_primary_observation()

        # --- 获取观察信息 --- #
        chat_observe_info = ""
        if obs_id:
            try:
                chat_observe_info = observation.get_observe_info(obs_id)
                logger.debug(f"[{self.subheartflow_id}] Using specific observation IDs: {obs_id}")
            except Exception as e:
                logger.error(
                    f"[{self.subheartflow_id}] Error getting observe info with IDs {obs_id}: {e}. Falling back."
                )
                chat_observe_info = observation.get_observe_info()  # 出错时回退到默认观察
        else:
            chat_observe_info = observation.get_observe_info()
            logger.debug(f"[{self.subheartflow_id}] Using default observation info.")

        # --- 构建 Prompt (基本逻辑不变) --- #
        extra_info_prompt = ""
        if extra_info:
            for tool_name, tool_data in extra_info.items():
                extra_info_prompt += f"{tool_name} 相关信息:\n"
                for item in tool_data:
                    extra_info_prompt += f"- {item['name']}: {item['content']}\n"
        else:
            extra_info_prompt = "无工具信息。\n"  # 提供默认值

        individuality = Individuality.get_instance()
        prompt_personality = f"你的名字是{self.bot_name}，你"
        prompt_personality += individuality.personality.personality_core

        # 添加随机性格侧面
        if individuality.personality.personality_sides:
            random_side = random.choice(individuality.personality.personality_sides)
            prompt_personality += f"，{random_side}"

        # 添加随机身份细节
        if individuality.identity.identity_detail:
            random_detail = random.choice(individuality.identity.identity_detail)
            prompt_personality += f"，{random_detail}"

        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # 创建局部Random对象避免影响全局随机状态
        local_random = random.Random()
        current_minute = int(time.strftime("%M"))
        local_random.seed(current_minute)  # 用分钟作为种子确保每分钟内选择一致

        hf_options = [
            ("继续生成你在这个聊天中的想法，在原来想法的基础上继续思考", 0.7),
            ("生成你在这个聊天中的想法，在原来的想法上尝试新的话题", 0.1),
            ("生成你在这个聊天中的想法，不要太深入", 0.1),
            ("继续生成你在这个聊天中的想法，进行深入思考", 0.1),
        ]

        hf_do_next = local_random.choices(
            [option[0] for option in hf_options], weights=[option[1] for option in hf_options], k=1
        )[0]

        prompt = (await global_prompt_manager.get_prompt_async("sub_heartflow_prompt_before")).format(
            extra_info=extra_info_prompt,
            # relation_prompt_all=relation_prompt_all,
            prompt_personality=prompt_personality,
            bot_name=self.bot_name,
            current_thinking_info=current_thinking_info,
            time_now=time_now,
            chat_observe_info=chat_observe_info,
            mood_info=mood_info,
            hf_do_next=hf_do_next,
            # sender_name=sender_name_sign,
            # message_txt=message_txt,
        )

        prompt = await relationship_manager.convert_all_person_sign_to_person_name(prompt)
        prompt = parse_text_timestamps(prompt, mode="lite")

        logger.debug(f"[{self.subheartflow_id}] 心流思考prompt:\n{prompt}\n")

        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)

            logger.debug(f"[{self.subheartflow_id}] 心流思考结果:\n{response}\n")

            if not response:  # 如果 LLM 返回空，给一个默认想法
                response = "(不知道该想些什么...)"
                logger.warning(f"[{self.subheartflow_id}] LLM 返回空结果，思考失败。")
        except Exception as e:
            logger.error(f"[{self.subheartflow_id}] 内心独白获取失败: {e}")
            response = "(思考时发生错误...)"  # 错误时的默认想法

        self.update_current_mind(response)

        # self.current_mind 已经在 update_current_mind 中更新

        # logger.info(f"[{self.subheartflow_id}] 思考前脑内状态：{self.current_mind}")
        return self.current_mind, self.past_mind

    def update_current_mind(self, response):
        self.past_mind.append(self.current_mind)
        self.current_mind = response

    def add_observation(self, observation: Observation):
        """添加一个新的observation对象到列表中，如果已存在相同id的observation则不添加"""
        # 查找是否存在相同id的observation
        for existing_obs in self.observations:
            if existing_obs.observe_id == observation.observe_id:
                # 如果找到相同id的observation，直接返回
                return
        # 如果没有找到相同id的observation，则添加新的
        self.observations.append(observation)

    def remove_observation(self, observation: Observation):
        """从列表中移除一个observation对象"""
        if observation in self.observations:
            self.observations.remove(observation)

    def get_all_observations(self) -> list[Observation]:
        """获取所有observation对象"""
        return self.observations

    def clear_observations(self):
        """清空所有observation对象"""
        self.observations.clear()

    def _get_primary_observation(self) -> Optional[ChattingObservation]:
        """获取主要的（通常是第一个）ChattingObservation实例"""
        if self.observations and isinstance(self.observations[0], ChattingObservation):
            return self.observations[0]
        logger.warning(f"SubHeartflow {self.subheartflow_id} 没有找到有效的 ChattingObservation")
        return None


init_prompt()
# subheartflow = SubHeartflow()
