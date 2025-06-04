from dataclasses import dataclass
from .info_base import InfoBase


@dataclass
class RelationInfo(InfoBase):
    """关系信息类

    用于存储和管理当前关系状态的信息。

    Attributes:
        type (str): 信息类型标识符，默认为 "relation"
        data (Dict[str, Any]): 包含 current_relation 的数据字典
    """

    type: str = "relation"

    def get_relation_info(self) -> str:
        """获取当前关系状态

        Returns:
            str: 当前关系状态
        """
        return self.get_info("relation_info") or ""

    def set_relation_info(self, relation_info: str) -> None:
        """设置当前关系状态

        Args:
            relation_info: 要设置的关系状态
        """
        self.data["relation_info"] = relation_info

    def get_processed_info(self) -> str:
        """获取处理后的信息

        Returns:
            str: 处理后的信息
        """
        return self.get_relation_info() or ""
