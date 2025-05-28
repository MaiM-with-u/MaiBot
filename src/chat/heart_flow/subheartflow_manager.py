import asyncio
import time
from typing import Dict, Any, Optional, List
from src.common.logger_manager import get_logger
from src.chat.message_receive.chat_stream import chat_manager
from src.chat.heart_flow.sub_heartflow import SubHeartflow, ChatState
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation


# 初始化日志记录器

logger = get_logger("subheartflow_manager")

# 子心流管理相关常量
INACTIVE_THRESHOLD_SECONDS = 3600  # 子心流不活跃超时时间(秒)
NORMAL_CHAT_TIMEOUT_SECONDS = 30 * 60  # 30分钟


async def _try_set_subflow_absent_internal(subflow: "SubHeartflow", log_prefix: str) -> bool:
    """
    尝试将给定的子心流对象状态设置为 ABSENT (内部方法，不处理锁)。

    Args:
        subflow: 子心流对象。
        log_prefix: 用于日志记录的前缀 (例如 "[子心流管理]" 或 "[停用]")。

    Returns:
        bool: 如果状态成功变为 ABSENT 或原本就是 ABSENT，返回 True；否则返回 False。
    """
    flow_id = subflow.subheartflow_id
    stream_name = chat_manager.get_stream_name(flow_id) or flow_id

    if subflow.chat_state.chat_status != ChatState.ABSENT:
        logger.debug(f"{log_prefix} 设置 {stream_name} 状态为 ABSENT")
        try:
            await subflow.change_chat_state(ChatState.ABSENT)
            # 再次检查以确认状态已更改 (change_chat_state 内部应确保)
            if subflow.chat_state.chat_status == ChatState.ABSENT:
                return True
            else:
                logger.warning(
                    f"{log_prefix} 调用 change_chat_state 后，{stream_name} 状态仍为 {subflow.chat_state.chat_status.value}"
                )
                return False
        except Exception as e:
            logger.error(f"{log_prefix} 设置 {stream_name} 状态为 ABSENT 时失败: {e}", exc_info=True)
            return False
    else:
        logger.debug(f"{log_prefix} {stream_name} 已是 ABSENT 状态")
        return True  # 已经是目标状态，视为成功


