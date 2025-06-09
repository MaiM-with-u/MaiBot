from typing import Any
from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.person_info.person_info import person_info_manager

logger = get_logger("config_api")

class ConfigAPI:
    """配置API模块
    
    提供了配置读取和用户信息获取等功能
    """
    
    def get_global_config(self, key: str, default: Any = None) -> Any:
        """
        安全地从全局配置中获取一个值。
        插件应使用此方法读取全局配置，以保证只读和隔离性。
        
        Args:
            key: 配置键名
            default: 如果配置不存在时返回的默认值
            
        Returns:
            Any: 配置值或默认值
        """
        return global_config.get(key, default)
        
    async def get_user_id_by_person_name(self, person_name: str) -> tuple[str, str]:
        """根据用户名获取用户ID
        
        Args:
            person_name: 用户名
            
        Returns:
            tuple[str, str]: (平台, 用户ID)
        """
        person_id = person_info_manager.get_person_id_by_person_name(person_name)
        user_id = await person_info_manager.get_value(person_id, "user_id")
        platform = await person_info_manager.get_value(person_id, "platform")
        return platform, user_id
        
    async def get_person_info(self, person_id: str, key: str, default: Any = None) -> Any:
        """获取用户信息
        
        Args:
            person_id: 用户ID
            key: 信息键名
            default: 默认值
            
        Returns:
            Any: 用户信息值或默认值
        """
        return await person_info_manager.get_value(person_id, key, default) 