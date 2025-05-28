from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field


@dataclass
class StructuredInfo:
    """信息基类

    这是一个基础信息类，用于存储和管理各种类型的信息数据。
    所有具体的信息类都应该继承自这个基类。

    Attributes:
        type (str): 信息类型标识符，默认为 "base"
        data (Dict[str, Union[str, Dict, list]]): 存储具体信息数据的字典，
            支持存储字符串、字典、列表等嵌套数据结构
    """

    type: str = "structured_info"
    data: Dict[str, Any] = field(default_factory=dict)

    def get_type(self) -> str:
        """获取信息类型

        Returns:
            str: 当前信息对象的类型标识符
        """
        return self.type

    def get_data(self) -> Dict[str, Any]:
        """获取所有信息数据

        Returns:
            Dict[str, Any]: 包含所有信息数据的字典
        """
        return self.data

    def get_info(self, key: str) -> Optional[Any]:
        """获取特定属性的信息

        Args:
            key: 要获取的属性键名

        Returns:
            Optional[Any]: 属性值，如果键不存在则返回 None
        """
        return self.data.get(key)

    def get_info_list(self, key: str) -> List[Any]:
        """获取特定属性的信息列表

        Args:
            key: 要获取的属性键名

        Returns:
            List[Any]: 属性值列表，如果键不存在则返回空列表
        """
        value = self.data.get(key)
        if isinstance(value, list):
            return value
        return []

    def set_info(self, key: str, value: Any) -> None:
        """设置特定属性的信息值

        Args:
            key: 要设置的属性键名
            value: 要设置的属性值
        """
        self.data[key] = value

    def get_processed_info(self) -> str:
        """获取处理后的信息

        Returns:
            str: 处理后的信息字符串
        """

        info_str = ""
        # print(f"self.data: {self.data}")

        for key, value in self.data.items():
            # print(f"key: {key}, value: {value}")
            info_str += f"信息类型：{key}，信息内容：{value}\n"

        return info_str
