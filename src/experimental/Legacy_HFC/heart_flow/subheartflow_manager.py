import asyncio
import time
import random
from typing import Dict, Any, Optional, List, Tuple
import json  # 导入 json 模块
import functools  # <-- 新增导入

# 导入日志模块
from src.common.logger_manager import get_logger

# 导入聊天流管理模块
from src.chat.message_receive.chat_stream import chat_manager

# 导入心流相关类
from .sub_heartflow import SubHeartflow, ChatState
from .mai_state_manager import MaiStateInfo
from .observation import ChattingObservation

# 导入LLM请求工具
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from src.individuality.individuality import Individuality
import traceback


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
                await observation.initialize()

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

    # --- 结束新增 ---

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

    async def sbhf_absent_into_focus(self):
        """评估子心流兴趣度，满足条件且未达上限则提升到FOCUSED状态（基于start_hfc_probability）"""
        try:
            current_state = self.mai_state_info.get_current_state()
            focused_limit = current_state.get_focused_chat_max_num()

            # --- 新增：检查是否允许进入 FOCUS 模式 --- #
            if not global_config.allow_focus_mode:
                if int(time.time()) % 60 == 0:  # 每60秒输出一次日志避免刷屏
                    logger.trace("未开启 FOCUSED 状态 (allow_focus_mode=False)")
                return  # 如果不允许，直接返回
            # --- 结束新增 ---

            logger.debug(f"当前状态 ({current_state.value}) 可以在{focused_limit}个群 专注聊天")

            if focused_limit <= 0:
                # logger.debug(f"{log_prefix} 当前状态 ({current_state.value}) 不允许 FOCUSED 子心流")
                return

            current_focused_count = self.count_subflows_by_state(ChatState.FOCUSED)
            if current_focused_count >= focused_limit:
                logger.debug(f"已达专注上限 ({current_focused_count}/{focused_limit})")
                return

            for sub_hf in list(self.subheartflows.values()):
                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id

                # 跳过非CHAT状态或已经是FOCUSED状态的子心流
                if sub_hf.chat_state.chat_status == ChatState.FOCUSED:
                    continue

                if sub_hf.interest_chatting.start_hfc_probability == 0:
                    continue
                else:
                    logger.debug(
                        f"{stream_name}，现在状态: {sub_hf.chat_state.chat_status.value}，进入专注概率: {sub_hf.interest_chatting.start_hfc_probability}"
                    )

                # 调试用
                from .mai_state_manager import enable_unlimited_hfc_chat

                if not enable_unlimited_hfc_chat:
                    if sub_hf.chat_state.chat_status != ChatState.CHAT:
                        continue

                if random.random() >= sub_hf.interest_chatting.start_hfc_probability:
                    continue

                # 再次检查是否达到上限
                if current_focused_count >= focused_limit:
                    logger.debug(f"{stream_name} 已达专注上限")
                    break

                # 获取最新状态并执行提升
                current_subflow = self.subheartflows.get(flow_id)
                if not current_subflow:
                    continue

                logger.info(
                    f"{stream_name} 触发 认真水群 (概率={current_subflow.interest_chatting.start_hfc_probability:.2f})"
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

    async def sbhf_absent_into_chat(self):
        """
        随机选一个 ABSENT 状态的 *群聊* 子心流，评估是否应转换为 CHAT 状态。
        每次调用最多转换一个。
        私聊会被忽略。
        """
        current_mai_state = self.mai_state_info.get_current_state()
        chat_limit = current_mai_state.get_normal_chat_max_num()

        async with self._lock:
            # 1. 筛选出所有 ABSENT 状态的 *群聊* 子心流
            absent_group_subflows = [
                hf
                for hf in self.subheartflows.values()
                if hf.chat_state.chat_status == ChatState.ABSENT and hf.is_group_chat
            ]

            if not absent_group_subflows:
                # logger.debug("没有摸鱼的群聊子心流可以评估。") # 日志太频繁
                return  # 没有目标，直接返回

            # 2. 随机选一个幸运儿
            sub_hf_to_evaluate = random.choice(absent_group_subflows)
            flow_id = sub_hf_to_evaluate.subheartflow_id
            stream_name = chat_manager.get_stream_name(flow_id) or flow_id
            log_prefix = f"[{stream_name}]"

            # 3. 检查 CHAT 上限
            current_chat_count = self.count_subflows_by_state_nolock(ChatState.CHAT)
            if current_chat_count >= chat_limit:
                logger.info(f"{log_prefix} 想看看能不能聊，但是聊天太多了， ({current_chat_count}/{chat_limit}) 满了。")
                return  # 满了，这次就算了

            # --- 获取 FOCUSED 计数 ---
            current_focused_count = self.count_subflows_by_state_nolock(ChatState.FOCUSED)
            focused_limit = current_mai_state.get_focused_chat_max_num()

            # --- 新增：获取聊天和专注群名 ---
            chatting_group_names = []
            focused_group_names = []
            for flow_id, hf in self.subheartflows.items():
                stream_name = chat_manager.get_stream_name(flow_id) or str(flow_id)  # 保证有名字
                if hf.chat_state.chat_status == ChatState.CHAT:
                    chatting_group_names.append(stream_name)
                elif hf.chat_state.chat_status == ChatState.FOCUSED:
                    focused_group_names.append(stream_name)
            # --- 结束新增 ---

            # --- 获取观察信息和构建 Prompt ---
            first_observation = sub_hf_to_evaluate.observations[0]  # 喵~第一个观察者肯定存在的说
            await first_observation.observe()
            current_chat_log = first_observation.talking_message_str or "当前没啥聊天内容。"
            _observation_summary = f"在[{stream_name}]这个群中，你最近看群友聊了这些:\n{current_chat_log}"

            _mai_state_description = f"你当前状态: {current_mai_state.value}。"
            individuality = Individuality.get_instance()
            personality_prompt = individuality.get_prompt(x_person=2, level=3)
            prompt_personality = f"你是{individuality.name}，{personality_prompt}"

            # --- 修改：在 prompt 中加入当前聊天计数和群名信息 (条件显示) ---
            chat_status_lines = []
            if chatting_group_names:
                chat_status_lines.append(
                    f"正在这些群闲聊 ({current_chat_count}/{chat_limit}): {', '.join(chatting_group_names)}"
                )
            if focused_group_names:
                chat_status_lines.append(
                    f"正在这些群专注的聊天 ({current_focused_count}/{focused_limit}): {', '.join(focused_group_names)}"
                )

            chat_status_prompt = "当前没有在任何群聊中。"  # 默认消息喵~
            if chat_status_lines:
                chat_status_prompt = "当前聊天情况，你已经参与了下面这几个群的聊天：\n" + "\n".join(
                    chat_status_lines
                )  # 拼接状态信息

            prompt = (
                f"{prompt_personality}\n"
                f"{chat_status_prompt}\n"  # <-- 喵！用了新的状态信息~
                f"你当前尚未加入 [{stream_name}] 群聊天。\n"
                f"{_observation_summary}\n---\n"
                f"基于以上信息，你想不想开始在这个群闲聊？\n"
                f"请说明理由，并以 JSON 格式回答，包含 'decision' (布尔值) 和 'reason' (字符串)。\n"
                f'例如：{{"decision": true, "reason": "看起来挺热闹的，插个话"}}\n'
                f'例如：{{"decision": false, "reason": "已经聊了好多，休息一下"}}\n'
                f"请只输出有效的 JSON 对象。"
            )
            # --- 结束修改 ---

            # --- 4. LLM 评估是否想聊 ---
            yao_kai_shi_liao_ma, reason = await self._llm_evaluate_state_transition(prompt)

            if reason:
                if yao_kai_shi_liao_ma:
                    logger.info(f"{log_prefix} 打算开始聊，原因是: {reason}")
                else:
                    logger.info(f"{log_prefix} 不打算聊，原因是: {reason}")
            else:
                logger.info(f"{log_prefix} 结果: {yao_kai_shi_liao_ma}")

            if yao_kai_shi_liao_ma is None:
                logger.debug(f"{log_prefix} 问AI想不想聊失败了，这次算了。")
                return  # 评估失败，结束

            if not yao_kai_shi_liao_ma:
                # logger.info(f"{log_prefix} 现在不想聊这个群。")
                return  # 不想聊，结束

            # --- 5. AI想聊，再次检查额度并尝试转换 ---
            # 再次检查以防万一
            current_chat_count_before_change = self.count_subflows_by_state_nolock(ChatState.CHAT)
            if current_chat_count_before_change < chat_limit:
                logger.info(
                    f"{log_prefix} 想聊，而且还有精力 ({current_chat_count_before_change}/{chat_limit})，这就去聊！"
                )
                await sub_hf_to_evaluate.change_chat_state(ChatState.CHAT)
                # 确认转换成功
                if sub_hf_to_evaluate.chat_state.chat_status == ChatState.CHAT:
                    logger.debug(f"{log_prefix} 成功进入聊天状态！本次评估圆满结束。")
                else:
                    logger.warning(
                        f"{log_prefix} 奇怪，尝试进入聊天状态失败了。当前状态: {sub_hf_to_evaluate.chat_state.chat_status.value}"
                    )
            else:
                logger.warning(
                    f"{log_prefix} AI说想聊，但是刚问完就没空位了 ({current_chat_count_before_change}/{chat_limit})。真不巧，下次再说吧。"
                )
            # 无论转换成功与否，本次评估都结束了

        # 锁在这里自动释放

    # --- 新增：单独检查 CHAT 状态超时的任务 ---
    async def sbhf_chat_into_absent(self):
        """定期检查处于 CHAT 状态的子心流是否因长时间未发言而超时，并将其转为 ABSENT。"""
        log_prefix_task = "[聊天超时检查]"
        transitioned_to_absent = 0
        checked_count = 0

        async with self._lock:
            subflows_snapshot = list(self.subheartflows.values())
            checked_count = len(subflows_snapshot)

            if not subflows_snapshot:
                return

            for sub_hf in subflows_snapshot:
                # 只检查 CHAT 状态的子心流
                if sub_hf.chat_state.chat_status != ChatState.CHAT:
                    continue

                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id
                log_prefix = f"[{stream_name}]({log_prefix_task})"

                should_deactivate = False
                reason = ""

                try:
                    last_bot_dong_zuo_time = sub_hf.get_normal_chat_last_speak_time()

                    if last_bot_dong_zuo_time > 0:
                        current_time = time.time()
                        time_since_last_bb = current_time - last_bot_dong_zuo_time
                        minutes_since_last_bb = time_since_last_bb / 60

                        # 60分钟强制退出
                        if minutes_since_last_bb >= 60:
                            should_deactivate = True
                            reason = "超过60分钟未发言，强制退出"
                        else:
                            # 根据时间区间确定退出概率
                            exit_probability = 0
                            if minutes_since_last_bb < 5:
                                exit_probability = 0.01  # 1%
                            elif minutes_since_last_bb < 15:
                                exit_probability = 0.02  # 2%
                            elif minutes_since_last_bb < 30:
                                exit_probability = 0.04  # 4%
                            else:
                                exit_probability = 0.08  # 8%

                            # 随机判断是否退出
                            if random.random() < exit_probability:
                                should_deactivate = True
                                reason = f"已{minutes_since_last_bb:.1f}分钟未发言，触发{exit_probability * 100:.0f}%退出概率"

                except AttributeError:
                    logger.error(
                        f"{log_prefix} 无法获取 Bot 最后 BB 时间，请确保 SubHeartflow 相关实现正确。跳过超时检查。"
                    )
                except Exception as e:
                    logger.error(f"{log_prefix} 检查 Bot 超时状态时出错: {e}", exc_info=True)

                # 执行状态转换（如果超时）
                if should_deactivate:
                    logger.debug(f"{log_prefix} 因超时 ({reason})，尝试转换为 ABSENT 状态。")
                    await sub_hf.change_chat_state(ChatState.ABSENT)
                    # 再次检查确保状态已改变
                    if sub_hf.chat_state.chat_status == ChatState.ABSENT:
                        transitioned_to_absent += 1
                        logger.info(f"{log_prefix} 不看了。")
                    else:
                        logger.warning(f"{log_prefix} 尝试因超时转换为 ABSENT 失败。")

        if transitioned_to_absent > 0:
            logger.debug(
                f"{log_prefix_task} 完成，共检查 {checked_count} 个子心流，{transitioned_to_absent} 个因超时转为 ABSENT。"
            )

    # --- 结束新增 ---

    async def _llm_evaluate_state_transition(self, prompt: str) -> Tuple[Optional[bool], Optional[str]]:
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
            logger.debug(f"{log_prefix} 原始评估结果: {response_text}")

            # --- 解析 JSON 响应 ---
            try:
                # 尝试去除可能的Markdown代码块标记
                cleaned_response = response_text.strip().strip("`").strip()
                if cleaned_response.startswith("json"):
                    cleaned_response = cleaned_response[4:].strip()

                data = json.loads(cleaned_response)
                decision = data.get("decision")  # 使用 .get() 避免 KeyError
                reason = data.get("reason")

                if isinstance(decision, bool):
                    logger.debug(f"{log_prefix} LLM评估结果 (来自JSON): {'建议转换' if decision else '建议不转换'}")

                    return decision, reason
                else:
                    logger.warning(
                        f"{log_prefix} LLM 返回的 JSON 中 'decision' 键的值不是布尔型: {decision}。响应: {response_text}"
                    )
                    return None, None  # 值类型不正确

            except json.JSONDecodeError as json_err:
                logger.warning(f"{log_prefix} LLM 返回的响应不是有效的 JSON: {json_err}。响应: {response_text}")
                # 尝试在非JSON响应中查找关键词作为后备方案 (可选)
                if "true" in response_text.lower():
                    logger.debug(f"{log_prefix} 在非JSON响应中找到 'true'，解释为建议转换")
                    return True, None
                if "false" in response_text.lower():
                    logger.debug(f"{log_prefix} 在非JSON响应中找到 'false'，解释为建议不转换")
                    return False, None
                return None, None  # JSON 解析失败，也未找到关键词
            except Exception as parse_err:  # 捕获其他可能的解析错误
                logger.warning(f"{log_prefix} 解析 LLM JSON 响应时发生意外错误: {parse_err}。响应: {response_text}")
                return None, None

        except Exception as e:
            logger.error(f"{log_prefix} 调用 LLM 或处理其响应时出错: {e}", exc_info=True)
            traceback.print_exc()
            return None, None  # LLM 调用或处理失败

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
        # 注意：这里不需要再获取锁，因为 sbhf_focus_into_absent 内部会处理锁
        logger.debug(f"[管理器 HFC 处理器] 接收到来自 {subheartflow_id} 的 HFC 无回复信号")
        await self.sbhf_focus_into_absent_or_chat(subheartflow_id)

    # --- 结束新增 --- #

    # --- 新增：处理来自 HeartFChatting 的状态转换请求 --- #
    async def sbhf_focus_into_absent_or_chat(self, subflow_id: Any):
        """
        接收来自 HeartFChatting 的请求，将特定子心流的状态转换为 ABSENT 或 CHAT。
        通常在连续多次 "no_reply" 后被调用。
        对于私聊，总是转换为 ABSENT。
        对于群聊，随机决定转换为 ABSENT 或 CHAT (如果 CHAT 未达上限)。

        Args:
            subflow_id: 需要转换状态的子心流 ID。
        """
        async with self._lock:
            subflow = self.subheartflows.get(subflow_id)
            if not subflow:
                logger.warning(f"[状态转换请求] 尝试转换不存在的子心流 {subflow_id} 到 ABSENT/CHAT")
                return

            stream_name = chat_manager.get_stream_name(subflow_id) or subflow_id
            current_state = subflow.chat_state.chat_status

            if current_state == ChatState.FOCUSED:
                target_state = ChatState.ABSENT  # Default target
                log_reason = "默认转换 (私聊或群聊)"

                # --- Modify logic based on chat type --- #
                if subflow.is_group_chat:
                    # Group chat: Decide between ABSENT or CHAT
                    if random.random() < 0.5:  # 50% chance to try CHAT
                        current_mai_state = self.mai_state_info.get_current_state()
                        chat_limit = current_mai_state.get_normal_chat_max_num()
                        current_chat_count = self.count_subflows_by_state_nolock(ChatState.CHAT)

                        if current_chat_count < chat_limit:
                            target_state = ChatState.CHAT
                            log_reason = f"群聊随机选择 CHAT (当前 {current_chat_count}/{chat_limit})"
                        else:
                            target_state = ChatState.ABSENT  # Fallback to ABSENT if CHAT limit reached
                            log_reason = (
                                f"群聊随机选择 CHAT 但已达上限 ({current_chat_count}/{chat_limit})，转为 ABSENT"
                            )
                    else:  # 50% chance to go directly to ABSENT
                        target_state = ChatState.ABSENT
                        log_reason = "群聊随机选择 ABSENT"
                else:
                    # Private chat: Always go to ABSENT
                    target_state = ChatState.ABSENT
                    log_reason = "私聊退出 FOCUSED，转为 ABSENT"
                # --- End modification --- #

                logger.info(
                    f"[状态转换请求] 接收到请求，将 {stream_name} (当前: {current_state.value}) 尝试转换为 {target_state.value} ({log_reason})"
                )
                try:
                    # 从HFC到CHAT时，清空兴趣字典
                    subflow.clear_interest_dict()
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
                logger.debug(f"[状态转换请求] {stream_name} 已处于 ABSENT 状态，无需转换")
            else:
                logger.warning(
                    f"[状态转换请求] 收到对 {stream_name} 的请求，但其状态为 {current_state.value} (非 FOCUSED)，不执行转换"
                )

    # --- 结束新增 --- #

    # --- 新增：处理私聊从 ABSENT 直接到 FOCUSED 的逻辑 --- #
    async def sbhf_absent_private_into_focus(self):
        """检查 ABSENT 状态的私聊子心流是否有新活动，若有且未达 FOCUSED 上限，则直接转换为 FOCUSED。"""
        log_prefix_task = "[私聊激活检查]"
        transitioned_count = 0
        checked_count = 0

        # --- 获取当前状态和 FOCUSED 上限 --- #
        current_mai_state = self.mai_state_info.get_current_state()
        focused_limit = current_mai_state.get_focused_chat_max_num()

        # --- 检查是否允许 FOCUS 模式 --- #
        if not global_config.allow_focus_mode:
            # Log less frequently to avoid spam
            # if int(time.time()) % 60 == 0:
            #     logger.debug(f"{log_prefix_task} 配置不允许进入 FOCUSED 状态")
            return

        if focused_limit <= 0:
            # logger.debug(f"{log_prefix_task} 当前状态 ({current_mai_state.value}) 不允许 FOCUSED 子心流")
            return

        async with self._lock:
            # --- 获取当前 FOCUSED 计数 (不上锁版本) --- #
            current_focused_count = self.count_subflows_by_state_nolock(ChatState.FOCUSED)

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
                # --- 再次检查 FOCUSED 上限，因为可能有多个同时激活 --- #
                if current_focused_count >= focused_limit:
                    logger.debug(
                        f"{log_prefix_task} 已达专注上限 ({current_focused_count}/{focused_limit})，停止检查后续私聊。"
                    )
                    break  # 已满，无需再检查其他私聊

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

                    # --- 如果活跃且未达上限，则尝试转换 --- #
                    if is_active:
                        logger.info(
                            f"{log_prefix} 检测到活跃且未达专注上限 ({current_focused_count}/{focused_limit})，尝试转换为 FOCUSED。"
                        )
                        await sub_hf.change_chat_state(ChatState.FOCUSED)
                        # 确认转换成功
                        if sub_hf.chat_state.chat_status == ChatState.FOCUSED:
                            transitioned_count += 1
                            current_focused_count += 1  # 更新计数器以供本轮后续检查
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
