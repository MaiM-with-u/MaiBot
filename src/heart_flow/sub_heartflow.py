from .observation import Observation, ChattingObservation
import asyncio
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
import time
from typing import Optional, List, Dict, Callable
import traceback
from src.plugins.chat.utils import parse_text_timestamps
import enum
from src.common.logger import get_module_logger, LogConfig, SUB_HEARTFLOW_STYLE_CONFIG  # noqa: E402
from src.individuality.individuality import Individuality
import random
from src.plugins.person_info.relationship_manager import relationship_manager
from ..plugins.utils.prompt_builder import Prompt, global_prompt_manager
from src.plugins.chat.message import MessageRecv
from src.plugins.chat.chat_stream import chat_manager
import math
from src.plugins.heartFC_chat.heartFC_chat import HeartFChatting
from src.plugins.heartFC_chat.normal_chat import NormalChat

# from src.do_tool.tool_use import ToolUser
from src.heart_flow.mai_state_manager import MaiStateInfo


# 定义常量 (从 interest.py 移动过来)
MAX_INTEREST = 15.0

subheartflow_config = LogConfig(
    # 使用海马体专用样式
    console_format=SUB_HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=SUB_HEARTFLOW_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("subheartflow", config=subheartflow_config)

interest_log_config = LogConfig(
    console_format=SUB_HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=SUB_HEARTFLOW_STYLE_CONFIG["file_format"],
)
interest_logger = get_module_logger("InterestChatting", config=interest_log_config)


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


class ChatState(enum.Enum):
    ABSENT = "没在看群"
    CHAT = "随便水群"
    FOCUSED = "激情水群"


class ChatStateInfo:
    def __init__(self):
        self.chat_status: ChatState = ChatState.ABSENT
        self.current_state_time = 120

        self.mood_manager = MoodManager()
        self.mood = self.mood_manager.get_prompt()


base_reply_probability = 0.05
probability_increase_rate_per_second = 0.08
max_reply_probability = 1


class InterestChatting:
    def __init__(
        self,
        decay_rate=global_config.default_decay_rate_per_second,
        max_interest=MAX_INTEREST,
        trigger_threshold=global_config.reply_trigger_threshold,
        base_reply_probability=base_reply_probability,
        increase_rate=probability_increase_rate_per_second,
        decay_factor=global_config.probability_decay_factor_per_second,
        max_probability=max_reply_probability,
        state_change_callback: Optional[Callable[[ChatState], None]] = None,
    ):
        self.interest_level: float = 0.0
        self.last_update_time: float = time.time()
        self.decay_rate_per_second: float = decay_rate
        self.max_interest: float = max_interest
        self.last_interaction_time: float = self.last_update_time

        self.trigger_threshold: float = trigger_threshold
        self.base_reply_probability: float = base_reply_probability
        self.probability_increase_rate: float = increase_rate
        self.probability_decay_factor: float = decay_factor
        self.max_reply_probability: float = max_probability
        self.current_reply_probability: float = 0.0
        self.is_above_threshold: bool = False
        self.update_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        self.interest_dict: Dict[str, tuple[MessageRecv, float, bool]] = {}
        self.update_interval = 1.0
        self.start_updates(self.update_interval)  # 初始化时启动后台更新任务

        self.above_threshold = False
        self.start_hfc_probability = 0.0

    def add_interest_dict(self, message: MessageRecv, interest_value: float, is_mentioned: bool):
        self.interest_dict[message.message_info.message_id] = (message, interest_value, is_mentioned)
        self.last_interaction_time = time.time()

    async def _calculate_decay(self):
        """计算兴趣值的衰减

        参数:
            current_time: 当前时间戳

        处理逻辑:
        1. 计算时间差
        2. 处理各种异常情况(负值/零值)
        3. 正常计算衰减
        4. 更新最后更新时间
        """

        # 处理极小兴趣值情况
        if self.interest_level < 1e-9:
            self.interest_level = 0.0
            return

        # 异常情况处理
        if self.decay_rate_per_second <= 0:
            interest_logger.warning(f"衰减率({self.decay_rate_per_second})无效，重置兴趣值为0")
            self.interest_level = 0.0
            return

        # 正常衰减计算
        try:
            decay_factor = math.pow(self.decay_rate_per_second, self.update_interval)
            self.interest_level *= decay_factor
        except ValueError as e:
            interest_logger.error(
                f"衰减计算错误: {e} 参数: 衰减率={self.decay_rate_per_second} 时间差={self.update_interval} 当前兴趣={self.interest_level}"
            )
            self.interest_level = 0.0

    async def _update_reply_probability(self):
        self.above_threshold = self.interest_level >= self.trigger_threshold
        if self.above_threshold:
            self.start_hfc_probability += 0.1
        else:
            if self.start_hfc_probability != 0:
                self.start_hfc_probability -= 0.1

    async def increase_interest(self, current_time: float, value: float):
        self.interest_level += value
        self.interest_level = min(self.interest_level, self.max_interest)

    async def decrease_interest(self, current_time: float, value: float):
        self.interest_level -= value
        self.interest_level = max(self.interest_level, 0.0)

    async def get_interest(self) -> float:
        return self.interest_level

    async def get_state(self) -> dict:
        interest = self.interest_level  # 直接使用属性值
        return {
            "interest_level": round(interest, 2),
            "start_hfc_probability": round(self.start_hfc_probability, 4),
            "is_above_threshold": self.is_above_threshold,
        }

    async def should_evaluate_reply(self) -> bool:
        if self.current_reply_probability > 0:
            trigger = random.random() < self.current_reply_probability
            return trigger
        else:
            return False

    # --- 新增后台更新任务相关方法 ---
    async def _run_update_loop(self, update_interval: float = 1.0):
        """后台循环，定期更新兴趣和回复概率。"""
        while not self._stop_event.is_set():
            try:
                if self.interest_level != 0:
                    await self._calculate_decay()

                await self._update_reply_probability()

                # 等待下一个周期或停止事件
                await asyncio.wait_for(self._stop_event.wait(), timeout=update_interval)
            except asyncio.TimeoutError:
                # 正常超时，继续循环
                continue
            except asyncio.CancelledError:
                interest_logger.info("InterestChatting 更新循环被取消。")
                break
            except Exception as e:
                interest_logger.error(f"InterestChatting 更新循环出错: {e}")
                interest_logger.error(traceback.format_exc())
                # 防止错误导致CPU飙升，稍作等待
                await asyncio.sleep(5)
        interest_logger.info("InterestChatting 更新循环已停止。")

    def start_updates(self, update_interval: float = 1.0):
        """启动后台更新任务"""
        if self.update_task is None or self.update_task.done():
            self._stop_event.clear()
            self.update_task = asyncio.create_task(self._run_update_loop(update_interval))
            interest_logger.debug("后台兴趣更新任务已创建并启动。")
        else:
            interest_logger.debug("后台兴趣更新任务已在运行中。")

    async def stop_updates(self):
        """停止后台更新任务"""
        if self.update_task and not self.update_task.done():
            interest_logger.info("正在停止 InterestChatting 后台更新任务...")
            self._stop_event.set()  # 发送停止信号
            try:
                # 等待任务结束，设置超时
                await asyncio.wait_for(self.update_task, timeout=5.0)
                interest_logger.info("InterestChatting 后台更新任务已成功停止。")
            except asyncio.TimeoutError:
                interest_logger.warning("停止 InterestChatting 后台任务超时，尝试取消...")
                self.update_task.cancel()
                try:
                    await self.update_task  # 等待取消完成
                except asyncio.CancelledError:
                    interest_logger.info("InterestChatting 后台更新任务已被取消。")
            except Exception as e:
                interest_logger.error(f"停止 InterestChatting 后台任务时发生异常: {e}")
            finally:
                self.update_task = None
        else:
            interest_logger.debug("InterestChatting 后台更新任务未运行或已完成。")

    # --- 结束 新增方法 ---


class SubHeartflow:
    def __init__(self, subheartflow_id, mai_states: MaiStateInfo):
        """子心流初始化函数

        Args:
            subheartflow_id: 子心流唯一标识符
            parent_heartflow: 父级心流实例
        """
        # 基础属性
        self.subheartflow_id = subheartflow_id
        self.chat_id = subheartflow_id

        self.mai_states = mai_states

        # 思维状态相关
        self.current_mind = "什么也没想"  # 当前想法
        self.past_mind = []  # 历史想法记录

        # 聊天状态管理
        self.chat_state: ChatStateInfo = ChatStateInfo()  # 该sub_heartflow的聊天状态信息
        self.interest_chatting = InterestChatting(
            state_change_callback=self.set_chat_state
        )  # 该sub_heartflow的兴趣系统

        # 活动状态管理
        self.last_active_time = time.time()  # 最后活跃时间
        self.should_stop = False  # 停止标志
        self.task: Optional[asyncio.Task] = None  # 后台任务
        self.heart_fc_instance: Optional[HeartFChatting] = None  # 该sub_heartflow的HeartFChatting实例
        self.normal_chat_instance: Optional[NormalChat] = None  # 该sub_heartflow的NormalChat实例

        # 观察和知识系统
        self.observations: List[ChattingObservation] = []  # 观察列表
        self.running_knowledges = []  # 运行中的知识

        # LLM模型配置
        self.llm_model = LLMRequest(
            model=global_config.llm_sub_heartflow,
            temperature=global_config.llm_sub_heartflow["temp"],
            max_tokens=800,
            request_type="sub_heart_flow",
        )

        self.log_prefix = chat_manager.get_stream_name(self.subheartflow_id) or self.subheartflow_id

    async def add_time_current_state(self, add_time: float):
        self.current_state_time += add_time

    async def change_to_state_chat(self):
        self.current_state_time = 120
        self._start_normal_chat()

    async def change_to_state_focused(self):
        self.current_state_time = 60
        self._start_heart_fc_chat()

    async def _stop_normal_chat(self):
        """停止 NormalChat 的兴趣监控"""
        if self.normal_chat_instance:
            logger.info(f"{self.log_prefix} 停止 NormalChat 兴趣监控...")
            try:
                await self.normal_chat_instance.stop_chat()  # 调用 stop_chat
            except Exception as e:
                logger.error(f"{self.log_prefix} 停止 NormalChat 监控任务时出错: {e}")
                logger.error(traceback.format_exc())

    async def _start_normal_chat(self) -> bool:
        """启动 NormalChat 实例及其兴趣监控，确保 HeartFChatting 已停止"""
        await self._stop_heart_fc_chat()  # 确保专注聊天已停止

        log_prefix = self.log_prefix
        try:
            # 总是尝试创建或获取最新的 stream 和 interest_dict
            chat_stream = chat_manager.get_stream(self.chat_id)
            if not chat_stream:
                logger.error(f"{log_prefix} 无法获取 chat_stream，无法启动 NormalChat。")
                return False

            # 如果实例不存在或需要更新，则创建新实例
            # if not self.normal_chat_instance: # 或者总是重新创建以获取最新的 interest_dict?
            self.normal_chat_instance = NormalChat(chat_stream=chat_stream, interest_dict=self.get_interest_dict())
            logger.info(f"{log_prefix} 创建或更新 NormalChat 实例。")

            logger.info(f"{log_prefix} 启动 NormalChat 兴趣监控...")
            await self.normal_chat_instance.start_chat()  # <--- 修正：调用 start_chat
            return True
        except Exception as e:
            logger.error(f"{log_prefix} 启动 NormalChat 时出错: {e}")
            logger.error(traceback.format_exc())
            self.normal_chat_instance = None  # 启动失败，清理实例
            return False

    async def _stop_heart_fc_chat(self):
        """停止并清理 HeartFChatting 实例"""
        if self.heart_fc_instance:
            logger.info(f"{self.log_prefix} 关闭 HeartFChatting 实例...")
            try:
                await self.heart_fc_instance.shutdown()
            except Exception as e:
                logger.error(f"{self.log_prefix} 关闭 HeartFChatting 实例时出错: {e}")
                logger.error(traceback.format_exc())
            finally:
                # 无论是否成功关闭，都清理引用
                self.heart_fc_instance = None

    async def _start_heart_fc_chat(self) -> bool:
        """启动 HeartFChatting 实例，确保 NormalChat 已停止"""
        await self._stop_normal_chat()  # 确保普通聊天监控已停止
        self.clear_interest_dict()  # 清理兴趣字典，准备专注聊天

        log_prefix = self.log_prefix
        # 如果实例已存在，检查其循环任务状态
        if self.heart_fc_instance:
            # 如果任务已完成或不存在，则尝试重新启动
            if self.heart_fc_instance._loop_task is None or self.heart_fc_instance._loop_task.done():
                logger.info(f"{log_prefix} HeartFChatting 实例存在但循环未运行，尝试启动...")
                try:
                    await self.heart_fc_instance.start()  # 启动循环
                    logger.info(f"{log_prefix} HeartFChatting 循环已启动。")
                    return True
                except Exception as e:
                    logger.error(f"{log_prefix} 尝试启动现有 HeartFChatting 循环时出错: {e}")
                    logger.error(traceback.format_exc())
                    return False  # 启动失败
            else:
                # 任务正在运行
                logger.debug(f"{log_prefix} HeartFChatting 已在运行中。")
                return True  # 已经在运行

        # 如果实例不存在，则创建并启动
        logger.info(f"{log_prefix} 麦麦准备开始专注聊天 (创建新实例)...")
        try:
            self.heart_fc_instance = HeartFChatting(
                chat_id=self.chat_id,
            )
            if await self.heart_fc_instance._initialize():
                await self.heart_fc_instance.start()  # 初始化成功后启动循环
                logger.info(f"{log_prefix} 麦麦已成功进入专注聊天模式 (新实例已启动)。")
                return True
            else:
                logger.error(f"{log_prefix} HeartFChatting 初始化失败，无法进入专注模式。")
                self.heart_fc_instance = None  # 初始化失败，清理实例
                return False
        except Exception as e:
            logger.error(f"{log_prefix} 创建或启动 HeartFChatting 实例时出错: {e}")
            logger.error(traceback.format_exc())
            self.heart_fc_instance = None  # 创建或初始化异常，清理实例
            return False

    async def set_chat_state(self, new_state: "ChatState", current_states_num: tuple = ()):
        """更新sub_heartflow的聊天状态，并管理 HeartFChatting 和 NormalChat 实例及任务"""
        current_state = self.chat_state.chat_status
        if current_state == new_state:
            # logger.trace(f"{self.log_prefix} 状态已为 {current_state.value}, 无需更改。") # 减少日志噪音
            return

        log_prefix = self.log_prefix
        current_mai_state = self.mai_states.get_current_state()
        state_changed = False  # 标记状态是否实际发生改变

        # --- 状态转换逻辑 ---
        if new_state == ChatState.CHAT:
            normal_limit = current_mai_state.get_normal_chat_max_num()
            current_chat_count = current_states_num[1] if len(current_states_num) > 1 else 0

            if current_chat_count >= normal_limit and current_state != ChatState.CHAT:
                logger.debug(
                    f"{log_prefix} 无法从 {current_state.value} 转到 聊天。原因：聊不过来了 ({current_chat_count}/{normal_limit})"
                )
                return  # 阻止状态转换
            else:
                logger.debug(f"{log_prefix} 准备进入或保持 聊天 状态 ({current_chat_count}/{normal_limit})")
                if await self._start_normal_chat():
                    logger.info(f"{log_prefix} 成功进入或保持 NormalChat 状态。")
                    state_changed = True
                else:
                    logger.error(f"{log_prefix} 启动 NormalChat 失败，无法进入 CHAT 状态。")
                    # 考虑是否需要回滚状态或采取其他措施
                    return  # 启动失败，不改变状态

        elif new_state == ChatState.FOCUSED:
            focused_limit = current_mai_state.get_focused_chat_max_num()
            current_focused_count = current_states_num[2] if len(current_states_num) > 2 else 0

            if current_focused_count >= focused_limit and current_state != ChatState.FOCUSED:
                logger.debug(
                    f"{log_prefix} 无法从 {current_state.value} 转到 专注。原因：聊不过来了 ({current_focused_count}/{focused_limit})"
                )
                return  # 阻止状态转换
            else:
                logger.debug(f"{log_prefix} 准备进入或保持 专注聊天 状态 ({current_focused_count}/{focused_limit})")
                if await self._start_heart_fc_chat():
                    logger.info(f"{log_prefix} 成功进入或保持 HeartFChatting 状态。")
                    state_changed = True
                else:
                    logger.error(f"{log_prefix} 启动 HeartFChatting 失败，无法进入 FOCUSED 状态。")
                    # 启动失败，状态回滚到之前的状态或ABSENT？这里保持不改变
                    return  # 启动失败，不改变状态

        elif new_state == ChatState.ABSENT:
            logger.info(f"{log_prefix} 进入 ABSENT 状态，停止所有聊天活动...")
            await self._stop_normal_chat()
            await self._stop_heart_fc_chat()
            state_changed = True  # 总是可以成功转换到 ABSENT

        # --- 更新状态和最后活动时间 ---
        if state_changed:
            logger.info(f"{log_prefix} 麦麦的聊天状态从 {current_state.value} 变更为 {new_state.value}")
            self.chat_state.chat_status = new_state
            self.last_active_time = time.time()
        else:
            # 如果因为某些原因（如启动失败）没有成功改变状态，记录一下
            logger.debug(
                f"{log_prefix} 尝试将状态从 {current_state.value} 变为 {new_state.value}，但未成功或未执行更改。"
            )

    async def subheartflow_start_working(self):
        """启动子心流的后台任务

        功能说明:
        - 负责子心流的主要后台循环
        - 每30秒检查一次停止标志
        """
        logger.info(f"{self.log_prefix} 子心流开始工作...")

        while not self.should_stop:
            await asyncio.sleep(30)  # 30秒检查一次停止标志

        logger.info(f"{self.log_prefix} 子心流后台任务已停止。")

    async def do_thinking_before_reply(
        self,
        extra_info: str,
        obs_id: list[str] = None,
    ):
        self.last_active_time = time.time()

        current_thinking_info = self.current_mind
        mood_info = self.chat_state.mood
        observation = self._get_primary_observation()

        chat_observe_info = ""
        if obs_id:
            try:
                chat_observe_info = observation.get_observe_info(obs_id)
                logger.debug(f"[{self.subheartflow_id}] Using specific observation IDs: {obs_id}")
            except Exception as e:
                logger.error(
                    f"[{self.subheartflow_id}] Error getting observe info with IDs {obs_id}: {e}. Falling back."
                )
                chat_observe_info = observation.get_observe_info()
        else:
            chat_observe_info = observation.get_observe_info()
            # logger.debug(f"[{self.subheartflow_id}] Using default observation info.")

        extra_info_prompt = ""
        if extra_info:
            for tool_name, tool_data in extra_info.items():
                extra_info_prompt += f"{tool_name} 相关信息:\n"
                for item in tool_data:
                    extra_info_prompt += f"- {item['name']}: {item['content']}\n"
        else:
            extra_info_prompt = "无工具信息。\n"

        individuality = Individuality.get_instance()
        prompt_personality = f"你的名字是{individuality.personality.bot_nickname}，你"
        prompt_personality += individuality.personality.personality_core

        if individuality.personality.personality_sides:
            random_side = random.choice(individuality.personality.personality_sides)
            prompt_personality += f"，{random_side}"

        if individuality.identity.identity_detail:
            random_detail = random.choice(individuality.identity.identity_detail)
            prompt_personality += f"，{random_detail}"

        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        local_random = random.Random()
        current_minute = int(time.strftime("%M"))
        local_random.seed(current_minute)

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
            prompt_personality=prompt_personality,
            bot_name=individuality.personality.bot_nickname,
            current_thinking_info=current_thinking_info,
            time_now=time_now,
            chat_observe_info=chat_observe_info,
            mood_info=mood_info,
            hf_do_next=hf_do_next,
        )

        prompt = await relationship_manager.convert_all_person_sign_to_person_name(prompt)
        prompt = parse_text_timestamps(prompt, mode="lite")

        logger.debug(f"[{self.subheartflow_id}] 心流思考prompt:\n{prompt}\n")

        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)

            logger.debug(f"[{self.subheartflow_id}] 心流思考结果:\n{response}\n")

            if not response:
                response = "(不知道该想些什么...)"
                logger.warning(f"[{self.subheartflow_id}] LLM 返回空结果，思考失败。")
        except Exception as e:
            logger.error(f"[{self.subheartflow_id}] 内心独白获取失败: {e}")
            response = "(思考时发生错误...)"

        self.update_current_mind(response)

        return self.current_mind, self.past_mind

    def update_current_mind(self, response):
        self.past_mind.append(self.current_mind)
        self.current_mind = response

    def add_observation(self, observation: Observation):
        for existing_obs in self.observations:
            if existing_obs.observe_id == observation.observe_id:
                return
        self.observations.append(observation)

    def remove_observation(self, observation: Observation):
        if observation in self.observations:
            self.observations.remove(observation)

    def get_all_observations(self) -> list[Observation]:
        return self.observations

    def clear_observations(self):
        self.observations.clear()

    def _get_primary_observation(self) -> Optional[ChattingObservation]:
        if self.observations and isinstance(self.observations[0], ChattingObservation):
            return self.observations[0]
        logger.warning(f"SubHeartflow {self.subheartflow_id} 没有找到有效的 ChattingObservation")
        return None

    async def get_interest_state(self) -> dict:
        return await self.interest_chatting.get_state()

    async def get_interest_level(self) -> float:
        return await self.interest_chatting.get_interest()

    async def should_evaluate_reply(self) -> bool:
        return await self.interest_chatting.should_evaluate_reply()

    async def add_interest_dict_entry(self, message: MessageRecv, interest_value: float, is_mentioned: bool):
        self.interest_chatting.add_interest_dict(message, interest_value, is_mentioned)

    def get_interest_dict(self) -> Dict[str, tuple[MessageRecv, float, bool]]:
        return self.interest_chatting.interest_dict

    def clear_interest_dict(self):
        self.interest_chatting.interest_dict.clear()

    async def get_full_state(self) -> dict:
        """获取子心流的完整状态，包括兴趣、思维和聊天状态。"""
        interest_state = await self.get_interest_state()
        return {
            "interest_state": interest_state,
            "current_mind": self.current_mind,
            "chat_state": self.chat_state.chat_status.value,
            "last_active_time": self.last_active_time,
        }

    async def shutdown(self):
        """安全地关闭子心流及其管理的任务"""
        if self.should_stop:
            logger.info(f"{self.log_prefix} 子心流已在关闭过程中。")
            return

        logger.info(f"{self.log_prefix} 开始关闭子心流...")
        self.should_stop = True  # 标记为停止，让后台任务退出

        # 使用新的停止方法
        await self._stop_normal_chat()
        await self._stop_heart_fc_chat()

        # 停止兴趣更新任务
        if self.interest_chatting:
            logger.info(f"{self.log_prefix} 停止兴趣系统后台任务...")
            await self.interest_chatting.stop_updates()

        # 取消可能存在的旧后台任务 (self.task)
        if self.task and not self.task.done():
            logger.info(f"{self.log_prefix} 取消子心流主任务 (Shutdown)...")
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=1.0)  # 给点时间响应取消
            except asyncio.CancelledError:
                logger.info(f"{self.log_prefix} 子心流主任务已取消 (Shutdown)。")
            except asyncio.TimeoutError:
                logger.warning(f"{self.log_prefix} 等待子心流主任务取消超时 (Shutdown)。")
            except Exception as e:
                logger.error(f"{self.log_prefix} 等待子心流主任务取消时发生错误 (Shutdown): {e}")

        self.task = None  # 清理任务引用
        self.chat_state.chat_status = ChatState.ABSENT  # 状态重置为不参与

        logger.info(f"{self.log_prefix} 子心流关闭完成。")


init_prompt()
