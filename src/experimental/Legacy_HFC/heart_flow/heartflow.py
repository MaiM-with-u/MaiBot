from .sub_heartflow import SubHeartflow, ChatState
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from ..schedule.schedule_generator import bot_schedule
from src.common.logger_manager import get_logger
from typing import Any, Optional
from src.tools.tool_use import ToolUser
from src.chat.person_info.relationship_manager import relationship_manager  # Module instance
from .mai_state_manager import MaiStateInfo, MaiStateManager
from .subheartflow_manager import SubHeartflowManager
from .mind import Mind
from .interest_logger import InterestLogger  # Import InterestLogger
from .background_tasks import BackgroundTaskManager  # Import BackgroundTaskManager

logger = get_logger("heartflow")


class Heartflow:
    """主心流协调器，负责初始化并协调各个子系统:
    - 状态管理 (MaiState)
    - 子心流管理 (SubHeartflow)
    - 思考过程 (Mind)
    - 日志记录 (InterestLogger)
    - 后台任务 (BackgroundTaskManager)
    """

    def __init__(self):
        # 核心状态
        self.current_mind = "什么也没想"  # 当前主心流想法
        self.past_mind = []  # 历史想法记录

        # 状态管理相关
        self.current_state: MaiStateInfo = MaiStateInfo()  # 当前状态信息
        self.mai_state_manager: MaiStateManager = MaiStateManager()  # 状态决策管理器

        # 子心流管理 (在初始化时传入 current_state)
        self.subheartflow_manager: SubHeartflowManager = SubHeartflowManager(self.current_state)

        # LLM模型配置
        self.llm_model = LLMRequest(
            model=global_config.llm_heartflow, temperature=0.6, max_tokens=1000, request_type="heart_flow"
        )

        # 外部依赖模块
        self.tool_user_instance = ToolUser()  # 工具使用模块
        self.relationship_manager_instance = relationship_manager  # 关系管理模块

        # 子系统初始化
        self.mind: Mind = Mind(self.subheartflow_manager, self.llm_model)  # 思考管理器
        self.interest_logger: InterestLogger = InterestLogger(self.subheartflow_manager, self)  # 兴趣日志记录器

        # 后台任务管理器 (整合所有定时任务)
        self.background_task_manager: BackgroundTaskManager = BackgroundTaskManager(
            mai_state_info=self.current_state,
            mai_state_manager=self.mai_state_manager,
            subheartflow_manager=self.subheartflow_manager,
            interest_logger=self.interest_logger,
        )

    async def get_or_create_subheartflow(self, subheartflow_id: Any) -> Optional["SubHeartflow"]:
        """获取或创建一个新的SubHeartflow实例 - 委托给 SubHeartflowManager"""
        # 不再需要传入 self.current_state
        return await self.subheartflow_manager.get_or_create_subheartflow(subheartflow_id)

    async def force_change_subheartflow_status(self, subheartflow_id: str, status: ChatState) -> None:
        """强制改变子心流的状态"""
        # 这里的 message 是可选的，可能是一个消息对象，也可能是其他类型的数据
        return await self.subheartflow_manager.force_change_state(subheartflow_id, status)

    async def api_get_all_states(self):
        """获取所有状态"""
        return await self.interest_logger.api_get_all_states()

    async def api_get_subheartflow_cycle_info(self, subheartflow_id: str, history_len: int) -> Optional[dict]:
        """获取子心流的循环信息"""
        subheartflow = await self.subheartflow_manager.get_or_create_subheartflow(subheartflow_id)
        if not subheartflow:
            logger.warning(f"尝试获取不存在的子心流 {subheartflow_id} 的周期信息")
            return None
        heartfc_instance = subheartflow.heart_fc_instance
        if not heartfc_instance:
            logger.warning(f"子心流 {subheartflow_id} 没有心流实例，无法获取周期信息")
            return None

        return heartfc_instance.get_cycle_history(last_n=history_len)

    async def heartflow_start_working(self):
        """启动后台任务"""
        await self.background_task_manager.start_tasks()
        logger.info("[Heartflow] 后台任务已启动")

    # 根本不会用到这个函数吧，那样麦麦直接死了
    async def stop_working(self):
        """停止所有任务和子心流"""
        logger.info("[Heartflow] 正在停止任务和子心流...")
        await self.background_task_manager.stop_tasks()
        await self.subheartflow_manager.deactivate_all_subflows()
        logger.info("[Heartflow] 所有任务和子心流已停止")

    async def do_a_thinking(self):
        """执行一次主心流思考过程"""
        schedule_info = bot_schedule.get_current_num_task(num=4, time_info=True)
        new_mind = await self.mind.do_a_thinking(
            current_main_mind=self.current_mind, mai_state_info=self.current_state, schedule_info=schedule_info
        )
        self.past_mind.append(self.current_mind)
        self.current_mind = new_mind
        logger.info(f"麦麦的总体脑内状态更新为：{self.current_mind[:100]}...")
        self.mind.update_subflows_with_main_mind(new_mind)


heartflow = Heartflow()
