import time
import asyncio

from abc import ABC, abstractmethod
from typing import Tuple, Optional, TYPE_CHECKING

from src.common.logger import get_logger
from src.chat.message_receive.chat_stream import ChatStream
from src.plugin_system.base.component_types import ActionActivationType, ActionInfo, ComponentType
from src.plugin_system.apis import send_api, database_api, message_api

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("base_action")


class BaseAction(ABC):
    """Action组件基类

    Action是插件的一种组件类型，用于处理聊天中的动作逻辑

    子类可以通过类属性定义激活条件，这些会在实例化时转换为实例属性：
    - focus_activation_type: 专注模式激活类型
    - normal_activation_type: 普通模式激活类型
    - activation_keywords: 激活关键词列表
    - keyword_case_sensitive: 关键词是否区分大小写
    - parallel_action: 是否允许并行执行
    - random_activation_probability: 随机激活概率
    - llm_judge_prompt: LLM判断提示词
    """

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        chat_stream: ChatStream,
        log_prefix: str = "",
        plugin_config: Optional[dict] = None,
        action_message: Optional[dict] = None,
        **kwargs,
    ):
        # sourcery skip: hoist-similar-statement-from-if, merge-else-if-into-elif, move-assign-in-block, swap-if-else-branches, swap-nested-ifs
        """初始化Action组件

        Args:
            action_data: 动作数据
            reasoning: 执行该动作的理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            chat_stream: 聊天流对象
            log_prefix: 日志前缀
            plugin_config: 插件配置字典
            action_message: 消息数据
            **kwargs: 其他参数
        """
        if plugin_config is None:
            plugin_config = {}
        self.action_data = action_data
        self.reasoning = reasoning
        self.cycle_timers = cycle_timers
        self.thinking_id = thinking_id
        self.log_prefix = log_prefix

        self.plugin_config = plugin_config or {}
        """对应的插件配置"""

        # 设置动作基本信息实例属性
        self.action_name: str = getattr(self, "action_name", self.__class__.__name__.lower().replace("action", ""))
        """Action的名字"""
        self.action_description: str = getattr(self, "action_description", self.__doc__ or "Action组件")
        """Action的描述"""
        self.action_parameters: dict = getattr(self.__class__, "action_parameters", {}).copy()
        self.action_require: list[str] = getattr(self.__class__, "action_require", []).copy()

        # 设置激活类型实例属性（从类属性复制，提供默认值）
        self.focus_activation_type = getattr(self.__class__, "focus_activation_type", ActionActivationType.ALWAYS) #已弃用
        """FOCUS模式下的激活类型"""
        self.normal_activation_type = getattr(self.__class__, "normal_activation_type", ActionActivationType.ALWAYS) #已弃用
        """NORMAL模式下的激活类型"""
        self.activation_type = getattr(self.__class__, "activation_type", self.focus_activation_type)
        """激活类型"""
        self.random_activation_probability: float = getattr(self.__class__, "random_activation_probability", 0.0)
        """当激活类型为RANDOM时的概率"""
        self.llm_judge_prompt: str = getattr(self.__class__, "llm_judge_prompt", "") #已弃用
        """协助LLM进行判断的Prompt"""
        self.activation_keywords: list[str] = getattr(self.__class__, "activation_keywords", []).copy()
        """激活类型为KEYWORD时的KEYWORDS列表"""
        self.keyword_case_sensitive: bool = getattr(self.__class__, "keyword_case_sensitive", False)
        self.parallel_action: bool = getattr(self.__class__, "parallel_action", True)
        self.associated_types: list[str] = getattr(self.__class__, "associated_types", []).copy()

        # =============================================================================
        # 便捷属性 - 直接在初始化时获取常用聊天信息（带类型注解）
        # =============================================================================

        # 获取聊天流对象
        self.chat_stream = chat_stream or kwargs.get("chat_stream")
        self.chat_id = self.chat_stream.stream_id
        self.platform = getattr(self.chat_stream, "platform", None)

        # 初始化基础信息（带类型注解）
        self.action_message = action_message

        self.group_id = None
        self.group_name = None
        self.user_id = None
        self.user_nickname = None
        self.is_group = False
        self.target_id = None
        self.has_action_message = False

        if self.action_message:
            self.has_action_message = True
        else:
            self.action_message = {}

        if self.has_action_message:
            if self.action_name != "no_action":
                self.group_id = str(self.action_message.get("chat_info_group_id", None))
                self.group_name = self.action_message.get("chat_info_group_name", None)

                self.user_id = str(self.action_message.get("user_id", None))
                self.user_nickname = self.action_message.get("user_nickname", None)
                if self.group_id:
                    self.is_group = True
                    self.target_id = self.group_id
                else:
                    self.is_group = False
                    self.target_id = self.user_id
            else:
                if self.chat_stream.group_info:
                    self.group_id = self.chat_stream.group_info.group_id
                    self.group_name = self.chat_stream.group_info.group_name
                    self.is_group = True
                    self.target_id = self.group_id
                else:
                    self.user_id = self.chat_stream.user_info.user_id
                    self.user_nickname = self.chat_stream.user_info.user_nickname
                    self.is_group = False
                    self.target_id = self.user_id

        logger.debug(f"{self.log_prefix} Action组件初始化完成")
        logger.debug(
            f"{self.log_prefix} 聊天信息: 类型={'群聊' if self.is_group else '私聊'}, 平台={self.platform}, 目标={self.target_id}"
        )

    async def wait_for_new_message(self, timeout: int = 1200) -> Tuple[bool, str]:
        """等待新消息或超时

        在loop_start_time之后等待新消息，如果没有新消息且没有超时，就一直等待。
        使用message_api检查self.chat_id对应的聊天中是否有新消息。

        Args:
            timeout: 超时时间（秒），默认1200秒

        Returns:
            Tuple[bool, str]: (是否收到新消息, 空字符串)
        """
        try:
            # 获取循环开始时间，如果没有则使用当前时间
            loop_start_time = self.action_data.get("loop_start_time", time.time())
            logger.info(f"{self.log_prefix} 开始等待新消息... (最长等待: {timeout}秒, 从时间点: {loop_start_time})")

            # 确保有有效的chat_id
            if not self.chat_id:
                logger.error(f"{self.log_prefix} 等待新消息失败: 没有有效的chat_id")
                return False, "没有有效的chat_id"

            wait_start_time = asyncio.get_event_loop().time()
            while True:
                # 检查关闭标志
                # shutting_down = self.get_action_context("shutting_down", False)
                # if shutting_down:
                # logger.info(f"{self.log_prefix} 等待新消息时检测到关闭信号，中断等待")
                # return False, ""

                # 检查新消息
                current_time = time.time()
                new_message_count = message_api.count_new_messages(
                    chat_id=self.chat_id, start_time=loop_start_time, end_time=current_time
                )

                if new_message_count > 0:
                    logger.info(f"{self.log_prefix} 检测到{new_message_count}条新消息，聊天ID: {self.chat_id}")
                    return True, ""

                # 检查超时
                elapsed_time = asyncio.get_event_loop().time() - wait_start_time
                if elapsed_time > timeout:
                    logger.warning(f"{self.log_prefix} 等待新消息超时({timeout}秒)，聊天ID: {self.chat_id}")
                    return False, ""

                # 每30秒记录一次等待状态
                if int(elapsed_time) % 15 == 0 and int(elapsed_time) > 0:
                    logger.debug(f"{self.log_prefix} 已等待{int(elapsed_time)}秒，继续等待新消息...")

                # 短暂休眠
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} 等待新消息被中断 (CancelledError)")
            return False, ""
        except Exception as e:
            logger.error(f"{self.log_prefix} 等待新消息时发生错误: {e}")
            return False, f"等待新消息失败: {str(e)}"

    async def send_text(
        self,
        content: str,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
        typing: bool = False,
    ) -> bool:
        """发送文本消息

        Args:
            content: 文本内容
            reply_to: 回复消息，格式为"发送者:消息内容"

        Returns:
            bool: 是否发送成功
        """
        if not self.chat_id:
            logger.error(f"{self.log_prefix} 缺少聊天ID")
            return False

        return await send_api.text_to_stream(
            text=content,
            stream_id=self.chat_id,
            set_reply=set_reply,
            reply_message=reply_message,
            typing=typing,
        )

    async def send_emoji(
        self, emoji_base64: str, set_reply: bool = False, reply_message: Optional["DatabaseMessages"] = None
    ) -> bool:
        """发送表情包

        Args:
            emoji_base64: 表情包的base64编码

        Returns:
            bool: 是否发送成功
        """
        if not self.chat_id:
            logger.error(f"{self.log_prefix} 缺少聊天ID")
            return False

        return await send_api.emoji_to_stream(
            emoji_base64, self.chat_id, set_reply=set_reply, reply_message=reply_message
        )

    async def send_image(
        self, image_base64: str, set_reply: bool = False, reply_message: Optional["DatabaseMessages"] = None
    ) -> bool:
        """发送图片

        Args:
            image_base64: 图片的base64编码

        Returns:
            bool: 是否发送成功
        """
        if not self.chat_id:
            logger.error(f"{self.log_prefix} 缺少聊天ID")
            return False

        return await send_api.image_to_stream(
            image_base64, self.chat_id, set_reply=set_reply, reply_message=reply_message
        )

    async def send_custom(
        self,
        message_type: str,
        content: str,
        typing: bool = False,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
    ) -> bool:
        """发送自定义类型消息

        Args:
            message_type: 消息类型，如"video"、"file"、"audio"等
            content: 消息内容
            typing: 是否显示正在输入
            reply_to: 回复消息，格式为"发送者:消息内容"

        Returns:
            bool: 是否发送成功
        """
        if not self.chat_id:
            logger.error(f"{self.log_prefix} 缺少聊天ID")
            return False

        return await send_api.custom_to_stream(
            message_type=message_type,
            content=content,
            stream_id=self.chat_id,
            typing=typing,
            set_reply=set_reply,
            reply_message=reply_message,
        )

    async def store_action_info(
        self,
        action_build_into_prompt: bool = False,
        action_prompt_display: str = "",
        action_done: bool = True,
    ) -> None:
        """存储动作信息到数据库

        Args:
            action_build_into_prompt: 是否构建到提示中
            action_prompt_display: 显示的action提示信息
            action_done: action是否完成
        """
        await database_api.store_action_info(
            chat_stream=self.chat_stream,
            action_build_into_prompt=action_build_into_prompt,
            action_prompt_display=action_prompt_display,
            action_done=action_done,
            thinking_id=self.thinking_id,
            action_data=self.action_data,
            action_name=self.action_name,
        )

    async def send_command(
        self,
        command_name: str,
        args: Optional[dict] = None,
        display_message: str = "",
        storage_message: bool = True,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
    ) -> bool:
        """发送命令消息

        使用stream API发送命令

        Args:
            command_name: 命令名称
            args: 命令参数
            display_message: 显示消息
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        try:
            if not self.chat_id:
                logger.error(f"{self.log_prefix} 缺少聊天ID")
                return False

            # 构造命令数据
            command_data = {"name": command_name, "args": args or {}}

            success = await send_api.command_to_stream(
                command=command_data,
                stream_id=self.chat_id,
                storage_message=storage_message,
                display_message=display_message,
                set_reply=set_reply,
                reply_message=reply_message,
            )

            if success:
                logger.info(f"{self.log_prefix} 成功发送命令: {command_name}")
            else:
                logger.error(f"{self.log_prefix} 发送命令失败: {command_name}")

            return success

        except Exception as e:
            logger.error(f"{self.log_prefix} 发送命令时出错: {e}")
            return False

    @classmethod
    def get_action_info(cls) -> "ActionInfo":
        """从类属性生成ActionInfo

        所有信息都从类属性中读取，确保一致性和完整性。
        Action类必须定义所有必要的类属性。

        Returns:
            ActionInfo: 生成的Action信息对象
        """

        # 从类属性读取名称，如果没有定义则使用类名自动生成
        name = getattr(cls, "action_name", cls.__name__.lower().replace("action", ""))
        if "." in name:
            logger.error(f"Action名称 '{name}' 包含非法字符 '.'，请使用下划线替代")
            raise ValueError(f"Action名称 '{name}' 包含非法字符 '.'，请使用下划线替代")
        # 获取focus_activation_type和normal_activation_type
        focus_activation_type = getattr(cls, "focus_activation_type", ActionActivationType.ALWAYS)
        normal_activation_type = getattr(cls, "normal_activation_type", ActionActivationType.ALWAYS)

        # 处理activation_type：如果插件中声明了就用插件的值，否则默认使用focus_activation_type
        activation_type = getattr(cls, "activation_type", focus_activation_type)

        return ActionInfo(
            name=name,
            component_type=ComponentType.ACTION,
            description=getattr(cls, "action_description", "Action动作"),
            focus_activation_type=focus_activation_type,
            normal_activation_type=normal_activation_type,
            activation_type=activation_type,
            activation_keywords=getattr(cls, "activation_keywords", []).copy(),
            keyword_case_sensitive=getattr(cls, "keyword_case_sensitive", False),
            parallel_action=getattr(cls, "parallel_action", True),
            random_activation_probability=getattr(cls, "random_activation_probability", 0.0),
            llm_judge_prompt=getattr(cls, "llm_judge_prompt", ""),
            # 使用正确的字段名
            action_parameters=getattr(cls, "action_parameters", {}).copy(),
            action_require=getattr(cls, "action_require", []).copy(),
            associated_types=getattr(cls, "associated_types", []).copy(),
        )

    @abstractmethod
    async def execute(self) -> Tuple[bool, str]:
        """执行Action的抽象方法，子类必须实现

        Returns:
            Tuple[bool, str]: (是否执行成功, 回复文本)
        """
        pass

    async def handle_action(self) -> Tuple[bool, str]:
        """兼容旧系统的handle_action接口，委托给execute方法

        为了保持向后兼容性，旧系统的代码可能会调用handle_action方法。
        此方法将调用委托给新的execute方法。

        Returns:
            Tuple[bool, str]: (是否执行成功, 回复文本)
        """
        return await self.execute()

    def get_config(self, key: str, default=None):
        """获取插件配置值，使用嵌套键访问

        Args:
            key: 配置键名，使用嵌套访问如 "section.subsection.key"
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
