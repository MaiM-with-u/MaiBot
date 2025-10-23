from dataclasses import dataclass, field
from typing import List, Optional, Set

from .config_base import ConfigBase


@dataclass
class APIProvider(ConfigBase):
    """API提供商配置类"""

    name: str
    """API提供商名称"""

    base_url: str
    """API基础URL"""

    api_key: str | List[str] = field(default_factory=str, repr=False)
    """API密钥（兼容字符串或字符串列表）"""

    api_keys: List[str] = field(default_factory=list, repr=False)
    """API密钥优先级列表（可选，覆盖单个api_key设置）"""

    client_type: str = field(default="openai")
    """客户端类型（如openai/google等，默认为openai）"""

    max_retry: int = 2
    """最大重试次数（单个模型API调用失败，最多重试的次数）"""

    timeout: int = 10
    """API调用的超时时长（超过这个时长，本次请求将被视为“请求超时”，单位：秒）"""

    retry_interval: int = 10
    """重试间隔（如果API调用失败，重试的间隔时间，单位：秒）"""

    _ordered_keys: List[str] = field(init=False, repr=False, default_factory=list)
    _key_index: int = field(init=False, repr=False, default=0)

    def get_api_key(self) -> str:
        """返回当前生效的API Key"""
        return self._ordered_keys[self._key_index]

    def rotate_api_key(self, exclude: Optional[Set[str]] = None) -> Optional[str]:
        """切换到下一枚可用的API Key，返回新Key；若无可切换则返回None"""
        if len(self._ordered_keys) <= 1:
            return None

        original_index = self._key_index
        key_count = len(self._ordered_keys)

        for _ in range(1, key_count):
            self._key_index = (self._key_index + 1) % key_count
            candidate = self._ordered_keys[self._key_index]
            if exclude and candidate in exclude:
                continue
            self.api_key = candidate
            return candidate

        # 无可用Key，回退到原位置
        self._key_index = original_index
        self.api_key = self._ordered_keys[self._key_index]
        return None

    def __post_init__(self):
        """确保api_key在repr中不被显示"""
        raw_keys: List[str] = []

        def _collect_keys(value):
            if not value:
                return
            if isinstance(value, str):
                # 支持逗号或换行分隔
                parts = [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
                raw_keys.extend(parts)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        raw_keys.append(item.strip())

        _collect_keys(self.api_key)
        _collect_keys(self.api_keys)

        if not raw_keys:
            raise ValueError("API密钥不能为空，请在配置中设置有效的API密钥。")

        # 按顺序去重
        ordered_keys: List[str] = []
        seen: Set[str] = set()
        for key in raw_keys:
            if key not in seen:
                ordered_keys.append(key)
                seen.add(key)

        self._ordered_keys = ordered_keys
        self._key_index = 0
        self.api_keys = ordered_keys
        self.api_key = ordered_keys[0]

        if not self.base_url and self.client_type != "gemini":
            raise ValueError("API基础URL不能为空，请在配置中设置有效的基础URL。")
        if not self.name:
            raise ValueError("API提供商名称不能为空，请在配置中设置有效的名称。")


@dataclass
class ModelInfo(ConfigBase):
    """单个模型信息配置类"""

    model_identifier: str
    """模型标识符（用于URL调用）"""

    name: str
    """模型名称（用于模块调用）"""

    api_provider: str
    """API提供商（如OpenAI、Azure等）"""

    price_in: float = field(default=0.0)
    """每M token输入价格"""

    price_out: float = field(default=0.0)
    """每M token输出价格"""

    force_stream_mode: bool = field(default=False)
    """是否强制使用流式输出模式"""

    extra_params: dict = field(default_factory=dict)
    """额外参数（用于API调用时的额外配置）"""

    def __post_init__(self):
        if not self.model_identifier:
            raise ValueError("模型标识符不能为空，请在配置中设置有效的模型标识符。")
        if not self.name:
            raise ValueError("模型名称不能为空，请在配置中设置有效的模型名称。")
        if not self.api_provider:
            raise ValueError("API提供商不能为空，请在配置中设置有效的API提供商。")


@dataclass
class TaskConfig(ConfigBase):
    """任务配置类"""

    model_list: list[str] = field(default_factory=list)
    """任务使用的模型列表"""

    max_tokens: int = 1024
    """任务最大输出token数"""

    temperature: float = 0.3
    """模型温度"""


@dataclass
class ModelTaskConfig(ConfigBase):
    """模型配置类"""

    utils: TaskConfig
    """组件模型配置"""

    utils_small: TaskConfig
    """组件小模型配置"""

    replyer: TaskConfig
    """normal_chat首要回复模型模型配置"""

    vlm: TaskConfig
    """视觉语言模型配置"""

    voice: TaskConfig
    """语音识别模型配置"""

    tool_use: TaskConfig
    """专注工具使用模型配置"""

    planner: TaskConfig
    """规划模型配置"""

    embedding: TaskConfig
    """嵌入模型配置"""

    lpmm_entity_extract: TaskConfig
    """LPMM实体提取模型配置"""

    lpmm_rdf_build: TaskConfig
    """LPMM RDF构建模型配置"""

    lpmm_qa: TaskConfig
    """LPMM问答模型配置"""

    def get_task(self, task_name: str) -> TaskConfig:
        """获取指定任务的配置"""
        if hasattr(self, task_name):
            return getattr(self, task_name)
        raise ValueError(f"任务 '{task_name}' 未找到对应的配置")
