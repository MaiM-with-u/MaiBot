from src.chat.heart_flow.sub_heartflow import SubHeartflow, ChatState
from src.common.logger_manager import get_logger
from typing import Any, Optional, List
from src.chat.heart_flow.subheartflow_manager import SubHeartflowManager
from src.chat.heart_flow.background_tasks import BackgroundTaskManager  # Import BackgroundTaskManager

logger = get_logger("heartflow")


class Heartflow:
    """主心流协调器，负责初始化并协调各个子系统:
    - 状态管理 (MaiState)
    - 子心流管理 (SubHeartflow)
    - 后台任务 (BackgroundTaskManager)
    """

    def __init__(self):
        # 子心流管理 (在初始化时传入 current_state)
        self.subheartflow_manager: SubHeartflowManager = SubHeartflowManager()

        # 后台任务管理器 (整合所有定时任务)
        self.background_task_manager: BackgroundTaskManager = BackgroundTaskManager(
            subheartflow_manager=self.subheartflow_manager,
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

    async def api_get_normal_chat_replies(self, subheartflow_id: str, limit: int = 10) -> Optional[List[dict]]:
        """获取子心流的NormalChat回复记录

        Args:
            subheartflow_id: 子心流ID
            limit: 最大返回数量，默认10条

        Returns:
            Optional[List[dict]]: 回复记录列表，如果子心流不存在则返回None
        """
        subheartflow = await self.subheartflow_manager.get_or_create_subheartflow(subheartflow_id)
        if not subheartflow:
            logger.warning(f"尝试获取不存在的子心流 {subheartflow_id} 的NormalChat回复记录")
            return None

        return subheartflow.get_normal_chat_recent_replies(limit)

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


heartflow = Heartflow()
