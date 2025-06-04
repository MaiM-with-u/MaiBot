import time
import os
from typing import Optional, Dict, Any
from src.common.logger_manager import get_logger
import json

logger = get_logger("hfc")  # Logger Name Changed

log_dir = "log/log_cycle_debug/"


class CycleDetail:
    """循环信息记录类"""

    def __init__(self, cycle_id: int):
        self.cycle_id = cycle_id
        self.prefix = ""
        self.thinking_id = ""
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.timers: Dict[str, float] = {}

        # 新字段
        self.loop_observation_info: Dict[str, Any] = {}
        self.loop_process_info: Dict[str, Any] = {}
        self.loop_plan_info: Dict[str, Any] = {}
        self.loop_action_info: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        """将循环信息转换为字典格式"""

        def convert_to_serializable(obj, depth=0, seen=None):
            if seen is None:
                seen = set()

            # 防止递归过深
            if depth > 5:  # 降低递归深度限制
                return str(obj)

            # 防止循环引用
            obj_id = id(obj)
            if obj_id in seen:
                return str(obj)
            seen.add(obj_id)

            try:
                if hasattr(obj, "to_dict"):
                    # 对于有to_dict方法的对象，直接调用其to_dict方法
                    return obj.to_dict()
                elif isinstance(obj, dict):
                    # 对于字典，只保留基本类型和可序列化的值
                    return {
                        k: convert_to_serializable(v, depth + 1, seen)
                        for k, v in obj.items()
                        if isinstance(k, (str, int, float, bool))
                    }
                elif isinstance(obj, (list, tuple)):
                    # 对于列表和元组，只保留可序列化的元素
                    return [
                        convert_to_serializable(item, depth + 1, seen)
                        for item in obj
                        if not isinstance(item, (dict, list, tuple))
                        or isinstance(item, (str, int, float, bool, type(None)))
                    ]
                elif isinstance(obj, (str, int, float, bool, type(None))):
                    return obj
                else:
                    return str(obj)
            finally:
                seen.remove(obj_id)

        return {
            "cycle_id": self.cycle_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "timers": self.timers,
            "thinking_id": self.thinking_id,
            "loop_observation_info": convert_to_serializable(self.loop_observation_info),
            "loop_process_info": convert_to_serializable(self.loop_process_info),
            "loop_plan_info": convert_to_serializable(self.loop_plan_info),
            "loop_action_info": convert_to_serializable(self.loop_action_info),
        }

    def complete_cycle(self):
        """完成循环，记录结束时间"""
        self.end_time = time.time()

        # 处理 prefix，只保留中英文字符和基本标点
        if not self.prefix:
            self.prefix = "group"
        else:
            # 只保留中文、英文字母、数字和基本标点
            allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
            self.prefix = (
                "".join(char for char in self.prefix if "\u4e00" <= char <= "\u9fff" or char in allowed_chars)
                or "group"
            )

        current_time_minute = time.strftime("%Y%m%d_%H%M", time.localtime())
        try:
            self.log_cycle_to_file(
                log_dir + self.prefix + f"/{current_time_minute}_cycle_" + str(self.cycle_id) + ".json"
            )
        except Exception as e:
            logger.warning(f"写入文件日志，可能是群名称包含非法字符: {e}")

    def log_cycle_to_file(self, file_path: str):
        """将循环信息写入文件"""
        # 如果目录不存在，则创建目
        dir_name = os.path.dirname(file_path)
        # 去除特殊字符，保留字母、数字、下划线、中划线和中文
        dir_name = "".join(
            char for char in dir_name if char.isalnum() or char in ["_", "-", "/"] or "\u4e00" <= char <= "\u9fff"
        )
        # print("dir_name:", dir_name)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
        # 写入文件
        

        file_path = os.path.join(dir_name, os.path.basename(file_path))
        # print("file_path:", file_path)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")

    def set_thinking_id(self, thinking_id: str):
        """设置思考消息ID"""
        self.thinking_id = thinking_id

    def set_loop_info(self, loop_info: Dict[str, Any]):
        """设置循环信息"""
        self.loop_observation_info = loop_info["loop_observation_info"]
        self.loop_processor_info = loop_info["loop_processor_info"]
        self.loop_plan_info = loop_info["loop_plan_info"]
        self.loop_action_info = loop_info["loop_action_info"]
