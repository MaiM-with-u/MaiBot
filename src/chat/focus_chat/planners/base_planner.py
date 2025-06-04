from abc import ABC, abstractmethod
from typing import List, Dict, Any
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.chat.focus_chat.info.info_base import InfoBase


class BasePlanner(ABC):
    """规划器基类"""

    def __init__(self, log_prefix: str, action_manager: ActionManager):
        self.log_prefix = log_prefix
        self.action_manager = action_manager

    @abstractmethod
    async def plan(self, all_plan_info: List[InfoBase], running_memorys: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        规划下一步行动

        Args:
            all_plan_info: 所有计划信息
            running_memorys: 回忆信息

        Returns:
            Dict[str, Any]: 规划结果
        """
        pass
