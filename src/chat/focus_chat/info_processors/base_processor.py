from abc import ABC, abstractmethod
from typing import List, Any
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.heart_flow.observation.observation import Observation
from src.common.logger import get_logger

logger = get_logger("base_processor")


class BaseProcessor(ABC):
    """信息处理器基类

    所有具体的信息处理器都应该继承这个基类，并实现process_info方法。
    支持处理InfoBase和Observation类型的输入。
    """

    log_prefix = "Base信息处理器"

    @abstractmethod
    def __init__(self):
        """初始化处理器"""

    @abstractmethod
    async def process_info(
        self,
        observations: List[Observation] = None,
        **kwargs: Any,
    ) -> List[InfoBase]:
        """处理信息对象的抽象方法

        Args:
            infos: InfoBase对象列表
            observations: 可选的Observation对象列表
            **kwargs: 其他可选参数

        Returns:
            List[InfoBase]: 处理后的InfoBase实例列表
        """
        pass

    def _create_processed_item(self, info_type: str, info_data: Any) -> dict:
        """创建处理后的信息项

        Args:
            info_type: 信息类型
            info_data: 信息数据

        Returns:
            dict: 处理后的信息项
        """
        return {"type": info_type, "id": f"info_{info_type}", "content": info_data, "ttl": 3}
