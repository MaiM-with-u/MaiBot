import jsonlines
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from src.config import global_config as config


class DataLoader:
    """数据加载工具类，用于从/data目录下加载各种格式的数据文件"""

    def __init__(self, custom_data_dir: Optional[Union[str, Path]] = None):
        """
        初始化数据加载器

        Args:
            custom_data_dir: 可选的自定义数据目录路径，如果不提供则使用配置文件中的默认路径
        """
        self.data_dir = (
            Path(custom_data_dir)
            if custom_data_dir
            else Path(config["persistence"]["data_root_path"])
        )
        if not self.data_dir.exists():
            raise FileNotFoundError(f"数据目录 {self.data_dir} 不存在")

    def _resolve_file_path(self, filename: str) -> Path:
        """
        解析文件路径

        Args:
            filename: 文件名

        Returns:
            完整的文件路径

        Raises:
            FileNotFoundError: 当文件不存在时抛出
        """
        file_path = self.data_dir / filename
        if not file_path.exists():
            raise FileNotFoundError(f"文件 {filename} 不存在")
        return file_path

    def load_jsonl(self, filename: str) -> List[Dict[str, Any]]:
        """
        加载JSONL格式的文件

        Args:
            filename: 文件名

        Returns:
            包含所有数据的列表
        """
        file_path = self._resolve_file_path(filename)
        data = []
        with jsonlines.open(file_path) as reader:
            for obj in reader:
                data.append(obj)
        return data
