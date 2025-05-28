from src.chat.heart_flow.heartflow import heartflow
from src.chat.heart_flow.sub_heartflow import ChatState
from src.common.logger_manager import get_logger
import time

logger = get_logger("api")


async def get_all_subheartflow_ids() -> list:
    """获取所有子心流的ID列表"""
    all_subheartflows = heartflow.subheartflow_manager.get_all_subheartflows()
    return [subheartflow.subheartflow_id for subheartflow in all_subheartflows]


async def forced_change_subheartflow_status(subheartflow_id: str, status: ChatState) -> bool:
    """强制改变子心流的状态"""
    subheartflow = await heartflow.get_or_create_subheartflow(subheartflow_id)
    if subheartflow:
        return await heartflow.force_change_subheartflow_status(subheartflow_id, status)
    return False


async def get_subheartflow_cycle_info(subheartflow_id: str, history_len: int) -> dict:
    """获取子心流的循环信息"""
    subheartflow_cycle_info = await heartflow.api_get_subheartflow_cycle_info(subheartflow_id, history_len)
    logger.debug(f"子心流 {subheartflow_id} 循环信息: {subheartflow_cycle_info}")
    if subheartflow_cycle_info:
        return subheartflow_cycle_info
    else:
        logger.warning(f"子心流 {subheartflow_id} 循环信息未找到")
        return None


async def get_normal_chat_replies(subheartflow_id: str, limit: int = 10) -> list:
    """获取子心流的NormalChat回复记录

    Args:
        subheartflow_id: 子心流ID
        limit: 最大返回数量，默认10条

    Returns:
        list: 回复记录列表，如果未找到则返回空列表
    """
    replies = await heartflow.api_get_normal_chat_replies(subheartflow_id, limit)
    logger.debug(f"子心流 {subheartflow_id} NormalChat回复记录: 获取到 {len(replies) if replies else 0} 条")
    if replies:
        # 格式化时间戳为可读时间
        for reply in replies:
            if "time" in reply:
                reply["formatted_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reply["time"]))
        return replies
    else:
        logger.warning(f"子心流 {subheartflow_id} NormalChat回复记录未找到")
        return []


async def get_all_states():
    """获取所有状态"""
    all_states = await heartflow.api_get_all_states()
    logger.debug(f"所有状态: {all_states}")
    return all_states
