import time
import traceback
from dateutil import tz
from typing import List, Optional, Dict, Any, Set
from maim_message import UserInfo
from src.common.logger_manager import get_logger
from src.chat.utils.chat_message_builder import build_readable_messages

# 确保导入路径正确
from .chat_observer import ChatObserver
from .chat_states import NotificationHandler, NotificationType, Notification

logger = get_logger("pfc_observation_info")

TIME_ZONE = tz.gettz("Asia/Shanghai")  # 使用配置的时区，提供默认值


class ObservationInfoHandler(NotificationHandler):
    """ObservationInfo的通知处理器"""

    def __init__(self, observation_info: "ObservationInfo", private_name: str):
        """初始化处理器"""
        self.observation_info = observation_info
        self.private_name = private_name

    async def handle_notification(self, notification: Notification):
        """处理来自 ChatObserver 的通知"""
        notification_type = notification.type
        data = notification.data
        timestamp = notification.timestamp  # 获取通知时间戳

        try:
            if notification_type == NotificationType.NEW_MESSAGE:
                # 处理新消息通知
                message_dict = data  # data 本身就是消息字典
                if not isinstance(message_dict, dict):
                    logger.warning(f"[私聊][{self.private_name}] 收到的 NEW_MESSAGE 数据不是字典: {data}")
                    return

                # 解析 UserInfo
                user_info_dict = message_dict.get("user_info")
                user_info: Optional[UserInfo] = None
                if isinstance(user_info_dict, dict):
                    try:
                        user_info = UserInfo.from_dict(user_info_dict)
                    except Exception as e:
                        logger.error(
                            f"[私聊][{self.private_name}] 从字典创建 UserInfo 时出错: {e}, dict: {user_info_dict}"
                        )
                elif user_info_dict is not None:
                    logger.warning(
                        f"[私聊][{self.private_name}] 收到的 user_info 不是预期的字典类型: {type(user_info_dict)}"
                    )

                # 更新 ObservationInfo
                await self.observation_info.update_from_message(message_dict, user_info)

            elif notification_type == NotificationType.COLD_CHAT:
                # 处理冷场通知
                is_cold = data.get("is_cold", False)
                await self.observation_info.update_cold_chat_status(is_cold, timestamp)  # 使用通知时间戳

            elif notification_type == NotificationType.MESSAGE_DELETED:
                # 处理消息删除通知
                message_id_to_delete = data.get("message_id")
                if message_id_to_delete:
                    await self.observation_info.remove_unprocessed_message(message_id_to_delete)
                else:
                    logger.warning(f"[私聊][{self.private_name}] 收到无效的消息删除通知，缺少 message_id: {data}")

            # --- 可以根据需要处理其他通知类型 ---
            elif notification_type == NotificationType.ACTIVE_CHAT:
                is_active = data.get("is_active", False)
                # 通常由 COLD_CHAT 的反向状态处理，但也可以在这里显式处理
                await self.observation_info.update_cold_chat_status(not is_active, timestamp)

            elif notification_type == NotificationType.BOT_SPEAKING:
                # 机器人开始说话 (例如，如果需要显示"正在输入...")
                # self.observation_info.is_typing = True
                pass  # 暂时不处理

            elif notification_type == NotificationType.USER_SPEAKING:
                # 用户开始说话
                # self.observation_info.is_typing = True
                pass  # 暂时不处理

            elif notification_type == NotificationType.USER_JOINED:
                user_id = data.get("user_id")
                if user_id:
                    self.observation_info.active_users.add(str(user_id))
                    self.observation_info.update_changed()

            elif notification_type == NotificationType.USER_LEFT:
                user_id = data.get("user_id")
                if user_id:
                    self.observation_info.active_users.discard(str(user_id))
                    self.observation_info.update_changed()

            elif notification_type == NotificationType.ERROR:
                error_msg = data.get("error", "未提供错误信息")
                logger.error(f"[私聊][{self.private_name}] 收到错误通知: {error_msg}")
                # 可以在这里触发一些错误处理逻辑

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 处理通知时发生错误 (类型: {notification_type.name}): {e}")
            logger.error(traceback.format_exc())


