from .sub_heartflow import SubHeartflow
from .observation import ChattingObservation
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.plugins.schedule.schedule_generator import bot_schedule
from src.plugins.utils.prompt_builder import Prompt, global_prompt_manager
import asyncio
from src.common.logger import get_module_logger, LogConfig, HEARTFLOW_STYLE_CONFIG  # noqa: E402
from src.individuality.individuality import Individuality
import time
import random
from typing import Dict, Any

heartflow_config = LogConfig(
    # 使用海马体专用样式
    console_format=HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=HEARTFLOW_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("heartflow", config=heartflow_config)


def init_prompt():
    prompt = ""
    prompt += "你刚刚在做的事情是：{schedule_info}\n"
    prompt += "{personality_info}\n"
    prompt += "你想起来{related_memory_info}。"
    prompt += "刚刚你的主要想法是{current_thinking_info}。"
    prompt += "你还有一些小想法，因为你在参加不同的群聊天，这是你正在做的事情：{sub_flows_info}\n"
    prompt += "你现在{mood_info}。"
    prompt += "现在你接下去继续思考，产生新的想法，但是要基于原有的主要想法，不要分点输出，"
    prompt += "输出连贯的内心独白，不要太长，但是记得结合上述的消息，关注新内容:"
    Prompt(prompt, "thinking_prompt")
    prompt = ""
    prompt += "{personality_info}\n"
    prompt += "现在{bot_name}的想法是：{current_mind}\n"
    prompt += "现在{bot_name}在qq群里进行聊天，聊天的话题如下：{minds_str}\n"
    prompt += "你现在{mood_info}\n"
    prompt += """现在请你总结这些聊天内容，注意关注聊天内容对原有的想法的影响，输出连贯的内心独白
    不要太长，但是记得结合上述的消息，要记得你的人设，关注新内容:"""
    Prompt(prompt, "mind_summary_prompt")


class CurrentState:
    def __init__(self):
        self.current_state_info = ""

        self.mood_manager = MoodManager()
        self.mood = self.mood_manager.get_prompt()

        self.attendance_factor = 0
        self.engagement_factor = 0

    def update_current_state_info(self):
        self.current_state_info = self.mood_manager.get_current_mood()


class Heartflow:
    def __init__(self):
        self.current_mind = "你什么也没想"
        self.past_mind = []
        self.current_state: CurrentState = CurrentState()
        self.llm_model = LLMRequest(
            model=global_config.llm_heartflow, temperature=0.6, max_tokens=1000, request_type="heart_flow"
        )

        self._subheartflows: Dict[Any, SubHeartflow] = {}

    async def _cleanup_inactive_subheartflows(self):
        """定期清理不活跃的子心流"""
        while True:
            current_time = time.time()
            inactive_subheartflows = []

            # 检查所有子心流
            for subheartflow_id, subheartflow in self._subheartflows.items():
                if (
                    current_time - subheartflow.last_active_time > global_config.sub_heart_flow_stop_time
                ):  # 10分钟 = 600秒
                    inactive_subheartflows.append(subheartflow_id)
                    logger.info(f"发现不活跃的子心流: {subheartflow_id}")

            # 清理不活跃的子心流
            for subheartflow_id in inactive_subheartflows:
                del self._subheartflows[subheartflow_id]
                logger.info(f"已清理不活跃的子心流: {subheartflow_id}")

            await asyncio.sleep(30)  # 每分钟检查一次

    async def _sub_heartflow_update(self):
        while True:
            # 检查是否存在子心流
            if not self._subheartflows:
                # logger.info("当前没有子心流，等待新的子心流创建...")
                await asyncio.sleep(30)  # 每分钟检查一次是否有新的子心流
                continue

            await self.do_a_thinking()
            await asyncio.sleep(global_config.heart_flow_update_interval)  # 5分钟思考一次

    async def heartflow_start_working(self):
        # 启动清理任务
        asyncio.create_task(self._cleanup_inactive_subheartflows())

        # 启动子心流更新任务
        asyncio.create_task(self._sub_heartflow_update())

    @staticmethod
    async def _update_current_state():
        print("TODO")

    async def do_a_thinking(self):
        logger.debug("麦麦大脑袋转起来了")
        self.current_state.update_current_state_info()

        # 开始构建prompt
        prompt_personality = "你"
        # person
        individuality = Individuality.get_instance()

        personality_core = individuality.personality.personality_core
        prompt_personality += personality_core

        personality_sides = individuality.personality.personality_sides
        random.shuffle(personality_sides)
        prompt_personality += f",{personality_sides[0]}"

        identity_detail = individuality.identity.identity_detail
        random.shuffle(identity_detail)
        prompt_personality += f",{identity_detail[0]}"

        personality_info = prompt_personality

        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        related_memory_info = "memory"
        try:
            sub_flows_info = await self.get_all_subheartflows_minds()
        except Exception as e:
            logger.error(f"获取子心流的想法失败: {e}")
            return

        schedule_info = bot_schedule.get_current_num_task(num=4, time_info=True)

        # prompt = ""
        # prompt += f"你刚刚在做的事情是：{schedule_info}\n"
        # prompt += f"{personality_info}\n"
        # prompt += f"你想起来{related_memory_info}。"
        # prompt += f"刚刚你的主要想法是{current_thinking_info}。"
        # prompt += f"你还有一些小想法，因为你在参加不同的群聊天，这是你正在做的事情：{sub_flows_info}\n"
        # prompt += f"你现在{mood_info}。"
        # prompt += "现在你接下去继续思考，产生新的想法，但是要基于原有的主要想法，不要分点输出，"
        # prompt += "输出连贯的内心独白，不要太长，但是记得结合上述的消息，关注新内容:"
        prompt = (await global_prompt_manager.get_prompt_async("thinking_prompt")).format(
            schedule_info, personality_info, related_memory_info, current_thinking_info, sub_flows_info, mood_info
        )

        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)
        except Exception as e:
            logger.error(f"内心独白获取失败: {e}")
            return
        self.update_current_mind(response)

        self.current_mind = response
        logger.info(f"麦麦的总体脑内状态：{self.current_mind}")
        # logger.info("麦麦想了想，当前活动:")
        # await bot_schedule.move_doing(self.current_mind)

        for _, subheartflow in self._subheartflows.items():
            subheartflow.main_heartflow_info = response

    def update_current_mind(self, response):
        self.past_mind.append(self.current_mind)
        self.current_mind = response

    async def get_all_subheartflows_minds(self):
        sub_minds = ""
        for _, subheartflow in self._subheartflows.items():
            sub_minds += subheartflow.current_mind

        return await self.minds_summary(sub_minds)

    async def minds_summary(self, minds_str):
        # 开始构建prompt
        prompt_personality = "你"
        # person
        individuality = Individuality.get_instance()

        personality_core = individuality.personality.personality_core
        prompt_personality += personality_core

        personality_sides = individuality.personality.personality_sides
        random.shuffle(personality_sides)
        prompt_personality += f",{personality_sides[0]}"

        identity_detail = individuality.identity.identity_detail
        random.shuffle(identity_detail)
        prompt_personality += f",{identity_detail[0]}"

        personality_info = prompt_personality
        mood_info = self.current_state.mood

        # prompt = ""
        # prompt += f"{personality_info}\n"
        # prompt += f"现在{global_config.BOT_NICKNAME}的想法是：{self.current_mind}\n"
        # prompt += f"现在{global_config.BOT_NICKNAME}在qq群里进行聊天，聊天的话题如下：{minds_str}\n"
        # prompt += f"你现在{mood_info}\n"
        # prompt += """现在请你总结这些聊天内容，注意关注聊天内容对原有的想法的影响，输出连贯的内心独白
        # 不要太长，但是记得结合上述的消息，要记得你的人设，关注新内容:"""
        prompt = (await global_prompt_manager.get_prompt_async("mind_summary_prompt")).format(
            personality_info, global_config.BOT_NICKNAME, self.current_mind, minds_str, mood_info
        )

        response, reasoning_content = await self.llm_model.generate_response_async(prompt)

        return response

    async def create_subheartflow(self, subheartflow_id):
        """
        创建一个新的SubHeartflow实例
        添加一个SubHeartflow实例到self._subheartflows字典中
        并根据subheartflow_id为子心流创建一个观察对象
        """

        try:
            if subheartflow_id not in self._subheartflows:
                subheartflow = SubHeartflow(subheartflow_id)
                # 创建一个观察对象，目前只可以用chat_id创建观察对象
                logger.debug(f"创建 observation: {subheartflow_id}")
                observation = ChattingObservation(subheartflow_id)
                await observation.initialize()
                subheartflow.add_observation(observation)
                logger.debug("添加 observation 成功")
                # 创建异步任务
                asyncio.create_task(subheartflow.subheartflow_start_working())
                logger.debug("创建异步任务 成功")
                self._subheartflows[subheartflow_id] = subheartflow
                logger.info("添加 subheartflow 成功")
            return self._subheartflows[subheartflow_id]
        except Exception as e:
            logger.error(f"创建 subheartflow 失败: {e}")
            return None

    def get_subheartflow(self, observe_chat_id) -> SubHeartflow:
        """获取指定ID的SubHeartflow实例"""
        return self._subheartflows.get(observe_chat_id)

    def get_all_subheartflows_streams_ids(self) -> list[Any]:
        """获取当前所有活跃的子心流的 ID 列表"""
        return list(self._subheartflows.keys())


init_prompt()
# 创建一个全局的管理器实例
heartflow = Heartflow()
