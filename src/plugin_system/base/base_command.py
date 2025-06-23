from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional, List
from src.common.logger import get_logger
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.plugin_system.apis import send_api

logger = get_logger("base_command")


class BaseCommand(ABC):
    """Command组件基类

    Command是插件的一种组件类型，用于处理命令请求

    子类可以通过类属性定义命令模式：
    - command_pattern: 命令匹配的正则表达式
    - command_help: 命令帮助信息
    - command_examples: 命令使用示例列表
    - intercept_message: 是否拦截消息处理（默认True拦截，False继续传递）
    """

    command_name: str = ""
    command_description: str = ""

    # 默认命令设置（子类可以覆盖）
    command_pattern: str = ""
    command_help: str = ""
    command_examples: List[str] = []
    intercept_message: bool = True  # 默认拦截消息，不继续处理

    def __init__(self, message: MessageRecv, plugin_config: dict = None):
        """初始化Command组件

        Args:
            message: 接收到的消息对象
            plugin_config: 插件配置字典
        """
        self.message = message
        self.matched_groups: Dict[str, str] = {}  # 存储正则表达式匹配的命名组
        self.plugin_config = plugin_config or {}  # 直接存储插件配置字典

        self.log_prefix = "[Command]"

        logger.debug(f"{self.log_prefix} Command组件初始化完成")

    def set_matched_groups(self, groups: Dict[str, str]) -> None:
        """设置正则表达式匹配的命名组

        Args:
            groups: 正则表达式匹配的命名组
        """
        self.matched_groups = groups

    @abstractmethod
    async def execute(self) -> Tuple[bool, Optional[str]]:
        """执行Command的抽象方法，子类必须实现

        Returns:
            Tuple[bool, Optional[str]]: (是否执行成功, 可选的回复消息)
        """
        pass

    def get_config(self, key: str, default=None):
        """获取插件配置值，支持嵌套键访问

        Args:
            key: 配置键名，支持嵌套访问如 "section.subsection.key"
            default: 默认值

        Returns:
            Any: 配置值或默认值
        """
        if not self.plugin_config:
            return default

        # 支持嵌套键访问
        keys = key.split(".")
        current = self.plugin_config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current

    async def send_text(self, content: str, reply_to: str = "") -> bool:
        """发送回复消息

        Args:
            content: 回复内容
            reply_to: 回复消息，格式为"发送者:消息内容"

        Returns:
            bool: 是否发送成功
        """
        # 获取聊天流信息
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.text_to_stream(text=content, stream_id=chat_stream.stream_id, reply_to=reply_to)

    async def send_type(
        self, message_type: str, content: str, display_message: str = "", typing: bool = False, reply_to: str = ""
    ) -> bool:
        """发送指定类型的回复消息到当前聊天环境

        Args:
            message_type: 消息类型，如"text"、"image"、"emoji"等
            content: 消息内容
            display_message: 显示消息（可选）
            typing: 是否显示正在输入
            reply_to: 回复消息，格式为"发送者:消息内容"

        Returns:
            bool: 是否发送成功
        """
        # 获取聊天流信息
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.custom_to_stream(
            message_type=message_type,
            content=content,
            stream_id=chat_stream.stream_id,
            display_message=display_message,
            typing=typing,
            reply_to=reply_to,
        )

    async def send_command(
        self, command_name: str, args: dict = None, display_message: str = "", storage_message: bool = True
    ) -> bool:
        """发送命令消息

        Args:
            command_name: 命令名称
            args: 命令参数
            display_message: 显示消息
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        try:
            # 获取聊天流信息
            chat_stream = self.message.chat_stream
            if not chat_stream or not hasattr(chat_stream, "stream_id"):
                logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
                return False

            # 构造命令数据
            command_data = {"name": command_name, "args": args or {}}

            success = await send_api.command_to_stream(
                command=command_data,
                stream_id=chat_stream.stream_id,
                storage_message=storage_message,
                display_message=display_message
            )

            if success:
                logger.info(f"{self.log_prefix} 成功发送命令: {command_name}")
            else:
                logger.error(f"{self.log_prefix} 发送命令失败: {command_name}")

            return success

        except Exception as e:
            logger.error(f"{self.log_prefix} 发送命令时出错: {e}")
            return False

    async def send_emoji(self, emoji_base64: str) -> bool:
        """发送表情包

        Args:
            emoji_base64: 表情包的base64编码

        Returns:
            bool: 是否发送成功
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.emoji_to_stream(emoji_base64, chat_stream.stream_id)

    async def send_image(self, image_base64: str) -> bool:
        """发送图片

        Args:
            image_base64: 图片的base64编码

        Returns:
            bool: 是否发送成功
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.image_to_stream(image_base64, chat_stream.stream_id)

    @classmethod
    def get_command_info(cls) -> "CommandInfo":
        """从类属性生成CommandInfo

        Args:
            name: Command名称，如果不提供则使用类名
            description: Command描述，如果不提供则使用类文档字符串

        Returns:
            CommandInfo: 生成的Command信息对象
        """

        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern,
            command_help=cls.command_help,
            command_examples=cls.command_examples.copy() if cls.command_examples else [],
            intercept_message=cls.intercept_message,
        )
