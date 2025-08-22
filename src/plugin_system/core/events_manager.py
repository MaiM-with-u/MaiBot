import asyncio
import contextlib
from typing import List, Dict, Optional, Type, Tuple, Any, TYPE_CHECKING

from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.plugin_system.base.component_types import EventType, EventHandlerInfo, MaiMessages
from src.plugin_system.base.base_events_handler import BaseEventHandler
from .global_announcement_manager import global_announcement_manager

if TYPE_CHECKING:
    from src.common.data_models.llm_data_model import LLMGenerationDataModel

logger = get_logger("events_manager")


class EventsManager:
    def __init__(self):
        # 有权重的 events 订阅者注册表
        self._events_subscribers: Dict[EventType, List[BaseEventHandler]] = {event: [] for event in EventType}
        self._handler_mapping: Dict[str, Type[BaseEventHandler]] = {}  # 事件处理器映射表
        self._handler_tasks: Dict[str, List[asyncio.Task]] = {}  # 事件处理器正在处理的任务

    def register_event_subscriber(self, handler_info: EventHandlerInfo, handler_class: Type[BaseEventHandler]) -> bool:
        """注册事件处理器

        Args:
            handler_info (EventHandlerInfo): 事件处理器信息
            handler_class (Type[BaseEventHandler]): 事件处理器类

        Returns:
            bool: 是否注册成功
        """
        handler_name = handler_info.name

        if handler_name in self._handler_mapping:
            logger.warning(f"事件处理器 {handler_name} 已存在，跳过注册")
            return False

        if not issubclass(handler_class, BaseEventHandler):
            logger.error(f"类 {handler_class.__name__} 不是 BaseEventHandler 的子类")
            return False

        self._handler_mapping[handler_name] = handler_class
        return self._insert_event_handler(handler_class, handler_info)

    def _prepare_message(
        self,
        event_type: EventType,
        message: Optional[MessageRecv] = None,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
        stream_id: Optional[str] = None,
        action_usage: Optional[List[str]] = None,
    ) -> Optional[MaiMessages]:
        """根据事件类型和输入，准备和转换消息对象。"""
        if message:
            return self._transform_event_message(message, llm_prompt, llm_response)

        if event_type not in [EventType.ON_START, EventType.ON_STOP]:
            assert stream_id, "如果没有消息，必须为非启动/关闭事件提供流ID"
            if event_type in [EventType.ON_MESSAGE, EventType.ON_PLAN, EventType.POST_LLM, EventType.AFTER_LLM]:
                return self._build_message_from_stream(stream_id, llm_prompt, llm_response)
            else:
                return self._transform_event_without_message(stream_id, llm_prompt, llm_response, action_usage)

        return None  # ON_START, ON_STOP事件没有消息体

    def _dispatch_handler_task(self, handler: BaseEventHandler, message: Optional[MaiMessages]):
        """分发一个非阻塞（异步）的事件处理任务。"""
        try:
            task = asyncio.create_task(handler.execute(message))

            task_name = f"{handler.plugin_name}-{handler.handler_name}"
            task.set_name(task_name)
            task.add_done_callback(self._task_done_callback)

            self._handler_tasks.setdefault(handler.handler_name, []).append(task)
        except Exception as e:
            logger.error(f"创建事件处理器任务 {handler.handler_name} 时发生异常: {e}", exc_info=True)

    async def _dispatch_intercepting_handler(self, handler: BaseEventHandler, message: Optional[MaiMessages]) -> bool:
        """分发并等待一个阻塞（同步）的事件处理器，返回是否应继续处理。"""
        try:
            success, continue_processing, result = await handler.execute(message)

            if not success:
                logger.error(f"EventHandler {handler.handler_name} 执行失败: {result}")
            else:
                logger.debug(f"EventHandler {handler.handler_name} 执行成功: {result}")

            return continue_processing
        except Exception as e:
            logger.error(f"EventHandler {handler.handler_name} 发生异常: {e}", exc_info=True)
            return True  # 发生异常时默认不中断其他处理

    async def handle_mai_events(
        self,
        event_type: EventType,
        message: Optional[MessageRecv] = None,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
        stream_id: Optional[str] = None,
        action_usage: Optional[List[str]] = None,
    ) -> bool:
        """
        处理所有事件，根据事件类型分发给订阅的处理器。
        """
        from src.plugin_system.core import component_registry

        continue_flag = True

        # 1. 准备消息
        transformed_message = self._prepare_message(
            event_type, message, llm_prompt, llm_response, stream_id, action_usage
        )

        # 2. 获取并遍历处理器
        handlers = self._events_subscribers.get(event_type, [])
        if not handlers:
            return True

        current_stream_id = transformed_message.stream_id if transformed_message else None

        for handler in handlers:
            # 3. 前置检查和配置加载
            if (
                current_stream_id
                and handler.handler_name
                in global_announcement_manager.get_disabled_chat_event_handlers(current_stream_id)
            ):
                continue

            # 统一加载插件配置
            plugin_config = component_registry.get_plugin_config(handler.plugin_name) or {}
            handler.set_plugin_config(plugin_config)

            # 4. 根据类型分发任务
            if handler.intercept_message:
                # 阻塞执行，并更新 continue_flag
                should_continue = await self._dispatch_intercepting_handler(handler, transformed_message)
                continue_flag = continue_flag and should_continue
            else:
                # 异步执行，不阻塞
                self._dispatch_handler_task(handler, transformed_message)

        return continue_flag

    def _insert_event_handler(self, handler_class: Type[BaseEventHandler], handler_info: EventHandlerInfo) -> bool:
        """插入事件处理器到对应的事件类型列表中并设置其插件配置"""

        handler_instance = handler_class()
        handler_instance.set_plugin_name(handler_info.plugin_name or "unknown")

        return True

    def _remove_event_handler_instance(self, handler_class: Type[BaseEventHandler]) -> bool:
        """从事件类型列表中移除事件处理器"""
        display_handler_name = handler_class.handler_name or handler_class.__name__
        if handler_class.event_type == EventType.UNKNOWN:
            logger.warning(f"事件处理器 {display_handler_name} 的事件类型未知，不存在于处理器列表中")
            return False

        handlers = self._events_subscribers[handler_class.event_type]
        for i, handler in enumerate(handlers):
            if isinstance(handler, handler_class):
                del handlers[i]
                logger.debug(f"事件处理器 {display_handler_name} 已移除")
                return True

        logger.warning(f"未找到事件处理器 {display_handler_name}，无法移除")
        return False

    def _transform_event_message(
        self, message: MessageRecv, llm_prompt: Optional[str] = None, llm_response: Optional["LLMGenerationDataModel"] = None
    ) -> MaiMessages:
        """转换事件消息格式"""
        # 直接赋值部分内容
        transformed_message = MaiMessages(
            llm_prompt=llm_prompt,
            llm_response_content=llm_response.content if llm_response else None,
            llm_response_reasoning=llm_response.reasoning if llm_response else None,
            llm_response_model=llm_response.model if llm_response else None,
            llm_response_tool_call=llm_response.tool_calls if llm_response else None,
            raw_message=message.raw_message,
            additional_data=message.message_info.additional_config or {},
        )

        # 消息段处理
        if message.message_segment.type == "seglist":
            transformed_message.message_segments = list(message.message_segment.data)  # type: ignore
        else:
            transformed_message.message_segments = [message.message_segment]

        # stream_id 处理
        if hasattr(message, "chat_stream") and message.chat_stream:
            transformed_message.stream_id = message.chat_stream.stream_id

        # 处理后文本
        transformed_message.plain_text = message.processed_plain_text

        # 基本信息
        if hasattr(message, "message_info") and message.message_info:
            if message.message_info.platform:
                transformed_message.message_base_info["platform"] = message.message_info.platform
            if message.message_info.group_info:
                transformed_message.is_group_message = True
                transformed_message.message_base_info.update(
                    {
                        "group_id": message.message_info.group_info.group_id,
                        "group_name": message.message_info.group_info.group_name,
                    }
                )
            if message.message_info.user_info:
                if not transformed_message.is_group_message:
                    transformed_message.is_private_message = True
                transformed_message.message_base_info.update(
                    {
                        "user_id": message.message_info.user_info.user_id,
                        "user_cardname": message.message_info.user_info.user_cardname,  # 用户群昵称
                        "user_nickname": message.message_info.user_info.user_nickname,  # 用户昵称（用户名）
                    }
                )

        return transformed_message

    def _build_message_from_stream(
        self, stream_id: str, llm_prompt: Optional[str] = None, llm_response: Optional["LLMGenerationDataModel"] = None
    ) -> MaiMessages:
        """从流ID构建消息"""
        chat_stream = get_chat_manager().get_stream(stream_id)
        assert chat_stream, f"未找到流ID为 {stream_id} 的聊天流"
        message = chat_stream.context.get_last_message()
        return self._transform_event_message(message, llm_prompt, llm_response)

    def _transform_event_without_message(
        self,
        stream_id: str,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
        action_usage: Optional[List[str]] = None,
    ) -> MaiMessages:
        """没有message对象时进行转换"""
        chat_stream = get_chat_manager().get_stream(stream_id)
        assert chat_stream, f"未找到流ID为 {stream_id} 的聊天流"
        return MaiMessages(
            stream_id=stream_id,
            llm_prompt=llm_prompt,
            llm_response_content=(llm_response.content if llm_response else None),
            llm_response_reasoning=(llm_response.reasoning if llm_response else None),
            llm_response_model=(llm_response.model if llm_response else None),
            llm_response_tool_call=(llm_response.tool_calls if llm_response else None),
            is_group_message=(not (not chat_stream.group_info)),
            is_private_message=(not chat_stream.group_info),
            action_usage=action_usage,
            additional_data={"response_is_processed": True},
        )

    def _task_done_callback(self, task: asyncio.Task[Tuple[bool, bool, str | None]]):
        """任务完成回调"""
        task_name = task.get_name() or "Unknown Task"
        try:
            success, _, result = task.result()  # 忽略是否继续的标志，因为消息本身未被拦截
            if success:
                logger.debug(f"事件处理任务 {task_name} 已成功完成: {result}")
            else:
                logger.error(f"事件处理任务 {task_name} 执行失败: {result}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"事件处理任务 {task_name} 发生异常: {e}")
        finally:
            with contextlib.suppress(ValueError, KeyError):
                self._handler_tasks[task_name].remove(task)

    async def cancel_handler_tasks(self, handler_name: str) -> None:
        tasks_to_be_cancelled = self._handler_tasks.get(handler_name, [])
        if remaining_tasks := [task for task in tasks_to_be_cancelled if not task.done()]:
            for task in remaining_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=5)
                logger.info(f"已取消事件处理器 {handler_name} 的所有任务")
            except asyncio.TimeoutError:
                logger.warning(f"取消事件处理器 {handler_name} 的任务超时，开始强制取消")
            except Exception as e:
                logger.error(f"取消事件处理器 {handler_name} 的任务时发生异常: {e}")
        if handler_name in self._handler_tasks:
            del self._handler_tasks[handler_name]

    async def unregister_event_subscriber(self, handler_name: str) -> bool:
        """取消注册事件处理器"""
        if handler_name not in self._handler_mapping:
            logger.warning(f"事件处理器 {handler_name} 不存在，无法取消注册")
            return False

        await self.cancel_handler_tasks(handler_name)

        handler_class = self._handler_mapping.pop(handler_name)
        if not self._remove_event_handler_instance(handler_class):
            return False

        logger.info(f"事件处理器 {handler_name} 已成功取消注册")
        return True


events_manager = EventsManager()
