import json
from datetime import datetime
from typing import Dict, Any
from pathlib import Path
from src.common.logger import get_logger

logger = get_logger("hfc_performance")


class HFCPerformanceLogger:
    """HFC性能记录管理器"""

    # 版本号常量，可在启动时修改
    INTERNAL_VERSION = "v1.0.0"

    def __init__(self, chat_id: str, version: str = None):
        self.chat_id = chat_id
        self.version = version or self.INTERNAL_VERSION
        self.log_dir = Path("log/hfc_loop")
        self.session_start_time = datetime.now()

        # 确保目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 当前会话的日志文件，包含版本号
        version_suffix = self.version.replace(".", "_")
        self.session_file = (
            self.log_dir / f"{chat_id}_{version_suffix}_{self.session_start_time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        self.current_session_data = []

    def record_cycle(self, cycle_data: Dict[str, Any]):
        """记录单次循环数据"""
        try:
            # 构建记录数据
            record = {
                "timestamp": datetime.now().isoformat(),
                "version": self.version,
                "cycle_id": cycle_data.get("cycle_id"),
                "chat_id": self.chat_id,
                "action_type": cycle_data.get("action_type", "unknown"),
                "total_time": cycle_data.get("total_time", 0),
                "step_times": cycle_data.get("step_times", {}),
                "processor_time_costs": cycle_data.get("processor_time_costs", {}),  # 前处理器时间
                "post_processor_time_costs": cycle_data.get("post_processor_time_costs", {}),  # 后处理器时间
                "reasoning": cycle_data.get("reasoning", ""),
                "success": cycle_data.get("success", False),
            }

            # 添加到当前会话数据
            self.current_session_data.append(record)

            # 立即写入文件（防止数据丢失）
            self._write_session_data()

            # 构建详细的日志信息
            log_parts = [
                f"cycle_id={record['cycle_id']}",
                f"action={record['action_type']}",
                f"time={record['total_time']:.2f}s",
            ]

            # 添加后处理器时间信息到日志
            if record["post_processor_time_costs"]:
                post_processor_stats = ", ".join(
                    [f"{name}: {time_cost:.3f}s" for name, time_cost in record["post_processor_time_costs"].items()]
                )
                log_parts.append(f"post_processors=({post_processor_stats})")

            logger.debug(f"记录HFC循环数据: {', '.join(log_parts)}")

        except Exception as e:
            logger.error(f"记录HFC循环数据失败: {e}")

    def _write_session_data(self):
        """写入当前会话数据到文件"""
        try:
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(self.current_session_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"写入会话数据失败: {e}")

    def get_current_session_stats(self) -> Dict[str, Any]:
        """获取当前会话的基本信息"""
        if not self.current_session_data:
            return {}

        return {
            "chat_id": self.chat_id,
            "version": self.version,
            "session_file": str(self.session_file),
            "record_count": len(self.current_session_data),
            "start_time": self.session_start_time.isoformat(),
        }

    def finalize_session(self):
        """结束会话"""
        try:
            if self.current_session_data:
                logger.info(f"完成会话，当前会话 {len(self.current_session_data)} 条记录")
        except Exception as e:
            logger.error(f"结束会话失败: {e}")

    @classmethod
    def cleanup_old_logs(cls, max_size_mb: float = 50.0):
        """
        清理旧的HFC日志文件，保持目录大小在指定限制内

        Args:
            max_size_mb: 最大目录大小限制（MB）
        """
        log_dir = Path("log/hfc_loop")
        if not log_dir.exists():
            logger.info("HFC日志目录不存在，跳过日志清理")
            return

        # 获取所有日志文件及其信息
        log_files = []
        total_size = 0

        for log_file in log_dir.glob("*.json"):
            try:
                file_stat = log_file.stat()
                log_files.append({"path": log_file, "size": file_stat.st_size, "mtime": file_stat.st_mtime})
                total_size += file_stat.st_size
            except Exception as e:
                logger.warning(f"无法获取文件信息 {log_file}: {e}")

        if not log_files:
            logger.info("没有找到HFC日志文件")
            return

        max_size_bytes = max_size_mb * 1024 * 1024
        current_size_mb = total_size / (1024 * 1024)

        logger.info(f"HFC日志目录当前大小: {current_size_mb:.2f}MB，限制: {max_size_mb}MB")

        if total_size <= max_size_bytes:
            logger.info("HFC日志目录大小在限制范围内，无需清理")
            return

        # 按修改时间排序（最早的在前面）
        log_files.sort(key=lambda x: x["mtime"])

        deleted_count = 0
        deleted_size = 0

        for file_info in log_files:
            if total_size <= max_size_bytes:
                break

            try:
                file_size = file_info["size"]
                file_path = file_info["path"]

                file_path.unlink()
                total_size -= file_size
                deleted_size += file_size
                deleted_count += 1

                logger.info(f"删除旧日志文件: {file_path.name} ({file_size / 1024:.1f}KB)")

            except Exception as e:
                logger.error(f"删除日志文件失败 {file_info['path']}: {e}")

        final_size_mb = total_size / (1024 * 1024)
        deleted_size_mb = deleted_size / (1024 * 1024)

        logger.info(f"HFC日志清理完成: 删除了{deleted_count}个文件，释放{deleted_size_mb:.2f}MB空间")
        logger.info(f"清理后目录大小: {final_size_mb:.2f}MB")
