from dataclasses import dataclass
from typing import List


@dataclass
class Identity:
    """身份特征类"""

    identity_detail: List[str]  # 身份细节描述

    def __init__(self, identity_detail: List[str] = None):
        """初始化身份特征

        Args:
            identity_detail: 身份细节描述列表
        """
        if identity_detail is None:
            identity_detail = []
        self.identity_detail = identity_detail

    def to_dict(self) -> dict:
        """将身份特征转换为字典格式"""
        return {
            "identity_detail": self.identity_detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Identity":
        """从字典创建身份特征实例"""
        return cls(identity_detail=data.get("identity_detail", []))
