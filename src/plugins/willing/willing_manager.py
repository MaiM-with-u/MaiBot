from typing import Optional
from src.common.logger import get_module_logger

from ..config.config import global_config
from .mode_classical import WillingManager as ClassicalWillingManager
from .mode_dynamic import WillingManager as DynamicWillingManager
from .mode_custom import WillingManager as CustomWillingManager
from src.common.logger import LogConfig

willing_config = LogConfig(
    console_format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<red>{extra[module]: <12}</red> | "
        "<level>{message}</level>"
    ),
)

logger = get_module_logger("willing", config=willing_config)


def init_willing_manager() -> Optional[object]:
    """
    根据配置初始化并返回对应的WillingManager实例

    Returns:
        对应mode的WillingManager实例
    """
    mode = global_config.willing_mode.lower()

    if mode == "classical":
        logger.info("使用经典回复意愿管理器")
        return ClassicalWillingManager()
    elif mode == "dynamic":
        logger.info("使用动态回复意愿管理器")
        return DynamicWillingManager()
    elif mode == "custom":
        logger.warning(f"自定义的回复意愿管理器模式: {mode}")
        return CustomWillingManager()
    else:
        logger.warning(f"未知的回复意愿管理器模式: {mode}, 将使用经典模式")
        return ClassicalWillingManager()


# 全局willing_manager对象
willing_manager = init_willing_manager()
