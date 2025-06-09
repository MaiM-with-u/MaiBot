import os
import json
import time
from typing import Any, Dict, List, Optional
from src.common.logger_manager import get_logger

logger = get_logger("utils_api")

class UtilsAPI:
    """工具类API模块
    
    提供了各种辅助功能
    """
    
    def get_plugin_path(self) -> str:
        """获取当前插件的路径
        
        Returns:
            str: 插件目录的绝对路径
        """
        import inspect
        plugin_module_path = inspect.getfile(self.__class__)
        plugin_dir = os.path.dirname(plugin_module_path)
        return plugin_dir
        
    def read_json_file(self, file_path: str, default: Any = None) -> Any:
        """读取JSON文件
        
        Args:
            file_path: 文件路径，可以是相对于插件目录的路径
            default: 如果文件不存在或读取失败时返回的默认值
            
        Returns:
            Any: JSON数据或默认值
        """
        try:
            # 如果是相对路径，则相对于插件目录
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.get_plugin_path(), file_path)
                
            if not os.path.exists(file_path):
                logger.warning(f"{self.log_prefix} 文件不存在: {file_path}")
                return default
                
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"{self.log_prefix} 读取JSON文件出错: {e}")
            return default
            
    def write_json_file(self, file_path: str, data: Any, indent: int = 2) -> bool:
        """写入JSON文件
        
        Args:
            file_path: 文件路径，可以是相对于插件目录的路径
            data: 要写入的数据
            indent: JSON缩进
            
        Returns:
            bool: 是否写入成功
        """
        try:
            # 如果是相对路径，则相对于插件目录
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.get_plugin_path(), file_path)
                
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            return True
        except Exception as e:
            logger.error(f"{self.log_prefix} 写入JSON文件出错: {e}")
            return False
            
    def get_timestamp(self) -> int:
        """获取当前时间戳
        
        Returns:
            int: 当前时间戳（秒）
        """
        return int(time.time())
        
    def format_time(self, timestamp: Optional[int] = None, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
        """格式化时间
        
        Args:
            timestamp: 时间戳，如果为None则使用当前时间
            format_str: 时间格式字符串
            
        Returns:
            str: 格式化后的时间字符串
        """
        import datetime
        if timestamp is None:
            timestamp = time.time()
        return datetime.datetime.fromtimestamp(timestamp).strftime(format_str)
        
    def parse_time(self, time_str: str, format_str: str = "%Y-%m-%d %H:%M:%S") -> int:
        """解析时间字符串为时间戳
        
        Args:
            time_str: 时间字符串
            format_str: 时间格式字符串
            
        Returns:
            int: 时间戳（秒）
        """
        import datetime
        dt = datetime.datetime.strptime(time_str, format_str)
        return int(dt.timestamp())
        
    def generate_unique_id(self) -> str:
        """生成唯一ID
        
        Returns:
            str: 唯一ID
        """
        import uuid
        return str(uuid.uuid4()) 