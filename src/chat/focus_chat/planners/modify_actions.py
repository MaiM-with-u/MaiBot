from typing import List, Optional, Any, Dict
from src.chat.heart_flow.observation.observation import Observation
from src.common.logger import get_logger
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.message_receive.chat_stream import get_chat_manager
from src.config.config import global_config
from src.llm_models.utils_model import LLMRequest
import random
import asyncio
import hashlib
import time
from src.chat.focus_chat.planners.action_manager import ActionManager

logger = get_logger("action_manager")


class ActionModifier:
    """动作处理器

    用于处理Observation对象和根据激活类型处理actions。
    集成了原有的modify_actions功能和新的激活类型处理功能。
    支持并行判定和智能缓存优化。
    """

    log_prefix = "动作处理"

    def __init__(self, action_manager: ActionManager):
        """初始化动作处理器"""
        self.action_manager = action_manager
        self.all_actions = self.action_manager.get_using_actions_for_mode("focus")

        # 用于LLM判定的小模型
        self.llm_judge = LLMRequest(
            model=global_config.model.utils_small,
            request_type="action.judge",
        )

        # 缓存相关属性
        self._llm_judge_cache = {}  # 缓存LLM判定结果
        self._cache_expiry_time = 30  # 缓存过期时间（秒）
        self._last_context_hash = None  # 上次上下文的哈希值

    async def modify_actions(
        self,
        observations: Optional[List[Observation]] = None,
        **kwargs: Any,
    ):
        """
        完整的动作修改流程，整合传统观察处理和新的激活类型判定

        这个方法处理完整的动作管理流程：
        1. 基于观察的传统动作修改（循环历史分析、类型匹配等）
        2. 基于激活类型的智能动作判定，最终确定可用动作集

        处理后，ActionManager 将包含最终的可用动作集，供规划器直接使用
        """
        logger.debug(f"{self.log_prefix}开始完整动作修改流程")

        # === 第一阶段：传统观察处理 ===
        chat_content = None

        if observations:
            hfc_obs = None
            chat_obs = None

            # 收集所有观察对象
            for obs in observations:
                if isinstance(obs, HFCloopObservation):
                    hfc_obs = obs
                if isinstance(obs, ChattingObservation):
                    chat_obs = obs
                    chat_content = obs.talking_message_str_truncate_short

            # 合并所有动作变更
            merged_action_changes = {"add": [], "remove": []}
            reasons = []

            # 处理HFCloopObservation - 传统的循环历史分析
            if hfc_obs:
                obs = hfc_obs
                # 获取适用于FOCUS模式的动作
                all_actions = self.all_actions
                action_changes = await self.analyze_loop_actions(obs)
                if action_changes["add"] or action_changes["remove"]:
                    # 合并动作变更
                    merged_action_changes["add"].extend(action_changes["add"])
                    merged_action_changes["remove"].extend(action_changes["remove"])
                    reasons.append("基于循环历史分析")

                    # 详细记录循环历史分析的变更原因
                    for action_name in action_changes["add"]:
                        logger.info(f"{self.log_prefix}添加动作: {action_name}，原因: 循环历史分析建议添加")
                    for action_name in action_changes["remove"]:
                        logger.info(f"{self.log_prefix}移除动作: {action_name}，原因: 循环历史分析建议移除")

            # 处理ChattingObservation - 传统的类型匹配检查
            if chat_obs:
                # 检查动作的关联类型
                chat_context = get_chat_manager().get_stream(chat_obs.chat_id).context
                type_mismatched_actions = []

                for action_name in all_actions.keys():
                    data = all_actions[action_name]
                    if data.get("associated_types"):
                        if not chat_context.check_types(data["associated_types"]):
                            type_mismatched_actions.append(action_name)
                            associated_types_str = ", ".join(data["associated_types"])
                            logger.info(
                                f"{self.log_prefix}移除动作: {action_name}，原因: 关联类型不匹配（需要: {associated_types_str}）"
                            )

                if type_mismatched_actions:
                    # 合并到移除列表中
                    merged_action_changes["remove"].extend(type_mismatched_actions)
                    reasons.append("基于关联类型检查")

            # 应用传统的动作变更到ActionManager
            for action_name in merged_action_changes["add"]:
                if action_name in self.action_manager.get_registered_actions():
                    self.action_manager.add_action_to_using(action_name)
                    logger.debug(f"{self.log_prefix}应用添加动作: {action_name}，原因集合: {reasons}")

            for action_name in merged_action_changes["remove"]:
                self.action_manager.remove_action_from_using(action_name)
                logger.debug(f"{self.log_prefix}应用移除动作: {action_name}，原因集合: {reasons}")

            logger.info(
                f"{self.log_prefix}传统动作修改完成，当前使用动作: {list(self.action_manager.get_using_actions().keys())}"
            )

        # 注释：已移除exit_focus_chat动作，现在由no_reply动作处理频率检测退出专注模式

        # === 第二阶段：激活类型判定 ===
        # 如果提供了聊天上下文，则进行激活类型判定
        if chat_content is not None:
            logger.debug(f"{self.log_prefix}开始激活类型判定阶段")

            # 获取当前使用的动作集（经过第一阶段处理，且适用于FOCUS模式）
            current_using_actions = self.action_manager.get_using_actions()
            all_registered_actions = self.action_manager.get_registered_actions()

            # 构建完整的动作信息
            current_actions_with_info = {}
            for action_name in current_using_actions.keys():
                if action_name in all_registered_actions:
                    current_actions_with_info[action_name] = all_registered_actions[action_name]
                else:
                    logger.warning(f"{self.log_prefix}使用中的动作 {action_name} 未在已注册动作中找到")

            # 应用激活类型判定
            final_activated_actions = await self._apply_activation_type_filtering(
                current_actions_with_info,
                chat_content,
            )

            # 更新ActionManager，移除未激活的动作
            actions_to_remove = []
            removal_reasons = {}

            for action_name in current_using_actions.keys():
                if action_name not in final_activated_actions:
                    actions_to_remove.append(action_name)
                    # 确定移除原因
                    if action_name in all_registered_actions:
                        action_info = all_registered_actions[action_name]
                        activation_type = action_info.get("focus_activation_type", "always")

                        # 处理字符串格式的激活类型值
                        if activation_type == "random":
                            probability = action_info.get("random_probability", 0.3)
                            removal_reasons[action_name] = f"RANDOM类型未触发（概率{probability}）"
                        elif activation_type == "llm_judge":
                            removal_reasons[action_name] = "LLM判定未激活"
                        elif activation_type == "keyword":
                            keywords = action_info.get("activation_keywords", [])
                            removal_reasons[action_name] = f"关键词未匹配（关键词: {keywords}）"
                        else:
                            removal_reasons[action_name] = "激活判定未通过"
                    else:
                        removal_reasons[action_name] = "动作信息不完整"

            for action_name in actions_to_remove:
                self.action_manager.remove_action_from_using(action_name)
                reason = removal_reasons.get(action_name, "未知原因")
                logger.info(f"{self.log_prefix}移除动作: {action_name}，原因: {reason}")

            # 注释：已完全移除exit_focus_chat动作

            logger.info(f"{self.log_prefix}激活类型判定完成，最终可用动作: {list(final_activated_actions.keys())}")

        logger.info(
            f"{self.log_prefix}完整动作修改流程结束，最终动作集: {list(self.action_manager.get_using_actions().keys())}"
        )

    async def _apply_activation_type_filtering(
        self,
        actions_with_info: Dict[str, Any],
        chat_content: str = "",
    ) -> Dict[str, Any]:
        """
        应用激活类型过滤逻辑，支持四种激活类型的并行处理

        Args:
            actions_with_info: 带完整信息的动作字典
            chat_content: 聊天内容

        Returns:
            Dict[str, Any]: 过滤后激活的actions字典
        """
        activated_actions = {}

        # 分类处理不同激活类型的actions
        always_actions = {}
        random_actions = {}
        llm_judge_actions = {}
        keyword_actions = {}

        for action_name, action_info in actions_with_info.items():
            activation_type = action_info.get("focus_activation_type", "always")

            # print(f"action_name: {action_name}, activation_type: {activation_type}")

            # 现在统一是字符串格式的激活类型值
            if activation_type == "always":
                always_actions[action_name] = action_info
            elif activation_type == "random":
                random_actions[action_name] = action_info
            elif activation_type == "llm_judge":
                llm_judge_actions[action_name] = action_info
            elif activation_type == "keyword":
                keyword_actions[action_name] = action_info
            else:
                logger.warning(f"{self.log_prefix}未知的激活类型: {activation_type}，跳过处理")

        # 1. 处理ALWAYS类型（直接激活）
        for action_name, action_info in always_actions.items():
            activated_actions[action_name] = action_info
            logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: ALWAYS类型直接激活")

        # 2. 处理RANDOM类型
        for action_name, action_info in random_actions.items():
            probability = action_info.get("random_activation_probability", ActionManager.DEFAULT_RANDOM_PROBABILITY)
            should_activate = random.random() < probability
            if should_activate:
                activated_actions[action_name] = action_info
                logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: RANDOM类型触发（概率{probability}）")
            else:
                logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: RANDOM类型未触发（概率{probability}）")

        # 3. 处理KEYWORD类型（快速判定）
        for action_name, action_info in keyword_actions.items():
            should_activate = self._check_keyword_activation(
                action_name,
                action_info,
                chat_content,
            )
            if should_activate:
                activated_actions[action_name] = action_info
                keywords = action_info.get("activation_keywords", [])
                logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: KEYWORD类型匹配关键词（{keywords}）")
            else:
                keywords = action_info.get("activation_keywords", [])
                logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: KEYWORD类型未匹配关键词（{keywords}）")

        # 4. 处理LLM_JUDGE类型（并行判定）
        if llm_judge_actions:
            # 直接并行处理所有LLM判定actions
            llm_results = await self._process_llm_judge_actions_parallel(
                llm_judge_actions,
                chat_content,
            )

            # 添加激活的LLM判定actions
            for action_name, should_activate in llm_results.items():
                if should_activate:
                    activated_actions[action_name] = llm_judge_actions[action_name]
                    logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: LLM_JUDGE类型判定通过")
                else:
                    logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: LLM_JUDGE类型判定未通过")

        logger.debug(f"{self.log_prefix}激活类型过滤完成: {list(activated_actions.keys())}")
        return activated_actions

    async def process_actions_for_planner(
        self, observed_messages_str: str = "", chat_context: Optional[str] = None, extra_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        [已废弃] 此方法现在已被整合到 modify_actions() 中

        为了保持向后兼容性而保留，但建议直接使用 ActionManager.get_using_actions()
        规划器应该直接从 ActionManager 获取最终的可用动作集，而不是调用此方法

        新的架构：
        1. 主循环调用 modify_actions() 处理完整的动作管理流程
        2. 规划器直接使用 ActionManager.get_using_actions() 获取最终动作集
        """
        logger.warning(
            f"{self.log_prefix}process_actions_for_planner() 已废弃，建议规划器直接使用 ActionManager.get_using_actions()"
        )

        # 为了向后兼容，仍然返回当前使用的动作集
        current_using_actions = self.action_manager.get_using_actions()
        all_registered_actions = self.action_manager.get_registered_actions()

        # 构建完整的动作信息
        result = {}
        for action_name in current_using_actions.keys():
            if action_name in all_registered_actions:
                result[action_name] = all_registered_actions[action_name]

        return result

    def _generate_context_hash(self, chat_content: str) -> str:
        """生成上下文的哈希值用于缓存"""
        context_content = f"{chat_content}"
        return hashlib.md5(context_content.encode("utf-8")).hexdigest()

    async def _process_llm_judge_actions_parallel(
        self,
        llm_judge_actions: Dict[str, Any],
        chat_content: str = "",
    ) -> Dict[str, bool]:
        """
        并行处理LLM判定actions，支持智能缓存

        Args:
            llm_judge_actions: 需要LLM判定的actions
            chat_content: 聊天内容

        Returns:
            Dict[str, bool]: action名称到激活结果的映射
        """

        # 生成当前上下文的哈希值
        current_context_hash = self._generate_context_hash(chat_content)
        current_time = time.time()

        results = {}
        tasks_to_run = {}

        # 检查缓存
        for action_name, action_info in llm_judge_actions.items():
            cache_key = f"{action_name}_{current_context_hash}"

            # 检查是否有有效的缓存
            if (
                cache_key in self._llm_judge_cache
                and current_time - self._llm_judge_cache[cache_key]["timestamp"] < self._cache_expiry_time
            ):
                results[action_name] = self._llm_judge_cache[cache_key]["result"]
                logger.debug(
                    f"{self.log_prefix}使用缓存结果 {action_name}: {'激活' if results[action_name] else '未激活'}"
                )
            else:
                # 需要进行LLM判定
                tasks_to_run[action_name] = action_info

        # 如果有需要运行的任务，并行执行
        if tasks_to_run:
            logger.debug(f"{self.log_prefix}并行执行LLM判定，任务数: {len(tasks_to_run)}")

            # 创建并行任务
            tasks = []
            task_names = []

            for action_name, action_info in tasks_to_run.items():
                task = self._llm_judge_action(
                    action_name,
                    action_info,
                    chat_content,
                )
                tasks.append(task)
                task_names.append(action_name)

            # 并行执行所有任务
            try:
                task_results = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果并更新缓存
                for _, (action_name, result) in enumerate(zip(task_names, task_results)):
                    if isinstance(result, Exception):
                        logger.error(f"{self.log_prefix}LLM判定action {action_name} 时出错: {result}")
                        results[action_name] = False
                    else:
                        results[action_name] = result

                        # 更新缓存
                        cache_key = f"{action_name}_{current_context_hash}"
                        self._llm_judge_cache[cache_key] = {"result": result, "timestamp": current_time}

                logger.debug(f"{self.log_prefix}并行LLM判定完成，耗时: {time.time() - current_time:.2f}s")

            except Exception as e:
                logger.error(f"{self.log_prefix}并行LLM判定失败: {e}")
                # 如果并行执行失败，为所有任务返回False
                for action_name in tasks_to_run.keys():
                    results[action_name] = False

        # 清理过期缓存
        self._cleanup_expired_cache(current_time)

        return results

    def _cleanup_expired_cache(self, current_time: float):
        """清理过期的缓存条目"""
        expired_keys = []
        for cache_key, cache_data in self._llm_judge_cache.items():
            if current_time - cache_data["timestamp"] > self._cache_expiry_time:
                expired_keys.append(cache_key)

        for key in expired_keys:
            del self._llm_judge_cache[key]

        if expired_keys:
            logger.debug(f"{self.log_prefix}清理了 {len(expired_keys)} 个过期缓存条目")

    async def _llm_judge_action(
        self,
        action_name: str,
        action_info: Dict[str, Any],
        chat_content: str = "",
    ) -> bool:
        """
        使用LLM判定是否应该激活某个action

        Args:
            action_name: 动作名称
            action_info: 动作信息
            observed_messages_str: 观察到的聊天消息
            chat_context: 聊天上下文
            extra_context: 额外上下文

        Returns:
            bool: 是否应该激活此action
        """

        try:
            # 构建判定提示词
            action_description = action_info.get("description", "")
            action_require = action_info.get("require", [])
            custom_prompt = action_info.get("llm_judge_prompt", "")

            # 构建基础判定提示词
            base_prompt = f"""
你需要判断在当前聊天情况下，是否应该激活名为"{action_name}"的动作。

动作描述：{action_description}

动作使用场景：
"""
            for req in action_require:
                base_prompt += f"- {req}\n"

            if custom_prompt:
                base_prompt += f"\n额外判定条件：\n{custom_prompt}\n"

            if chat_content:
                base_prompt += f"\n当前聊天记录：\n{chat_content}\n"

            base_prompt += """
请根据以上信息判断是否应该激活这个动作。
只需要回答"是"或"否"，不要有其他内容。
"""

            # 调用LLM进行判定
            response, _ = await self.llm_judge.generate_response_async(prompt=base_prompt)

            # 解析响应
            response = response.strip().lower()

            # print(base_prompt)
            print(f"LLM判定动作 {action_name}：响应='{response}'")

            should_activate = "是" in response or "yes" in response or "true" in response

            logger.debug(
                f"{self.log_prefix}LLM判定动作 {action_name}：响应='{response}'，结果={'激活' if should_activate else '不激活'}"
            )
            return should_activate

        except Exception as e:
            logger.error(f"{self.log_prefix}LLM判定动作 {action_name} 时出错: {e}")
            # 出错时默认不激活
            return False

    def _check_keyword_activation(
        self,
        action_name: str,
        action_info: Dict[str, Any],
        chat_content: str = "",
    ) -> bool:
        """
        检查是否匹配关键词触发条件

        Args:
            action_name: 动作名称
            action_info: 动作信息
            observed_messages_str: 观察到的聊天消息
            chat_context: 聊天上下文
            extra_context: 额外上下文

        Returns:
            bool: 是否应该激活此action
        """

        activation_keywords = action_info.get("activation_keywords", [])
        case_sensitive = action_info.get("keyword_case_sensitive", False)

        if not activation_keywords:
            logger.warning(f"{self.log_prefix}动作 {action_name} 设置为关键词触发但未配置关键词")
            return False

        # 构建检索文本
        search_text = ""
        if chat_content:
            search_text += chat_content
        # if chat_context:
        # search_text += f" {chat_context}"
        # if extra_context:
        # search_text += f" {extra_context}"

        # 如果不区分大小写，转换为小写
        if not case_sensitive:
            search_text = search_text.lower()

        # 检查每个关键词
        matched_keywords = []
        for keyword in activation_keywords:
            check_keyword = keyword if case_sensitive else keyword.lower()
            if check_keyword in search_text:
                matched_keywords.append(keyword)

        if matched_keywords:
            logger.debug(f"{self.log_prefix}动作 {action_name} 匹配到关键词: {matched_keywords}")
            return True
        else:
            logger.debug(f"{self.log_prefix}动作 {action_name} 未匹配到任何关键词: {activation_keywords}")
            return False

    async def analyze_loop_actions(self, obs: HFCloopObservation) -> Dict[str, List[str]]:
        """分析最近的循环内容并决定动作的增减

        Returns:
            Dict[str, List[str]]: 包含要增加和删除的动作
                {
                    "add": ["action1", "action2"],
                    "remove": ["action3"]
                }
        """
        result = {"add": [], "remove": []}

        # 获取最近10次循环
        recent_cycles = obs.history_loop[-10:] if len(obs.history_loop) > 10 else obs.history_loop
        if not recent_cycles:
            return result

        reply_sequence = []  # 记录最近的动作序列

        for cycle in recent_cycles:
            action_result = cycle.loop_plan_info.get("action_result", {})
            action_type = action_result.get("action_type", "unknown")
            reply_sequence.append(action_type == "reply")

        # 计算连续回复的相关阈值

        max_reply_num = int(global_config.focus_chat.consecutive_replies * 3.2)
        sec_thres_reply_num = int(global_config.focus_chat.consecutive_replies * 2)
        one_thres_reply_num = int(global_config.focus_chat.consecutive_replies * 1.5)

        # 获取最近max_reply_num次的reply状态
        if len(reply_sequence) >= max_reply_num:
            last_max_reply_num = reply_sequence[-max_reply_num:]
        else:
            last_max_reply_num = reply_sequence[:]

        # 详细打印阈值和序列信息，便于调试
        logger.info(
            f"连续回复阈值: max={max_reply_num}, sec={sec_thres_reply_num}, one={one_thres_reply_num}，"
            f"最近reply序列: {last_max_reply_num}"
        )
        # print(f"consecutive_replies: {consecutive_replies}")

        # 根据最近的reply情况决定是否移除reply动作
        if len(last_max_reply_num) >= max_reply_num and all(last_max_reply_num):
            # 如果最近max_reply_num次都是reply，直接移除
            result["remove"].append("reply")
            # reply_count = len(last_max_reply_num) - no_reply_count
            logger.info(
                f"{self.log_prefix}移除reply动作，原因: 连续回复过多（最近{len(last_max_reply_num)}次全是reply，超过阈值{max_reply_num}）"
            )
        elif len(last_max_reply_num) >= sec_thres_reply_num and all(last_max_reply_num[-sec_thres_reply_num:]):
            # 如果最近sec_thres_reply_num次都是reply，40%概率移除
            removal_probability = 0.4 / global_config.focus_chat.consecutive_replies
            if random.random() < removal_probability:
                result["remove"].append("reply")
                logger.info(
                    f"{self.log_prefix}移除reply动作，原因: 连续回复较多（最近{sec_thres_reply_num}次全是reply，{removal_probability:.2f}概率移除，触发移除）"
                )
            else:
                logger.debug(
                    f"{self.log_prefix}连续回复检测：最近{sec_thres_reply_num}次全是reply，{removal_probability:.2f}概率移除，未触发"
                )
        elif len(last_max_reply_num) >= one_thres_reply_num and all(last_max_reply_num[-one_thres_reply_num:]):
            # 如果最近one_thres_reply_num次都是reply，20%概率移除
            removal_probability = 0.2 / global_config.focus_chat.consecutive_replies
            if random.random() < removal_probability:
                result["remove"].append("reply")
                logger.info(
                    f"{self.log_prefix}移除reply动作，原因: 连续回复检测（最近{one_thres_reply_num}次全是reply，{removal_probability:.2f}概率移除，触发移除）"
                )
            else:
                logger.debug(
                    f"{self.log_prefix}连续回复检测：最近{one_thres_reply_num}次全是reply，{removal_probability:.2f}概率移除，未触发"
                )
        else:
            logger.debug(f"{self.log_prefix}连续回复检测：无需移除reply动作，最近回复模式正常")

        return result