class SubHeartflowManager:
    """管理所有活跃的 SubHeartflow 实例。"""

    def __init__(self):
        self.subheartflows: Dict[Any, "SubHeartflow"] = {}
        self._lock = asyncio.Lock()  # 用于保护 self.subheartflows 的访问

    async def force_change_state(self, subflow_id: Any, target_state: ChatState) -> bool:
        """强制改变指定子心流的状态"""
        async with self._lock:
            subflow = self.subheartflows.get(subflow_id)
            if not subflow:
                logger.warning(f"[强制状态转换]尝试转换不存在的子心流{subflow_id} 到 {target_state.value}")
                return False
            await subflow.change_chat_state(target_state)
            logger.info(f"[强制状态转换]子心流 {subflow_id} 已转换到 {target_state.value}")
            return True

    def get_all_subheartflows(self) -> List["SubHeartflow"]:
        """获取所有当前管理的 SubHeartflow 实例列表 (快照)。"""
        return list(self.subheartflows.values())

    async def get_or_create_subheartflow(self, subheartflow_id: Any) -> Optional["SubHeartflow"]:
        """获取或创建指定ID的子心流实例

        Args:
            subheartflow_id: 子心流唯一标识符
            mai_states 参数已被移除，使用 self.mai_state_info

        Returns:
            成功返回SubHeartflow实例，失败返回None
        """
        async with self._lock:
            # 检查是否已存在该子心流
            if subheartflow_id in self.subheartflows:
                subflow = self.subheartflows[subheartflow_id]
                if subflow.should_stop:
                    logger.warning(f"尝试获取已停止的子心流 {subheartflow_id}，正在重新激活")
                    subflow.should_stop = False  # 重置停止标志
                return subflow

            try:
                # 初始化子心流, 传入 mai_state_info
                new_subflow = SubHeartflow(
                    subheartflow_id,
                )

                # 首先创建并添加聊天观察者
                observation = ChattingObservation(chat_id=subheartflow_id)
                await observation.initialize()
                new_subflow.add_observation(observation)

                # 然后再进行异步初始化，此时 SubHeartflow 内部若需启动 HeartFChatting，就能拿到 observation
                await new_subflow.initialize()

                # 注册子心流
                self.subheartflows[subheartflow_id] = new_subflow
                heartflow_name = chat_manager.get_stream_name(subheartflow_id) or subheartflow_id
                logger.info(f"[{heartflow_name}] 开始接收消息")

                return new_subflow
            except Exception as e:
                logger.error(f"创建子心流 {subheartflow_id} 失败: {e}", exc_info=True)
                return None

    async def sleep_subheartflow(self, subheartflow_id: Any, reason: str) -> bool:
        """停止指定的子心流并将其状态设置为 ABSENT"""
        log_prefix = "[子心流管理]"
        async with self._lock:  # 加锁以安全访问字典
            subheartflow = self.subheartflows.get(subheartflow_id)

            stream_name = chat_manager.get_stream_name(subheartflow_id) or subheartflow_id
            logger.info(f"{log_prefix} 正在停止 {stream_name}, 原因: {reason}")

            # 调用内部方法处理状态变更
            success = await _try_set_subflow_absent_internal(subheartflow, log_prefix)

            return success
        # 锁在此处自动释放

    def get_inactive_subheartflows(self, max_age_seconds=INACTIVE_THRESHOLD_SECONDS):
        """识别并返回需要清理的不活跃(处于ABSENT状态超过一小时)子心流(id, 原因)"""
        _current_time = time.time()
        flows_to_stop = []

        for subheartflow_id, subheartflow in list(self.subheartflows.items()):
            state = subheartflow.chat_state.chat_status
            if state != ChatState.ABSENT:
                continue
            subheartflow.update_last_chat_state_time()
            _absent_last_time = subheartflow.chat_state_last_time
            flows_to_stop.append(subheartflow_id)

        return flows_to_stop

    async def deactivate_all_subflows(self):
        """将所有子心流的状态更改为 ABSENT (例如主状态变为OFFLINE时调用)"""
        log_prefix = "[停用]"
        changed_count = 0
        processed_count = 0

        async with self._lock:  # 获取锁以安全迭代
            # 使用 list() 创建一个当前值的快照，防止在迭代时修改字典
            flows_to_update = list(self.subheartflows.values())
            processed_count = len(flows_to_update)
            if not flows_to_update:
                logger.debug(f"{log_prefix} 无活跃子心流，无需操作")
                return

            for subflow in flows_to_update:
                # 记录原始状态，以便统计实际改变的数量
                original_state_was_absent = subflow.chat_state.chat_status == ChatState.ABSENT

                success = await _try_set_subflow_absent_internal(subflow, log_prefix)

                # 如果成功设置为 ABSENT 且原始状态不是 ABSENT，则计数
                if success and not original_state_was_absent:
                    if subflow.chat_state.chat_status == ChatState.ABSENT:
                        changed_count += 1
                    else:
                        # 这种情况理论上不应发生，如果内部方法返回 True 的话
                        stream_name = chat_manager.get_stream_name(subflow.subheartflow_id) or subflow.subheartflow_id
                        logger.warning(f"{log_prefix} 内部方法声称成功但 {stream_name} 状态未变为 ABSENT。")
        # 锁在此处自动释放

        logger.info(
            f"{log_prefix} 完成，共处理 {processed_count} 个子心流，成功将 {changed_count} 个非 ABSENT 子心流的状态更改为 ABSENT。"
        )

    # async def sbhf_normal_into_focus(self):
    # """评估子心流兴趣度，满足条件则提升到FOCUSED状态（基于start_hfc_probability）"""
    # try:
    #     for sub_hf in list(self.subheartflows.values()):
    #         flow_id = sub_hf.subheartflow_id
    #         stream_name = chat_manager.get_stream_name(flow_id) or flow_id

    #         # 跳过已经是FOCUSED状态的子心流
    #         if sub_hf.chat_state.chat_status == ChatState.FOCUSED:
    #             continue

    #         if sub_hf.interest_chatting.start_hfc_probability == 0:
    #             continue
    #         else:
    #             logger.debug(
    #                 f"{stream_name}，现在状态: {sub_hf.chat_state.chat_status.value}，进入专注概率: {sub_hf.interest_chatting.start_hfc_probability}"
    #             )

    #         if random.random() >= sub_hf.interest_chatting.start_hfc_probability:
    #             continue

    #         # 获取最新状态并执行提升
    #         current_subflow = self.subheartflows.get(flow_id)
    #         if not current_subflow:
    #             continue

    #         logger.info(
    #             f"{stream_name} 触发 认真水群 (概率={current_subflow.interest_chatting.start_hfc_probability:.2f})"
    #         )

    #         # 执行状态提升
    #         await current_subflow.change_chat_state(ChatState.FOCUSED)

    # except Exception as e:
    #     logger.error(f"启动HFC 兴趣评估失败: {e}", exc_info=True)

    async def sbhf_focus_into_normal(self, subflow_id: Any):
        """
        接收来自 HeartFChatting 的请求，将特定子心流的状态转换为 NORMAL。
        通常在连续多次 "no_reply" 后被调用。
        对于私聊和群聊，都转换为 NORMAL。

        Args:
            subflow_id: 需要转换状态的子心流 ID。
        """
        async with self._lock:
            subflow = self.subheartflows.get(subflow_id)
            if not subflow:
                logger.warning(f"[状态转换请求] 尝试转换不存在的子心流 {subflow_id} 到 NORMAL")
                return

            stream_name = chat_manager.get_stream_name(subflow_id) or subflow_id
            current_state = subflow.chat_state.chat_status

            if current_state == ChatState.FOCUSED:
                target_state = ChatState.NORMAL
                log_reason = "转为NORMAL"

                logger.info(
                    f"[状态转换请求] 接收到请求，将 {stream_name} (当前: {current_state.value}) 尝试转换为 {target_state.value} ({log_reason})"
                )
                try:
                    # 从HFC到CHAT时，清空兴趣字典
                    subflow.interest_dict.clear()
                    await subflow.change_chat_state(target_state)
                    final_state = subflow.chat_state.chat_status
                    if final_state == target_state:
                        logger.debug(f"[状态转换请求] {stream_name} 状态已成功转换为 {final_state.value}")
                    else:
                        logger.warning(
                            f"[状态转换请求] 尝试将 {stream_name} 转换为 {target_state.value} 后，状态实际为 {final_state.value}"
                        )
                except Exception as e:
                    logger.error(
                        f"[状态转换请求] 转换 {stream_name} 到 {target_state.value} 时出错: {e}", exc_info=True
                    )
            elif current_state == ChatState.ABSENT:
                logger.debug(f"[状态转换请求] {stream_name} 处于 ABSENT 状态，尝试转为 NORMAL")
                await subflow.change_chat_state(ChatState.NORMAL)
            else:
                logger.debug(f"[状态转换请求] {stream_name} 当前状态为 {current_state.value}，无需转换")

    async def delete_subflow(self, subheartflow_id: Any):
        """删除指定的子心流。"""
        async with self._lock:
            subflow = self.subheartflows.pop(subheartflow_id, None)
            if subflow:
                logger.info(f"正在删除 SubHeartflow: {subheartflow_id}...")
                try:
                    # 调用 shutdown 方法确保资源释放
                    await subflow.shutdown()
                    logger.info(f"SubHeartflow {subheartflow_id} 已成功删除。")
                except Exception as e:
                    logger.error(f"删除 SubHeartflow {subheartflow_id} 时出错: {e}", exc_info=True)
            else:
                logger.warning(f"尝试删除不存在的 SubHeartflow: {subheartflow_id}")

    # --- 新增：处理私聊从 ABSENT 直接到 FOCUSED 的逻辑 --- #
    async def sbhf_absent_private_into_focus(self):
        """检查 ABSENT 状态的私聊子心流是否有新活动，若有则直接转换为 FOCUSED。"""
        log_prefix_task = "[私聊激活检查]"
        transitioned_count = 0
        checked_count = 0

        async with self._lock:
            # --- 筛选出所有 ABSENT 状态的私聊子心流 --- #
            eligible_subflows = [
                hf
                for hf in self.subheartflows.values()
                if hf.chat_state.chat_status == ChatState.ABSENT and not hf.is_group_chat
            ]
            checked_count = len(eligible_subflows)

            if not eligible_subflows:
                # logger.debug(f"{log_prefix_task} 没有 ABSENT 状态的私聊子心流可以评估。")
                return

            # --- 遍历评估每个符合条件的私聊 --- #
            for sub_hf in eligible_subflows:
                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id
                log_prefix = f"[{stream_name}]({log_prefix_task})"

                try:
                    # --- 检查是否有新活动 --- #
                    observation = sub_hf._get_primary_observation()  # 获取主要观察者
                    is_active = False
                    if observation:
                        # 检查自上次状态变为 ABSENT 后是否有新消息
                        # 使用 chat_state_changed_time 可能更精确
                        # 加一点点缓冲时间（例如 1 秒）以防时间戳完全相等
                        timestamp_to_check = sub_hf.chat_state_changed_time - 1
                        has_new = await observation.has_new_messages_since(timestamp_to_check)
                        if has_new:
                            is_active = True
                            logger.debug(f"{log_prefix} 检测到新消息，标记为活跃。")
                    else:
                        logger.warning(f"{log_prefix} 无法获取主要观察者来检查活动状态。")

                    # --- 如果活跃，则尝试转换 --- #
                    if is_active:
                        await sub_hf.change_chat_state(ChatState.FOCUSED)
                        # 确认转换成功
                        if sub_hf.chat_state.chat_status == ChatState.FOCUSED:
                            transitioned_count += 1
                            logger.info(f"{log_prefix} 成功进入 FOCUSED 状态。")
                        else:
                            logger.warning(
                                f"{log_prefix} 尝试进入 FOCUSED 状态失败。当前状态: {sub_hf.chat_state.chat_status.value}"
                            )
                    # else: # 不活跃，无需操作
                    #    logger.debug(f"{log_prefix} 未检测到新活动，保持 ABSENT。")

                except Exception as e:
                    logger.error(f"{log_prefix} 检查私聊活动或转换状态时出错: {e}", exc_info=True)

        # --- 循环结束后记录总结日志 --- #
        if transitioned_count > 0:
            logger.debug(
                f"{log_prefix_task} 完成，共检查 {checked_count} 个私聊，{transitioned_count} 个转换为 FOCUSED。"
            )
