import asyncio
import time
import random
from typing import Dict, Any, Optional, List
import json  # 导入 json 模块
import functools  # <-- 新增导入

# 导入日志模块
from src.common.logger import get_module_logger, LogConfig, SUBHEARTFLOW_MANAGER_STYLE_CONFIG

# 导入聊天流管理模块
from src.plugins.chat.chat_stream import chat_manager

# 导入心流相关类
from src.heart_flow.sub_heartflow import SubHeartflow, ChatState
from src.heart_flow.mai_state_manager import MaiStateInfo
from .observation import ChattingObservation

# 导入LLM请求工具
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.individuality.individuality import Individuality
import traceback

# --- 新增导入 ---
from src.do_tool.tool_can_use.schedule_task import TaskScheduler
from datetime import datetime, timezone
# --- 结束新增导入 ---


# 初始化日志记录器

subheartflow_manager_log_config = LogConfig(
    console_format=SUBHEARTFLOW_MANAGER_STYLE_CONFIG["console_format"],
    file_format=SUBHEARTFLOW_MANAGER_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("subheartflow_manager", config=subheartflow_manager_log_config)

# 子心流管理相关常量
INACTIVE_THRESHOLD_SECONDS = 3600  # 子心流不活跃超时时间(秒)


class SubHeartflowManager:
    """管理所有活跃的 SubHeartflow 实例。"""

    def __init__(self, mai_state_info: MaiStateInfo):
        self.subheartflows: Dict[Any, "SubHeartflow"] = {}
        self._lock = asyncio.Lock()  # 用于保护 self.subheartflows 的访问
        self.mai_state_info: MaiStateInfo = mai_state_info  # 存储传入的 MaiStateInfo 实例

        # 为 LLM 状态评估创建一个 LLMRequest 实例
        # 使用与 Heartflow 相同的模型和参数
        self.llm_state_evaluator = LLMRequest(
            model=global_config.llm_heartflow,  # 与 Heartflow 一致
            temperature=0.6,  # 与 Heartflow 一致
            max_tokens=1000,  # 与 Heartflow 一致 (虽然可能不需要这么多)
            request_type="subheartflow_state_eval",  # 保留特定的请求类型
        )

    def get_all_subheartflows(self) -> List["SubHeartflow"]:
        """获取所有当前管理的 SubHeartflow 实例列表 (快照)。"""
        return list(self.subheartflows.values())

    async def get_or_create_subheartflow(self, subheartflow_id: Any) -> Optional["SubHeartflow"]:
        """获取或创建指定ID的子心流实例

        Args:
            subheartflow_id: 子心流唯一标识符
            # mai_states 参数已被移除，使用 self.mai_state_info

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

                subflow.last_active_time = time.time()  # 更新活跃时间
                # logger.debug(f"获取到已存在的子心流: {subheartflow_id}")
                return subflow

            try:
                # --- 使用 functools.partial 创建 HFC 回调 --- #
                # 将 manager 的 _handle_hfc_no_reply 方法与当前的 subheartflow_id 绑定
                hfc_callback = functools.partial(self._handle_hfc_no_reply, subheartflow_id)
                # --- 结束创建回调 --- #

                # 初始化子心流, 传入 mai_state_info 和 partial 创建的回调
                new_subflow = SubHeartflow(
                    subheartflow_id,
                    self.mai_state_info,
                    hfc_callback,  # <-- 传递 partial 创建的回调
                )

                # 异步初始化
                await new_subflow.initialize()

                # 添加聊天观察者
                observation = ChattingObservation(chat_id=subheartflow_id)
                new_subflow.add_observation(observation)

                # 注册子心流
                self.subheartflows[subheartflow_id] = new_subflow
                heartflow_name = chat_manager.get_stream_name(subheartflow_id) or subheartflow_id
                logger.info(f"[{heartflow_name}] 开始接收消息")

                # 启动后台任务
                asyncio.create_task(new_subflow.subheartflow_start_working())

                return new_subflow
            except Exception as e:
                logger.error(f"创建子心流 {subheartflow_id} 失败: {e}", exc_info=True)
                return None

    # --- 新增：内部方法，用于尝试将单个子心流设置为 ABSENT ---
    async def _try_set_subflow_absent_internal(self, subflow: "SubHeartflow", log_prefix: str) -> bool:
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

    # --- 结束新增 ---

    async def sleep_subheartflow(self, subheartflow_id: Any, reason: str) -> bool:
        """停止指定的子心流并将其状态设置为 ABSENT"""
        log_prefix = "[子心流管理]"
        async with self._lock:  # 加锁以安全访问字典
            subheartflow = self.subheartflows.get(subheartflow_id)

            stream_name = chat_manager.get_stream_name(subheartflow_id) or subheartflow_id
            logger.info(f"{log_prefix} 正在停止 {stream_name}, 原因: {reason}")

            # 调用内部方法处理状态变更
            success = await self._try_set_subflow_absent_internal(subheartflow, log_prefix)

            return success
        # 锁在此处自动释放

    def get_inactive_subheartflows(self, max_age_seconds=INACTIVE_THRESHOLD_SECONDS):
        """识别并返回需要清理的不活跃(处于ABSENT状态超过一小时)子心流(id, 原因)"""
        current_time = time.time()
        flows_to_stop = []

        for subheartflow_id, subheartflow in list(self.subheartflows.items()):
            state = subheartflow.chat_state.chat_status
            if state != ChatState.ABSENT:
                continue
            subheartflow.update_last_chat_state_time()
            absent_last_time = subheartflow.chat_state_last_time
            if max_age_seconds and (current_time - absent_last_time) > max_age_seconds:
                flows_to_stop.append(subheartflow_id)

        return flows_to_stop

    async def enforce_subheartflow_limits(self):
        """根据主状态限制停止超额子心流(优先停不活跃的)"""
        # 使用 self.mai_state_info 获取当前状态和限制
        current_mai_state = self.mai_state_info.get_current_state()
        normal_limit = current_mai_state.get_normal_chat_max_num()
        focused_limit = current_mai_state.get_focused_chat_max_num()
        logger.debug(f"[限制] 状态:{current_mai_state.value}, 普通限:{normal_limit}, 专注限:{focused_limit}")

        # 分类统计当前子心流
        normal_flows = []
        focused_flows = []
        for flow_id, flow in list(self.subheartflows.items()):
            if flow.chat_state.chat_status == ChatState.CHAT:
                normal_flows.append((flow_id, getattr(flow, "last_active_time", 0)))
            elif flow.chat_state.chat_status == ChatState.FOCUSED:
                focused_flows.append((flow_id, getattr(flow, "last_active_time", 0)))

        logger.debug(f"[限制] 当前数量 - 普通:{len(normal_flows)}, 专注:{len(focused_flows)}")
        stopped = 0

        # 处理普通聊天超额
        if len(normal_flows) > normal_limit:
            excess = len(normal_flows) - normal_limit
            logger.info(f"[限制] 普通聊天超额({len(normal_flows)}>{normal_limit}), 停止{excess}个")
            normal_flows.sort(key=lambda x: x[1])
            for flow_id, _ in normal_flows[:excess]:
                if await self.sleep_subheartflow(flow_id, f"普通聊天超额(限{normal_limit})"):
                    stopped += 1

        # 处理专注聊天超额(需重新统计)
        focused_flows = [
            (fid, t)
            for fid, f in list(self.subheartflows.items())
            if (t := getattr(f, "last_active_time", 0)) and f.chat_state.chat_status == ChatState.FOCUSED
        ]
        if len(focused_flows) > focused_limit:
            excess = len(focused_flows) - focused_limit
            logger.info(f"[限制] 专注聊天超额({len(focused_flows)}>{focused_limit}), 停止{excess}个")
            focused_flows.sort(key=lambda x: x[1])
            for flow_id, _ in focused_flows[:excess]:
                if await self.sleep_subheartflow(flow_id, f"专注聊天超额(限{focused_limit})"):
                    stopped += 1

        if stopped:
            logger.info(f"[限制] 已停止{stopped}个子心流, 剩余:{len(self.subheartflows)}")
        else:
            logger.debug(f"[限制] 无需停止, 当前总数:{len(self.subheartflows)}")

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

                success = await self._try_set_subflow_absent_internal(subflow, log_prefix)

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

    async def check_upcoming_tasks_and_focus(self, upcoming_threshold_seconds: int = 60):
        """
        检查计划任务，并将有任务即将到期的子心流状态设置为 FOCUSED。

        Args:
            upcoming_threshold_seconds: 定义"即将到来"的时间阈值（秒）。默认为 60 秒（1 分钟）。
        """
        log_prefix = "[任务检查聚焦]"
        try:
            scheduler = TaskScheduler.get_instance()
            # 获取当前计划任务的快照（包含时间戳、描述和回调信息）
            # 注意：直接访问 scheduled_tasks 列表，需要确保 TaskScheduler 内部对列表的操作是线程/协程安全的
            # 如果不安全，TaskScheduler 应提供一个获取安全副本的方法
            tasks_snapshot = list(scheduler.scheduled_tasks) # 创建副本以安全迭代

            if not tasks_snapshot:
                # logger.debug(f"{log_prefix} 当前无计划任务。")
                return

            now_ts = datetime.now(timezone.utc).timestamp()
            subflows_to_focus = set() # 使用集合避免重复处理同一个 subflow_id

            # 1. 识别哪些子心流需要被聚焦
            for timestamp, task_description, callback_info in tasks_snapshot:
                # 检查任务是否在未来阈值时间内
                if now_ts < timestamp <= now_ts + upcoming_threshold_seconds:
                    # 尝试从 callback_info 中获取 subheartflow_id
                    # !!! 关键假设：添加任务时 callback_info 中存储了 'subheartflow_id' !!!
                    subheartflow_id = callback_info.get('subheartflow_id')

                    if subheartflow_id:
                        logger.debug(f"{log_prefix} 发现子心流 {subheartflow_id} 的任务 '{task_description}' 即将执行 (剩余 {timestamp - now_ts:.1f} 秒)")
                        subflows_to_focus.add(subheartflow_id)
                    else:
                        logger.warning(f"{log_prefix} 任务 '{task_description}' 即将执行，但在 callback_info 中未找到 'subheartflow_id'。")

            # 2. 对需要聚焦的子心流执行状态切换
            if subflows_to_focus:
                focused_count = 0
                async with self._lock: # 获取锁以安全地修改 subheartflows 字典及其状态
                    for sf_id in subflows_to_focus:
                        subflow = self.subheartflows.get(sf_id)
                        if subflow:
                            # 检查当前状态是否需要切换
                            if subflow.chat_state.chat_status != ChatState.FOCUSED:
                                try:
                                    stream_name = chat_manager.get_stream_name(sf_id) or sf_id
                                    logger.info(f"{log_prefix} 检测到子心流 [{stream_name}] 有任务即将执行，将其状态从 {subflow.chat_state.chat_status.value} 切换到 FOCUSED。")
                                    await subflow.change_chat_state(ChatState.FOCUSED)
                                    # 可以在此验证状态是否真的切换成功
                                    if subflow.chat_state.chat_status == ChatState.FOCUSED:
                                        focused_count += 1
                                    else:
                                         logger.warning(f"{log_prefix} 尝试聚焦 [{stream_name}] 后，状态仍为 {subflow.chat_state.chat_status.value}。")
                                except Exception as e:
                                    logger.error(f"{log_prefix} 切换子心流 {sf_id} 状态到 FOCUSED 时出错: {e}", exc_info=True)
                            else:
                                # 如果已经是 FOCUSED，可以选择记录日志或跳过
                                logger.debug(f"{log_prefix} 子心流 {sf_id} 已处于 FOCUSED 状态。")
                        else:
                            logger.warning(f"{log_prefix} 发现即将执行的任务关联的子心流 {sf_id} 不在当前管理列表中。")
                if focused_count > 0:
                     logger.info(f"{log_prefix} 共将 {focused_count} 个子心流的状态切换到了 FOCUSED。")

        except Exception as e:
            logger.error(f"{log_prefix} 检查即将到来的任务时发生错误: {e}", exc_info=True)

    async def evaluate_interest_and_promote(self):
        """评估子心流兴趣度，满足条件且未达上限则提升到FOCUSED状态（基于start_hfc_probability）"""
        try:
            log_prefix = "[兴趣评估]"
            # 使用 self.mai_state_info 获取当前状态和限制
            current_state = self.mai_state_info.get_current_state()
            focused_limit = current_state.get_focused_chat_max_num()

            logger.debug(f"{log_prefix} 当前状态 ({current_state.value}) 开始尝试提升到FOCUSED状态")

            if int(time.time()) % 20 == 0:  # 每20秒输出一次
                logger.debug(f"{log_prefix} 当前状态 ({current_state.value}) 可以在{focused_limit}个群激情聊天")

            if focused_limit <= 0:
                # logger.debug(f"{log_prefix} 当前状态 ({current_state.value}) 不允许 FOCUSED 子心流")
                return

            current_focused_count = self.count_subflows_by_state(ChatState.FOCUSED)
            if current_focused_count >= focused_limit:
                logger.debug(f"{log_prefix} 已达专注上限 ({current_focused_count}/{focused_limit})")
                return

            for sub_hf in list(self.subheartflows.values()):
                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id

                logger.debug(f"{log_prefix} 检查子心流: {stream_name}，现在状态: {sub_hf.chat_state.chat_status.value}")

                # 跳过非CHAT状态或已经是FOCUSED状态的子心流
                if sub_hf.chat_state.chat_status == ChatState.FOCUSED:
                    continue

                from .mai_state_manager import enable_unlimited_hfc_chat

                if not enable_unlimited_hfc_chat:
                    if sub_hf.chat_state.chat_status != ChatState.CHAT:
                        continue

                # 检查是否满足提升概率
                logger.debug(
                    f"{log_prefix} 检查子心流: {stream_name}，现在概率: {sub_hf.interest_chatting.start_hfc_probability}"
                )
                if random.random() >= sub_hf.interest_chatting.start_hfc_probability:
                    continue

                # 再次检查是否达到上限
                if current_focused_count >= focused_limit:
                    logger.debug(f"{log_prefix} [{stream_name}] 已达专注上限")
                    break

                # 获取最新状态并执行提升
                current_subflow = self.subheartflows.get(flow_id)
                if not current_subflow:
                    continue

                logger.info(
                    f"{log_prefix} [{stream_name}] 触发 认真水群 (概率={current_subflow.interest_chatting.start_hfc_probability:.2f})"
                )

                # 执行状态提升
                await current_subflow.change_chat_state(ChatState.FOCUSED)

                # 验证提升结果
                if (
                    final_subflow := self.subheartflows.get(flow_id)
                ) and final_subflow.chat_state.chat_status == ChatState.FOCUSED:
                    current_focused_count += 1
        except Exception as e:
            logger.error(f"启动HFC 兴趣评估失败: {e}", exc_info=True)

    async def evaluate_and_transition_subflows_by_llm(self):
        """
        使用LLM评估每个子心流的状态，并根据LLM的判断执行状态转换（ABSENT <-> CHAT）。
        注意：此函数包含对假设的LLM函数的调用。
        """
        # 获取当前状态和限制，用于CHAT激活检查
        current_mai_state = self.mai_state_info.get_current_state()
        chat_limit = current_mai_state.get_normal_chat_max_num()

        transitioned_to_chat = 0
        transitioned_to_absent = 0

        async with self._lock:  # 在锁内获取快照并迭代
            subflows_snapshot = list(self.subheartflows.values())
            # 使用不上锁的版本，因为我们已经在锁内
            current_chat_count = self.count_subflows_by_state_nolock(ChatState.CHAT)

            if not subflows_snapshot:
                logger.info("当前没有子心流需要评估。")
                return

            for sub_hf in subflows_snapshot:
                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id
                log_prefix = f"[{stream_name}]"
                current_subflow_state = sub_hf.chat_state.chat_status

                _observation_summary = "没有可用的观察信息。"  # 默认值

                first_observation = sub_hf.observations[0]
                if isinstance(first_observation, ChattingObservation):
                    # 组合中期记忆和当前聊天内容
                    await first_observation.observe()
                    current_chat = first_observation.talking_message_str or "当前无聊天内容。"
                    combined_summary = f"当前聊天内容:\n{current_chat}"
                else:
                    logger.warning(f"{log_prefix} [{stream_name}] 第一个观察者不是 ChattingObservation 类型。")

                # --- 获取麦麦状态 ---
                mai_state_description = f"你当前状态: {current_mai_state.value}。"

                # 获取个性化信息
                individuality = Individuality.get_instance()

                # 构建个性部分
                prompt_personality = f"你正在扮演名为{individuality.personality.bot_nickname}的人类，你"
                prompt_personality += individuality.personality.personality_core

                # 随机添加个性侧面
                if individuality.personality.personality_sides:
                    random_side = random.choice(individuality.personality.personality_sides)
                    prompt_personality += f"，{random_side}"

                # 随机添加身份细节
                if individuality.identity.identity_detail:
                    random_detail = random.choice(individuality.identity.identity_detail)
                    prompt_personality += f"，{random_detail}"

                # --- 针对 ABSENT 状态 ---
                if current_subflow_state == ChatState.ABSENT:
                    # 构建Prompt
                    prompt = (
                        f"{prompt_personality}\n"
                        f"你当前没有在： [{stream_name}] 群中聊天。\n"
                        f"{mai_state_description}\n"
                        f"这个群里最近的聊天内容是:\n---\n{combined_summary}\n---\n"
                        f"基于以上信息，请判断你是否愿意在这个群开始闲聊，"
                        f"进入常规聊天(CHAT)状态？\n"
                        f"给出你的判断，和理由，然后以 JSON 格式回答"
                        f"包含键 'decision'，如果要开始聊天，值为 true ，否则为 false.\n"
                        f"包含键 'reason'，其值为你的理由。\n"
                        f'例如：{{"decision": true, "reason": "因为我想聊天"}}\n'
                        f"请只输出有效的 JSON 对象。"
                    )

                    # 调用LLM评估
                    should_activate = await self._llm_evaluate_state_transition(prompt)
                    if should_activate is None:  # 处理解析失败或意外情况
                        logger.warning(f"{log_prefix}LLM评估返回无效结果，跳过。")
                        continue

                    if should_activate:
                        # 检查CHAT限额
                        # 使用不上锁的版本，因为我们已经在锁内
                        current_chat_count = self.count_subflows_by_state_nolock(ChatState.CHAT)
                        if current_chat_count < chat_limit:
                            logger.info(
                                f"{log_prefix}LLM建议激活到CHAT状态，且未达上限({current_chat_count}/{chat_limit})。正在尝试转换..."
                            )
                            await sub_hf.change_chat_state(ChatState.CHAT)
                            if sub_hf.chat_state.chat_status == ChatState.CHAT:
                                transitioned_to_chat += 1
                            else:
                                logger.warning(f"{log_prefix}尝试激活到CHAT失败。")
                        else:
                            logger.info(
                                f"{log_prefix}LLM建议激活到CHAT状态，但已达到上限({current_chat_count}/{chat_limit})。跳过转换。"
                            )
                    else:
                        logger.info(f"{log_prefix}LLM建议不激活到CHAT状态。")

                # --- 针对 CHAT 状态 ---
                elif current_subflow_state == ChatState.CHAT:
                    # 构建Prompt
                    prompt = (
                        f"{prompt_personality}\n"
                        f"你正在在： [{stream_name}] 群中聊天。\n"
                        f"{mai_state_description}\n"
                        f"这个群里最近的聊天内容是:\n---\n{combined_summary}\n---\n"
                        f"基于以上信息，请判断你是否愿意在这个群继续闲聊，"
                        f"还是暂时离开聊天，进入休眠状态？\n"
                        f"给出你的判断，和理由，然后以 JSON 格式回答"
                        f"包含键 'decision'，如果要离开聊天，值为 true ，否则为 false.\n"
                        f"包含键 'reason'，其值为你的理由。\n"
                        f'例如：{{"decision": true, "reason": "因为我想休息"}}\n'
                        f"请只输出有效的 JSON 对象。"
                    )

                    # 调用LLM评估
                    should_deactivate = await self._llm_evaluate_state_transition(prompt)
                    if should_deactivate is None:  # 处理解析失败或意外情况
                        logger.warning(f"{log_prefix}LLM评估返回无效结果，跳过。")
                        continue

                    if should_deactivate:
                        logger.info(f"{log_prefix}LLM建议进入ABSENT状态。正在尝试转换...")
                        await sub_hf.change_chat_state(ChatState.ABSENT)
                        if sub_hf.chat_state.chat_status == ChatState.ABSENT:
                            transitioned_to_absent += 1
                    else:
                        logger.info(f"{log_prefix}LLM建议不进入ABSENT状态。")

    async def _llm_evaluate_state_transition(self, prompt: str) -> Optional[bool]:
        """
        使用 LLM 评估是否应进行状态转换，期望 LLM 返回 JSON 格式。

        Args:
            prompt: 提供给 LLM 的提示信息，要求返回 {"decision": true/false}。

        Returns:
            Optional[bool]: 如果成功解析 LLM 的 JSON 响应并提取了 'decision' 键的值，则返回该布尔值。
                        如果 LLM 调用失败、返回无效 JSON 或 JSON 中缺少 'decision' 键或其值不是布尔型，则返回 None。
        """
        log_prefix = "[LLM状态评估]"
        try:
            # --- 真实的 LLM 调用 ---
            response_text, _ = await self.llm_state_evaluator.generate_response_async(prompt)
            # logger.debug(f"{log_prefix} 使用模型 {self.llm_state_evaluator.model_name} 评估")
            logger.debug(f"{log_prefix} 原始输入: {prompt}")
            logger.debug(f"{log_prefix} 原始响应: {response_text}")

            # --- 解析 JSON 响应 ---
            try:
                # 尝试去除可能的Markdown代码块标记
                cleaned_response = response_text.strip().strip("`").strip()
                if cleaned_response.startswith("json"):
                    cleaned_response = cleaned_response[4:].strip()

                data = json.loads(cleaned_response)
                decision = data.get("decision")  # 使用 .get() 避免 KeyError

                if isinstance(decision, bool):
                    logger.debug(f"{log_prefix} LLM评估结果 (来自JSON): {'建议转换' if decision else '建议不转换'}")
                    return decision
                else:
                    logger.warning(
                        f"{log_prefix} LLM 返回的 JSON 中 'decision' 键的值不是布尔型: {decision}。响应: {response_text}"
                    )
                    return None  # 值类型不正确

            except json.JSONDecodeError as json_err:
                logger.warning(f"{log_prefix} LLM 返回的响应不是有效的 JSON: {json_err}。响应: {response_text}")
                # 尝试在非JSON响应中查找关键词作为后备方案 (可选)
                if "true" in response_text.lower():
                    logger.debug(f"{log_prefix} 在非JSON响应中找到 'true'，解释为建议转换")
                    return True
                if "false" in response_text.lower():
                    logger.debug(f"{log_prefix} 在非JSON响应中找到 'false'，解释为建议不转换")
                    return False
                return None  # JSON 解析失败，也未找到关键词
            except Exception as parse_err:  # 捕获其他可能的解析错误
                logger.warning(f"{log_prefix} 解析 LLM JSON 响应时发生意外错误: {parse_err}。响应: {response_text}")
                return None

        except Exception as e:
            logger.error(f"{log_prefix} 调用 LLM 或处理其响应时出错: {e}", exc_info=True)
            traceback.print_exc()
            return None  # LLM 调用或处理失败

    def count_subflows_by_state(self, state: ChatState) -> int:
        """统计指定状态的子心流数量"""
        count = 0
        # 遍历所有子心流实例
        for subheartflow in self.subheartflows.values():
            # 检查子心流状态是否匹配
            if subheartflow.chat_state.chat_status == state:
                count += 1
        return count

    def count_subflows_by_state_nolock(self, state: ChatState) -> int:
        """
        统计指定状态的子心流数量 (不上锁版本)。
        警告：仅应在已持有 self._lock 的上下文中使用此方法。
        """
        count = 0
        for subheartflow in self.subheartflows.values():
            if subheartflow.chat_state.chat_status == state:
                count += 1
        return count

    def get_active_subflow_minds(self) -> List[str]:
        """获取所有活跃(非ABSENT)子心流的当前想法"""
        minds = []
        for subheartflow in self.subheartflows.values():
            # 检查子心流是否活跃(非ABSENT状态)
            if subheartflow.chat_state.chat_status != ChatState.ABSENT:
                minds.append(subheartflow.sub_mind.current_mind)
        return minds

    def update_main_mind_in_subflows(self, main_mind: str):
        """更新所有子心流的主心流想法"""
        updated_count = sum(
            1
            for _, subheartflow in list(self.subheartflows.items())
            if subheartflow.subheartflow_id in self.subheartflows
        )
        logger.debug(f"[子心流管理器] 更新了{updated_count}个子心流的主想法")

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

    # --- 新增：处理 HFC 无回复回调的专用方法 --- #
    async def _handle_hfc_no_reply(self, subheartflow_id: Any):
        """处理来自 HeartFChatting 的连续无回复信号 (通过 partial 绑定 ID)"""
        # 注意：这里不需要再获取锁，因为 request_absent_transition 内部会处理锁
        logger.debug(f"[管理器 HFC 处理器] 接收到来自 {subheartflow_id} 的 HFC 无回复信号")
        await self.request_absent_transition(subheartflow_id)

    # --- 结束新增 --- #

    # --- 新增：处理来自 HeartFChatting 的状态转换请求 --- #
    async def request_absent_transition(self, subflow_id: Any):
        """
        接收来自 HeartFChatting 的请求，将特定子心流的状态转换为 ABSENT。
        通常在连续多次 "no_reply" 后被调用。

        Args:
            subflow_id: 需要转换状态的子心流 ID。
        """
        async with self._lock:
            subflow = self.subheartflows.get(subflow_id)
            if not subflow:
                logger.warning(f"[状态转换请求] 尝试转换不存在的子心流 {subflow_id} 到 ABSENT")
                return

            stream_name = chat_manager.get_stream_name(subflow_id) or subflow_id
            current_state = subflow.chat_state.chat_status

            # 仅当子心流处于 FOCUSED 状态时才进行转换
            # 因为 HeartFChatting 只在 FOCUSED 状态下运行
            if current_state == ChatState.FOCUSED:
                logger.info(f"[状态转换请求] 接收到请求，将 {stream_name} (当前: {current_state.value}) 转换为 ABSENT")
                try:
                    await subflow.change_chat_state(ChatState.ABSENT)
                    logger.info(f"[状态转换请求] {stream_name} 状态已成功转换为 ABSENT")
                except Exception as e:
                    logger.error(f"[状态转换请求] 转换 {stream_name} 到 ABSENT 时出错: {e}", exc_info=True)
            elif current_state == ChatState.ABSENT:
                logger.debug(f"[状态转换请求] {stream_name} 已处于 ABSENT 状态，无需转换")
            else:
                logger.warning(
                    f"[状态转换请求] 收到对 {stream_name} 的请求，但其状态为 {current_state.value} (非 FOCUSED)，不执行转换"
                )

    # --- 结束新增 --- #
