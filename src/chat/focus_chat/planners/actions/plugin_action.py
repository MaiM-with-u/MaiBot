import traceback
from typing import Tuple, Dict, List, Any, Optional
from src.chat.focus_chat.planners.actions.base_action import BaseAction, register_action  # noqa F401
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.person_info.person_info import person_info_manager
from abc import abstractmethod
from src.config.config import global_config
import os
import inspect
import toml  # 导入 toml 库

logger = get_logger("plugin_action")


class PluginAction(BaseAction):
    """插件动作基类

    封装了主程序内部依赖，提供简化的API接口给插件开发者
    """

    action_config_file_name: Optional[str] = None  # 插件可以覆盖此属性来指定配置文件名

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

    def get_global_config(self, key: str, default: Any = None) -> Any:
        """
        安全地从全局配置中获取一个值。
        插件应使用此方法读取全局配置，以保证只读和隔离性。
        """

        return global_config.get(key, default)

    async def get_user_id_by_person_name(self, person_name: str) -> Tuple[str, str]:
        """根据用户名获取用户ID"""
        person_id = person_info_manager.get_person_id_by_person_name(person_name)
        user_id = await person_info_manager.get_value(person_id, "user_id")
        platform = await person_info_manager.get_value(person_id, "platform")
        return platform, user_id

    # 提供简化的API方法
    async def send_message(self, type: str, data: str, target: Optional[str] = "", display_message: str = "") -> bool:
        """发送消息的简化方法

        Args:
            text: 要发送的消息文本
            target: 目标消息（可选）

        Returns:
            bool: 是否发送成功
        """
        try:
            expressor = self._services.get("expressor")
            chat_stream = self._services.get("chat_stream")

            if not expressor or not chat_stream:
                logger.error(f"{self.log_prefix} 无法发送消息：缺少必要的内部服务")
                return False

            # 构造简化的动作数据
            # reply_data = {"text": text, "target": target or "", "emojis": []}

            # 获取锚定消息（如果有）
            observations = self._services.get("observations", [])

            if len(observations) > 0:
                chatting_observation: ChattingObservation = next(
                    obs for obs in observations if isinstance(obs, ChattingObservation)
                )

                anchor_message = chatting_observation.search_message_by_text(target)
            else:
                anchor_message = None

            # 如果没有找到锚点消息，创建一个占位符
            if not anchor_message:
                logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
                anchor_message = await create_empty_anchor_message(
                    chat_stream.platform, chat_stream.group_info, chat_stream
                )
            else:
                anchor_message.update_chat_stream(chat_stream)

            response_set = [
                (type, data),
            ]

            # 调用内部方法发送消息
            success = await expressor.send_response_messages(
                anchor_message=anchor_message,
                response_set=response_set,
                display_message=display_message,
            )

            return success
        except Exception as e:
            logger.error(f"{self.log_prefix} 发送消息时出错: {e}")
            traceback.print_exc()
            return False

    async def send_message_by_expressor(self, text: str, target: Optional[str] = None) -> bool:
        """发送消息的简化方法

        Args:
            text: 要发送的消息文本
            target: 目标消息（可选）

        Returns:
            bool: 是否发送成功
        """
        expressor = self._services.get("expressor")
        chat_stream = self._services.get("chat_stream")

        if not expressor or not chat_stream:
            logger.error(f"{self.log_prefix} 无法发送消息：缺少必要的内部服务")
            return False

        # 构造简化的动作数据
        reply_data = {"text": text, "target": target or "", "emojis": []}

        # 获取锚定消息（如果有）
        observations = self._services.get("observations", [])

        # 查找 ChattingObservation 实例
        chatting_observation = None
        for obs in observations:
            if isinstance(obs, ChattingObservation):
                chatting_observation = obs
                break

        if not chatting_observation:
            logger.warning(f"{self.log_prefix} 未找到 ChattingObservation 实例，创建占位符")
            anchor_message = await create_empty_anchor_message(
                chat_stream.platform, chat_stream.group_info, chat_stream
            )
        else:
            anchor_message = chatting_observation.search_message_by_text(reply_data["target"])
            if not anchor_message:
                logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
                anchor_message = await create_empty_anchor_message(
                    chat_stream.platform, chat_stream.group_info, chat_stream
                )
            else:
                anchor_message.update_chat_stream(chat_stream)

        # 调用内部方法发送消息
        success, _ = await expressor.deal_reply(
            cycle_timers=self.cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            reasoning=self.reasoning,
            thinking_id=self.thinking_id,
        )

        return success

    async def send_message_by_replyer(self, target: Optional[str] = None, extra_info_block: Optional[str] = None) -> bool:
        """通过 replyer 发送消息的简化方法

        Args:
            text: 要发送的消息文本
            target: 目标消息（可选）

        Returns:
            bool: 是否发送成功
        """
        replyer = self._services.get("replyer")
        chat_stream = self._services.get("chat_stream")

        if not replyer or not chat_stream:
            logger.error(f"{self.log_prefix} 无法发送消息：缺少必要的内部服务")
            return False

        # 构造简化的动作数据
        reply_data = {"target": target or "", "extra_info_block": extra_info_block}

        # 获取锚定消息（如果有）
        observations = self._services.get("observations", [])

        # 查找 ChattingObservation 实例
        chatting_observation = None
        for obs in observations:
            if isinstance(obs, ChattingObservation):
                chatting_observation = obs
                break

        if not chatting_observation:
            logger.warning(f"{self.log_prefix} 未找到 ChattingObservation 实例，创建占位符")
            anchor_message = await create_empty_anchor_message(
                chat_stream.platform, chat_stream.group_info, chat_stream
            )
        else:
            anchor_message = chatting_observation.search_message_by_text(reply_data["target"])
            if not anchor_message:
                logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
                anchor_message = await create_empty_anchor_message(
                    chat_stream.platform, chat_stream.group_info, chat_stream
                )
            else:
                anchor_message.update_chat_stream(chat_stream)

        # 调用内部方法发送消息
        success, _ = await replyer.deal_reply(
            cycle_timers=self.cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            reasoning=self.reasoning,
            thinking_id=self.thinking_id,
        )

        return success

    def get_chat_type(self) -> str:
        """获取当前聊天类型

        Returns:
            str: 聊天类型 ("group" 或 "private")
        """
        chat_stream = self._services.get("chat_stream")
        if chat_stream and hasattr(chat_stream, "group_info"):
            return "group" if chat_stream.group_info else "private"
        return "unknown"

    def get_recent_messages(self, count: int = 5) -> List[Dict[str, Any]]:
        """获取最近的消息

        Args:
            count: 要获取的消息数量

        Returns:
            List[Dict]: 消息列表，每个消息包含发送者、内容等信息
        """
        messages = []
        observations = self._services.get("observations", [])

        if observations and len(observations) > 0:
            obs = observations[0]
            if hasattr(obs, "get_talking_message"):
                raw_messages = obs.get_talking_message()
                # 转换为简化格式
                for msg in raw_messages[-count:]:
                    simple_msg = {
                        "sender": msg.get("sender", "未知"),
                        "content": msg.get("content", ""),
                        "timestamp": msg.get("timestamp", 0),
                    }
                    messages.append(simple_msg)

        return messages

    def get_available_models(self) -> Dict[str, Any]:
        """获取所有可用的模型配置

        Returns:
            Dict[str, Any]: 模型配置字典，key为模型名称，value为模型配置
        """
        if not hasattr(global_config, "model"):
            logger.error(f"{self.log_prefix} 无法获取模型列表：全局配置中未找到 model 配置")
            return {}
        
        models = global_config.model
            
        return models

    async def generate_with_model(
        self,
        prompt: str,
        model_config: Dict[str, Any],
        max_tokens: int = 2000,
        request_type: str = "plugin.generate",
        **kwargs
    ) -> Tuple[bool, str]:
        """使用指定模型生成内容

        Args:
            prompt: 提示词
            model_config: 模型配置（从 get_available_models 获取的模型配置）
            temperature: 温度参数，控制随机性 (0-1)
            max_tokens: 最大生成token数
            request_type: 请求类型标识
            **kwargs: 其他模型特定参数

        Returns:
            Tuple[bool, str]: (是否成功, 生成的内容或错误信息)
        """
        try:
            
            
            logger.info(f"prompt: {prompt}")
            
            llm_request = LLMRequest(
                model=model_config,
                max_tokens=max_tokens,
                request_type=request_type,
                **kwargs
            )
            
            response,(resoning , model_name) = await llm_request.generate_response_async(prompt)
            return True, response, resoning, model_name
        except Exception as e:
            error_msg = f"生成内容时出错: {str(e)}"
            logger.error(f"{self.log_prefix} {error_msg}")
            return False, error_msg

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
