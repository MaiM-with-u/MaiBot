import asyncio
import time
import json
import os
import traceback
from typing import TYPE_CHECKING, Dict, List

from src.common.logger_manager import get_logger

# Need chat_manager to get stream names
from src.chat.message_receive.chat_stream import chat_manager

if TYPE_CHECKING:
    from .subheartflow_manager import SubHeartflowManager
    from .sub_heartflow import SubHeartflow
    from .heartflow import Heartflow  # 导入 Heartflow 类型


logger = get_logger("interest")

# Consider moving log directory/filename constants here
LOG_DIRECTORY = "logs/interest"
HISTORY_LOG_FILENAME = "interest_history.log"


def _ensure_log_directory():
    """确保日志目录存在。"""
    os.makedirs(LOG_DIRECTORY, exist_ok=True)
    logger.info(f"已确保日志目录 '{LOG_DIRECTORY}' 存在")


def _clear_and_create_log_file():
    """清除日志文件并创建新的日志文件。"""
    if os.path.exists(os.path.join(LOG_DIRECTORY, HISTORY_LOG_FILENAME)):
        os.remove(os.path.join(LOG_DIRECTORY, HISTORY_LOG_FILENAME))
    with open(os.path.join(LOG_DIRECTORY, HISTORY_LOG_FILENAME), "w", encoding="utf-8") as f:
        f.write("")


class InterestLogger:
    """负责定期记录主心流和所有子心流的状态到日志文件。"""

    def __init__(self, subheartflow_manager: "SubHeartflowManager", heartflow: "Heartflow"):
        """
        初始化 InterestLogger。

        Args:
            subheartflow_manager: 子心流管理器实例。
            heartflow: 主心流实例，用于获取主心流状态。
        """
        self.subheartflow_manager = subheartflow_manager
        self.heartflow = heartflow  # 存储 Heartflow 实例
        self._history_log_file_path = os.path.join(LOG_DIRECTORY, HISTORY_LOG_FILENAME)
        _ensure_log_directory()
        _clear_and_create_log_file()

    async def get_all_subflow_states(self) -> Dict[str, Dict]:
        """并发获取所有活跃子心流的当前完整状态。"""
        all_flows: List["SubHeartflow"] = self.subheartflow_manager.get_all_subheartflows()
        tasks = []
        results = {}

        if not all_flows:
            # logger.debug("未找到任何子心流状态")
            return results

        for subheartflow in all_flows:
            if await self.subheartflow_manager.get_or_create_subheartflow(subheartflow.subheartflow_id):
                tasks.append(
                    asyncio.create_task(subheartflow.get_full_state(), name=f"get_state_{subheartflow.subheartflow_id}")
                )
            else:
                logger.warning(f"子心流 {subheartflow.subheartflow_id} 在创建任务前已消失")

        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=5.0)

            if pending:
                logger.warning(f"获取子心流状态超时，有 {len(pending)} 个任务未完成")
                for task in pending:
                    task.cancel()

            for task in done:
                stream_id_str = task.get_name().split("get_state_")[-1]
                stream_id = stream_id_str

                if task.cancelled():
                    logger.warning(f"获取子心流 {stream_id} 状态的任务已取消(超时)", exc_info=False)
                elif task.exception():
                    exc = task.exception()
                    logger.warning(f"获取子心流 {stream_id} 状态出错: {exc}")
                else:
                    result = task.result()
                    results[stream_id] = result

        logger.trace(f"成功获取 {len(results)} 个子心流的完整状态")
        return results

    async def log_all_states(self):
        """获取主心流状态和所有子心流的完整状态并写入日志文件。"""
        try:
            current_timestamp = time.time()

            # main_mind = self.heartflow.current_mind
            # 获取 Mai 状态名称
            mai_state_name = self.heartflow.current_state.get_current_state().name

            all_subflow_states = await self.get_all_subflow_states()

            log_entry_base = {
                "timestamp": round(current_timestamp, 2),
                # "main_mind": main_mind,
                "mai_state": mai_state_name,
                "subflow_count": len(all_subflow_states),
                "subflows": [],
            }

            if not all_subflow_states:
                # logger.debug("没有获取到任何子心流状态，仅记录主心流状态")
                with open(self._history_log_file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry_base, ensure_ascii=False) + "\n")
                return

            subflow_details = []
            items_snapshot = list(all_subflow_states.items())
            for stream_id, state in items_snapshot:
                group_name = stream_id
                try:
                    chat_stream = chat_manager.get_stream(stream_id)
                    if chat_stream:
                        if chat_stream.group_info:
                            group_name = chat_stream.group_info.group_name
                        elif chat_stream.user_info:
                            group_name = f"私聊_{chat_stream.user_info.user_nickname}"
                except Exception as e:
                    logger.trace(f"无法获取 stream_id {stream_id} 的群组名: {e}")

                interest_state = state.get("interest_state", {})

                subflow_entry = {
                    "stream_id": stream_id,
                    "group_name": group_name,
                    "sub_mind": state.get("current_mind", "未知"),
                    "sub_chat_state": state.get("chat_state", "未知"),
                    "interest_level": interest_state.get("interest_level", 0.0),
                    "start_hfc_probability": interest_state.get("start_hfc_probability", 0.0),
                    # "is_above_threshold": interest_state.get("is_above_threshold", False),
                }
                subflow_details.append(subflow_entry)

            log_entry_base["subflows"] = subflow_details

            with open(self._history_log_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry_base, ensure_ascii=False) + "\n")

        except IOError as e:
            logger.error(f"写入状态日志到 {self._history_log_file_path} 出错: {e}")
        except Exception as e:
            logger.error(f"记录状态时发生意外错误: {e}")
            logger.error(traceback.format_exc())

    async def api_get_all_states(self):
        """获取主心流和所有子心流的状态。"""
        try:
            current_timestamp = time.time()

            # main_mind = self.heartflow.current_mind
            # 获取 Mai 状态名称
            mai_state_name = self.heartflow.current_state.get_current_state().name

            all_subflow_states = await self.get_all_subflow_states()

            log_entry_base = {
                "timestamp": round(current_timestamp, 2),
                # "main_mind": main_mind,
                "mai_state": mai_state_name,
                "subflow_count": len(all_subflow_states),
                "subflows": [],
            }

            subflow_details = []
            items_snapshot = list(all_subflow_states.items())
            for stream_id, state in items_snapshot:
                group_name = stream_id
                try:
                    chat_stream = chat_manager.get_stream(stream_id)
                    if chat_stream:
                        if chat_stream.group_info:
                            group_name = chat_stream.group_info.group_name
                        elif chat_stream.user_info:
                            group_name = f"私聊_{chat_stream.user_info.user_nickname}"
                except Exception as e:
                    logger.trace(f"无法获取 stream_id {stream_id} 的群组名: {e}")

                interest_state = state.get("interest_state", {})

                subflow_entry = {
                    "stream_id": stream_id,
                    "group_name": group_name,
                    "sub_mind": state.get("current_mind", "未知"),
                    "sub_chat_state": state.get("chat_state", "未知"),
                    "interest_level": interest_state.get("interest_level", 0.0),
                    "start_hfc_probability": interest_state.get("start_hfc_probability", 0.0),
                    # "is_above_threshold": interest_state.get("is_above_threshold", False),
                }
                subflow_details.append(subflow_entry)

            log_entry_base["subflows"] = subflow_details
            return subflow_details
        except Exception as e:
            logger.error(f"记录状态时发生意外错误: {e}")
            logger.error(traceback.format_exc())