class ObservationInfo:
    """决策信息类，用于收集和管理来自chat_observer的通知信息"""

    def __init__(self, private_name: str):
        """初始化 ObservationInfo"""
        self.private_name: str = private_name

        self.sender_user_id: Optional[str] = None  # 存储为字符串
        self.sender_platform: Optional[str] = None

        # 聊天记录相关
        self.chat_history: List[Dict[str, Any]] = []  # 存储已处理的消息历史
        self.chat_history_str: str = "还没有聊天记录。"  # 用于生成 Prompt 的历史记录字符串
        self.chat_history_count: int = 0

        # 未处理消息相关 (核心修改点)
        self.unprocessed_messages: List[Dict[str, Any]] = []  # 存储尚未被机器人回复的消息
        self.new_messages_count: int = 0  # unprocessed_messages 的数量

        # 状态信息
        self.active_users: Set[str] = set()  # 当前活跃用户 (私聊场景可能只有对方)
        self.last_bot_speak_time: Optional[float] = None
        self.last_user_speak_time: Optional[float] = None  # 指对方用户的发言时间
        self.last_message_time: Optional[float] = None  # 指所有消息（包括自己）的最新时间
        self.last_message_id: Optional[str] = None
        self.last_message_content: str = ""
        self.last_message_sender: Optional[str] = None  # user_id of the last message sender
        self.bot_id: Optional[str] = None  # 机器人自己的 ID

        # 冷场状态
        self.cold_chat_start_time: Optional[float] = None
        self.cold_chat_duration: float = 0.0
        self.is_cold_chat: bool = False  # 当前是否处于冷场状态

        # 其他状态
        self.is_typing: bool = False  # 是否正在输入 (未来可能用到)
        self.changed: bool = False  # 状态是否有变化 (用于优化)

        # 用于存储格式化的当前时间
        self.current_time_str: Optional[str] = None

        # 关联对象
        self.chat_observer: Optional[ChatObserver] = None
        self.handler: Optional[ObservationInfoHandler] = ObservationInfoHandler(self, self.private_name)

        # 初始化 bot_id
        try:
            from ...config.config import global_config

            self.bot_id = str(global_config.BOT_QQ) if global_config.BOT_QQ else None
            if not self.bot_id:
                logger.error(f"[私聊][{self.private_name}] 未能从配置中获取 BOT_QQ ID！")
        except ImportError:
            logger.error(f"[私聊][{self.private_name}] 无法导入 global_config 获取 BOT_QQ ID！")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 获取 BOT_QQ ID 时出错: {e}")

    def bind_to_chat_observer(self, chat_observer: ChatObserver):
        """绑定到指定的 ChatObserver 并注册通知处理器"""
        if self.chat_observer:
            logger.warning(f"[私聊][{self.private_name}] 尝试重复绑定 ChatObserver")
            return
        if not self.handler:
            logger.error(f"[私聊][{self.private_name}] ObservationInfoHandler 未初始化，无法绑定！")
            return

        self.chat_observer = chat_observer
        try:
            # 注册需要处理的通知类型
            notification_manager = self.chat_observer.notification_manager
            notification_manager.register_handler("observation_info", NotificationType.NEW_MESSAGE, self.handler)
            notification_manager.register_handler("observation_info", NotificationType.COLD_CHAT, self.handler)
            notification_manager.register_handler("observation_info", NotificationType.MESSAGE_DELETED, self.handler)
            # 根据需要注册更多类型...
            # notification_manager.register_handler("observation_info", NotificationType.ACTIVE_CHAT, self.handler)
            # notification_manager.register_handler("observation_info", NotificationType.USER_JOINED, self.handler)
            # notification_manager.register_handler("observation_info", NotificationType.USER_LEFT, self.handler)
            # notification_manager.register_handler("observation_info", NotificationType.ERROR, self.handler)

            logger.info(f"[私聊][{self.private_name}] ObservationInfo 成功绑定到 ChatObserver")
        except AttributeError:
            logger.error(f"[私聊][{self.private_name}] 绑定的 ChatObserver 对象缺少 notification_manager 属性！")
            self.chat_observer = None  # 绑定失败
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 绑定到 ChatObserver 时出错: {e}")
            self.chat_observer = None  # 绑定失败

    def unbind_from_chat_observer(self):
        """解除与 ChatObserver 的绑定"""
        if self.chat_observer and hasattr(self.chat_observer, "notification_manager") and self.handler:
            try:
                notification_manager = self.chat_observer.notification_manager
                notification_manager.unregister_handler("observation_info", NotificationType.NEW_MESSAGE, self.handler)
                notification_manager.unregister_handler("observation_info", NotificationType.COLD_CHAT, self.handler)
                notification_manager.unregister_handler(
                    "observation_info", NotificationType.MESSAGE_DELETED, self.handler
                )
                # ... 注销其他已注册的类型 ...

                logger.info(f"[私聊][{self.private_name}] ObservationInfo 成功从 ChatObserver 解绑")
            except Exception as e:
                logger.error(f"[私聊][{self.private_name}] 从 ChatObserver 解绑时出错: {e}")
            finally:
                self.chat_observer = None  # 无论成功与否都清除引用
        else:
            logger.warning(f"[私聊][{self.private_name}] 尝试解绑时 ChatObserver 无效或 handler 未设置")

    async def update_from_message(self, message: Dict[str, Any], user_info: Optional[UserInfo]):
        """根据收到的新消息更新 ObservationInfo 的状态"""
        message_time = message.get("time")
        message_id = message.get("message_id")
        processed_text = message.get("processed_plain_text", "")
        sender_id_str: Optional[str] = str(user_info.user_id) if user_info else None

        if not message_time or not message_id:
            logger.warning(f"[私聊][{self.private_name}] 收到的消息缺少 time 或 message_id: {message}")
            return

        # --- 新增/修改：提取并存储发信人详细信息 ---
        current_message_sender_id: Optional[str] = None
        if user_info:
            try:
                self.sender_user_id = str(user_info.user_id)  # 确保是字符串
                self.sender_platform = user_info.platform
                current_message_sender_id = self.sender_user_id  # 用于后续逻辑
                logger.debug(
                    f"[私聊][{self.private_name}] 更新发信人信息: ID={self.sender_user_id}, Name={self.private_name}, Platform={self.sender_platform}"
                )
            except AttributeError as e:
                logger.error(f"[私聊][{self.private_name}] 从 UserInfo 对象提取信息时出错: {e}, UserInfo: {user_info}")
                # 如果提取失败，将这些新字段设为 None，避免使用旧数据
                self.sender_user_id = None
                self.sender_platform = None
        else:
            logger.warning(f"[私聊][{self.private_name}] 处理消息更新时缺少有效的 UserInfo, message_id: {message_id}")
            # 如果没有 UserInfo，也将这些新字段设为 None
            self.sender_user_id = None
            self.sender_platform = None
        # --- 新增/修改结束 ---

        # 更新最后消息时间（所有消息）
        if message_time > (self.last_message_time or 0):
            self.last_message_time = message_time
            self.last_message_id = message_id
            self.last_message_content = processed_text
            self.last_message_sender = current_message_sender_id  # 使用新获取的 current_message_sender_id

        # 更新说话者特定时间
        if sender_id_str:
            if sender_id_str == self.bot_id:
                self.last_bot_speak_time = message_time
            else:
                self.last_user_speak_time = message_time
                self.active_users.add(sender_id_str)  # 添加到活跃用户
        else:
            logger.warning(f"[私聊][{self.private_name}] 处理消息更新时缺少有效的 UserInfo, message_id: {message_id}")

        # 更新冷场状态
        self.is_cold_chat = False
        self.cold_chat_start_time = None
        self.cold_chat_duration = 0.0

        # --- [核心修改] 将新消息添加到未处理列表 ---
        # 检查消息是否已存在于未处理列表中，避免重复添加
        if not any(msg.get("message_id") == message_id for msg in self.unprocessed_messages):
            # 创建消息的副本以避免修改原始数据（如果需要）
            self.unprocessed_messages.append(message.copy())
            self.new_messages_count = len(self.unprocessed_messages)
            logger.debug(
                f"[私聊][{self.private_name}] 添加新未处理消息 ID: {message_id}, 发送者: {sender_id_str}, 当前未处理数: {self.new_messages_count}"
            )
            self.update_changed()
        else:
            logger.warning(f"[私聊][{self.private_name}] 尝试重复添加未处理消息 ID: {message_id}")

    async def remove_unprocessed_message(self, message_id_to_delete: str):
        """从 unprocessed_messages 列表中移除指定 ID 的消息"""
        original_count = len(self.unprocessed_messages)
        self.unprocessed_messages = [
            msg for msg in self.unprocessed_messages if msg.get("message_id") != message_id_to_delete
        ]
        new_count = len(self.unprocessed_messages)

        if new_count < original_count:
            self.new_messages_count = new_count
            logger.debug(
                f"[私聊][{self.private_name}] 移除了未处理的消息 (ID: {message_id_to_delete}), 当前未处理数: {self.new_messages_count}"
            )
            self.update_changed()
        else:
            logger.warning(f"[私聊][{self.private_name}] 尝试移除不存在的未处理消息 ID: {message_id_to_delete}")

    async def update_cold_chat_status(self, is_cold: bool, current_time: float):
        """更新冷场状态"""
        if is_cold != self.is_cold_chat:
            self.is_cold_chat = is_cold
            if is_cold:
                # 冷场开始时间应基于最后一条消息的时间
                self.cold_chat_start_time = self.last_message_time or current_time
                logger.info(f"[私聊][{self.private_name}] 进入冷场状态，开始时间: {self.cold_chat_start_time:.2f}")
            else:
                if self.cold_chat_start_time:
                    self.cold_chat_duration = current_time - self.cold_chat_start_time
                    logger.info(f"[私聊][{self.private_name}] 结束冷场状态，持续时间: {self.cold_chat_duration:.2f} 秒")
                self.cold_chat_start_time = None  # 结束冷场，重置开始时间
            self.update_changed()

        # 持续更新冷场时长
        if self.is_cold_chat and self.cold_chat_start_time:
            self.cold_chat_duration = current_time - self.cold_chat_start_time

    def update_changed(self):
        """标记状态已改变"""
        self.changed = True
        # 这个标记通常在处理完改变后由外部逻辑重置为 False

    # --- [修改点 15] 重命名并修改 clear_unprocessed_messages ---
    async def clear_processed_messages(self, message_ids_to_clear: Set[str]):
        """将指定 ID 的未处理消息移入历史记录，并更新相关状态"""
        if not message_ids_to_clear:
            logger.debug(f"[私聊][{self.private_name}] 没有需要清理的消息 ID。")
            return

        messages_to_move = []
        remaining_messages = []
        cleared_count = 0

        # 分离要清理和要保留的消息
        for msg in self.unprocessed_messages:
            msg_id = msg.get("message_id")
            if msg_id in message_ids_to_clear:
                messages_to_move.append(msg)
                cleared_count += 1
            else:
                remaining_messages.append(msg)

        if not messages_to_move:
            logger.debug(
                f"[私聊][{self.private_name}] 未找到与 ID 列表 {message_ids_to_clear} 匹配的未处理消息进行清理。"
            )
            return

        logger.debug(f"[私聊][{self.private_name}] 准备清理 {cleared_count} 条已处理消息...")

        # 将要移动的消息添加到历史记录 (按时间排序)
        messages_to_move.sort(key=lambda m: m.get("time", 0))
        self.chat_history.extend(messages_to_move)

        # 限制历史记录长度 (可选)
        max_history_len = 100  # 例如保留最近 100 条
        if len(self.chat_history) > max_history_len:
            self.chat_history = self.chat_history[-max_history_len:]

        # 更新历史记录字符串 (仅使用最近一部分生成，提高效率)
        history_slice_for_str = self.chat_history[-30:]  # 例如最近 20 条
        try:
            self.chat_history_str = await build_readable_messages(
                history_slice_for_str,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,  # read_mark 可能需要调整或移除
            )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 构建聊天记录字符串时出错: {e}")
            self.chat_history_str = "[构建聊天记录出错]"

        # 更新未处理消息列表和计数
        self.unprocessed_messages = remaining_messages
        self.new_messages_count = len(self.unprocessed_messages)
        self.chat_history_count = len(self.chat_history)

        logger.debug(
            f"[私聊][{self.private_name}] 已清理 {cleared_count} 条消息 (IDs: {message_ids_to_clear})，剩余未处理 {self.new_messages_count} 条，当前历史记录 {self.chat_history_count} 条。"
        )

        self.update_changed()  # 状态改变

    # --- Helper methods (可以根据需要添加) ---
    def get_active_duration(self) -> float:
        """获取当前活跃时长（距离最后一条消息的时间）"""
        if not self.last_message_time:
            return float("inf")  # 或返回 0.0，取决于定义
        return time.time() - self.last_message_time

    def get_user_response_time(self) -> Optional[float]:
        """获取对方最后响应时间（距离对方最后一条消息的时间）"""
        if not self.last_user_speak_time:
            return None
        return time.time() - self.last_user_speak_time

    def get_bot_response_time(self) -> Optional[float]:
        """获取机器人最后响应时间（距离机器人最后一条消息的时间）"""
        if not self.last_bot_speak_time:
            return None
        return time.time() - self.last_bot_speak_time
