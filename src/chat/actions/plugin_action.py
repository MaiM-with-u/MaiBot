import traceback
from typing import Tuple, Dict, List, Any, Optional, Union, Type
from src.chat.actions.base_action import BaseAction, register_action, ActionActivationType, ChatMode  # noqa F401
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message
from src.common.logger_manager import get_logger
from src.config.config import global_config
import os
import inspect
import toml  # 导入 toml 库
from abc import abstractmethod

# 导入拆分后的API模块
from src.chat.actions.plugin_api.message_api import MessageAPI
from src.chat.actions.plugin_api.llm_api import LLMAPI
from src.chat.actions.plugin_api.database_api import DatabaseAPI
from src.chat.actions.plugin_api.config_api import ConfigAPI
from src.chat.actions.plugin_api.utils_api import UtilsAPI

# 以下为类型注解需要
from src.chat.message_receive.chat_stream import ChatStream # noqa
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor # noqa
from src.chat.focus_chat.replyer.default_replyer import DefaultReplyer # noqa
from src.chat.focus_chat.info.obs_info import ObsInfo # noqa

logger = get_logger("plugin_action")


class PluginAction(BaseAction, MessageAPI, LLMAPI, DatabaseAPI, ConfigAPI, UtilsAPI):
    """插件动作基类

    封装了主程序内部依赖，提供简化的API接口给插件开发者
    """

    action_config_file_name: Optional[str] = None  # 插件可以覆盖此属性来指定配置文件名
    
    # 默认激活类型设置，插件可以覆盖
    focus_activation_type = ActionActivationType.ALWAYS
    normal_activation_type = ActionActivationType.ALWAYS
    random_activation_probability: float = 0.3
    llm_judge_prompt: str = ""
    activation_keywords: list[str] = []
    keyword_case_sensitive: bool = False
    
    # 默认模式启用设置 - 插件动作默认在所有模式下可用，插件可以覆盖
    mode_enable = ChatMode.ALL

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        global_config: Optional[dict] = None,
        **kwargs,
    ):
        """初始化插件动作基类"""
        super().__init__(action_data, reasoning, cycle_timers, thinking_id)

        # 存储内部服务和对象引用
        self._services = {}
        self.config: Dict[str, Any] = {}  # 用于存储插件自身的配置

        # 从kwargs提取必要的内部服务
        if "observations" in kwargs:
            self._services["observations"] = kwargs["observations"]
        if "expressor" in kwargs:
            self._services["expressor"] = kwargs["expressor"]
        if "chat_stream" in kwargs:
            self._services["chat_stream"] = kwargs["chat_stream"]
        if "replyer" in kwargs:
            self._services["replyer"] = kwargs["replyer"]

        self.log_prefix = kwargs.get("log_prefix", "")
        self._load_plugin_config()  # 初始化时加载插件配置

    def _load_plugin_config(self):
        """
        加载插件自身的配置文件。
        配置文件应与插件模块在同一目录下。
        插件可以通过覆盖 `action_config_file_name` 类属性来指定文件名。
        如果 `action_config_file_name` 未指定，则不加载配置。
        仅支持 TOML (.toml) 格式。
        """
        if not self.action_config_file_name:
            logger.debug(
                f"{self.log_prefix} 插件 {self.__class__.__name__} 未指定 action_config_file_name，不加载插件配置。"
            )
            return

        try:
            plugin_module_path = inspect.getfile(self.__class__)
            plugin_dir = os.path.dirname(plugin_module_path)
            config_file_path = os.path.join(plugin_dir, self.action_config_file_name)

            if not os.path.exists(config_file_path):
                logger.warning(
                    f"{self.log_prefix} 插件 {self.__class__.__name__} 的配置文件 {config_file_path} 不存在。"
                )
                return

            file_ext = os.path.splitext(self.action_config_file_name)[1].lower()

            if file_ext == ".toml":
                with open(config_file_path, "r", encoding="utf-8") as f:
                    self.config = toml.load(f) or {}
                logger.info(f"{self.log_prefix} 插件 {self.__class__.__name__} 的配置已从 {config_file_path} 加载。")
            else:
                logger.warning(
                    f"{self.log_prefix} 不支持的插件配置文件格式: {file_ext}。仅支持 .toml。插件配置未加载。"
                )
                self.config = {}  # 确保未加载时为空字典
                return

        except Exception as e:
            logger.error(
                f"{self.log_prefix} 加载插件 {self.__class__.__name__} 的配置文件 {self.action_config_file_name} 时出错: {e}"
            )
            self.config = {}  # 出错时确保 config 是一个空字典

    @abstractmethod
    async def process(self) -> Tuple[bool, str]:
        """插件处理逻辑，子类必须实现此方法

        Returns:
            Tuple[bool, str]: (是否执行成功, 回复文本)
        """
        pass

    async def handle_action(self) -> Tuple[bool, str]:
        """实现BaseAction的抽象方法，调用子类的process方法

        Returns:
            Tuple[bool, str]: (是否执行成功, 回复文本)
        """
        return await self.process()
