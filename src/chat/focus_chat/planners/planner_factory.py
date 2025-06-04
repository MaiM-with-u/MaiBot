from typing import Dict, Type
from src.chat.focus_chat.planners.base_planner import BasePlanner
from src.chat.focus_chat.planners.planner_simple import ActionPlanner as SimpleActionPlanner
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.config.config import global_config
from src.common.logger_manager import get_logger

logger = get_logger("planner_factory")


class PlannerFactory:
    """规划器工厂类，用于创建不同类型的规划器实例"""

    # 注册所有可用的规划器类型
    _planner_types: Dict[str, Type[BasePlanner]] = {
        "simple": SimpleActionPlanner,
    }

    @classmethod
    def register_planner(cls, name: str, planner_class: Type[BasePlanner]) -> None:
        """
        注册新的规划器类型

        Args:
            name: 规划器类型名称
            planner_class: 规划器类
        """
        cls._planner_types[name] = planner_class
        logger.info(f"注册新的规划器类型: {name}")

    @classmethod
    def create_planner(cls, log_prefix: str, action_manager: ActionManager) -> BasePlanner:
        """
        创建规划器实例

        Args:
            log_prefix: 日志前缀
            action_manager: 动作管理器实例

        Returns:
            BasePlanner: 规划器实例
        """
        planner_type = global_config.focus_chat.planner_type

        if planner_type not in cls._planner_types:
            logger.warning(f"{log_prefix} 未知的规划器类型: {planner_type}，使用默认规划器")
            planner_type = "complex"

        planner_class = cls._planner_types[planner_type]
        logger.info(f"{log_prefix} 使用{planner_type}规划器")
        return planner_class(log_prefix=log_prefix, action_manager=action_manager)
