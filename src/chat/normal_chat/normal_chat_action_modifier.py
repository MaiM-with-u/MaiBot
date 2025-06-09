from typing import List, Any, Dict
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.chat.actions.base_action import ActionActivationType, ChatMode
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
from src.config.config import global_config
import random
import time

logger = get_logger("normal_chat_action_modifier")


class NormalChatActionModifier:
    """Normal Chat动作修改器

    负责根据Normal Chat的上下文和状态动态调整可用的动作集合
    实现与Focus Chat类似的动作激活策略，但将LLM_JUDGE转换为概率激活以提升性能
    """

    def __init__(self, action_manager: ActionManager, stream_id: str, stream_name: str):
        """初始化动作修改器"""
        self.action_manager = action_manager
        self.stream_id = stream_id
        self.stream_name = stream_name
        self.log_prefix = f"[{stream_name}]动作修改器"

        # 缓存所有注册的动作
        self.all_actions = self.action_manager.get_registered_actions()

    async def modify_actions_for_normal_chat(
        self,
        chat_stream,
        recent_replies: List[dict],
        message_content: str,
        **kwargs: Any,
    ):
        """为Normal Chat修改可用动作集合
        
        实现动作激活策略：
        1. 基于关联类型的动态过滤
        2. 基于激活类型的智能判定（LLM_JUDGE转为概率激活）

        Args:
            chat_stream: 聊天流对象
            recent_replies: 最近的回复记录
            **kwargs: 其他参数
        """

        reasons = []
        merged_action_changes = {"add": [], "remove": []}
        type_mismatched_actions = []  # 在外层定义避免作用域问题
        
        self.action_manager.restore_default_actions()

        # 第一阶段：基于关联类型的动态过滤
        if chat_stream:
            chat_context = chat_stream.context if hasattr(chat_stream, "context") else None
            if chat_context:
                # 获取Normal模式下的可用动作（已经过滤了mode_enable）
                current_using_actions = self.action_manager.get_using_actions_for_mode(ChatMode.NORMAL)
                # print(f"current_using_actions: {current_using_actions}")
                for action_name in current_using_actions.keys():
                    if action_name in self.all_actions:
                        data = self.all_actions[action_name]
                        if data.get("associated_types"):
                            if not chat_context.check_types(data["associated_types"]):
                                type_mismatched_actions.append(action_name)
                                logger.debug(f"{self.log_prefix} 动作 {action_name} 关联类型不匹配，移除该动作")

                if type_mismatched_actions:
                    merged_action_changes["remove"].extend(type_mismatched_actions)
                    reasons.append(f"移除{type_mismatched_actions}(关联类型不匹配)")

        # 第二阶段：应用激活类型判定
        # 构建聊天内容 - 使用与planner一致的方式
        chat_content = ""
        if chat_stream and hasattr(chat_stream, 'stream_id'):
            try:
                # 获取消息历史，使用与normal_chat_planner相同的方法
                message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                    chat_id=chat_stream.stream_id,
                    timestamp=time.time(),
                    limit=global_config.focus_chat.observation_context_size,  # 使用相同的配置
                )
                
                # 构建可读的聊天上下文
                chat_content = build_readable_messages(
                    message_list_before_now,
                    replace_bot_name=True,
                    merge_messages=False,
                    timestamp_mode="relative",
                    read_mark=0.0,
                    show_actions=True,
                )
                
                logger.debug(f"{self.log_prefix} 成功构建聊天内容，长度: {len(chat_content)}")
                
            except Exception as e:
                logger.warning(f"{self.log_prefix} 构建聊天内容失败: {e}")
                chat_content = ""
        
        # 获取当前Normal模式下的动作集进行激活判定
        current_actions = self.action_manager.get_using_actions_for_mode(ChatMode.NORMAL)

        # print(f"current_actions: {current_actions}")
        # print(f"chat_content: {chat_content}")
        final_activated_actions = await self._apply_normal_activation_filtering(
            current_actions,
            chat_content,
            message_content
        )
        # print(f"final_activated_actions: {final_activated_actions}")
        
        # 统一处理所有需要移除的动作，避免重复移除
        all_actions_to_remove = set()  # 使用set避免重复
        
        # 添加关联类型不匹配的动作
        if type_mismatched_actions:
            all_actions_to_remove.update(type_mismatched_actions)
        
        # 添加激活类型判定未通过的动作
        for action_name in current_actions.keys():
            if action_name not in final_activated_actions:
                all_actions_to_remove.add(action_name)
        
        # 统计移除原因（避免重复）
        activation_failed_actions = [name for name in current_actions.keys() if name not in final_activated_actions and name not in type_mismatched_actions]
        if activation_failed_actions:
            reasons.append(f"移除{activation_failed_actions}(激活类型判定未通过)")

        # 统一执行移除操作
        for action_name in all_actions_to_remove:
            success = self.action_manager.remove_action_from_using(action_name)
            if success:
                logger.debug(f"{self.log_prefix} 移除动作: {action_name}")
            else:
                logger.debug(f"{self.log_prefix} 动作 {action_name} 已经不在使用集中，跳过移除")

        # 应用动作添加（如果有的话）
        for action_name in merged_action_changes["add"]:
            if action_name in self.all_actions:
                success = self.action_manager.add_action_to_using(action_name)
                if success:
                    logger.debug(f"{self.log_prefix} 添加动作: {action_name}")

        # 记录变更原因
        if reasons:
            logger.info(f"{self.log_prefix} 动作调整完成: {' | '.join(reasons)}")
            
        # 获取最终的Normal模式可用动作并记录
        final_actions = self.action_manager.get_using_actions_for_mode(ChatMode.NORMAL)
        logger.debug(f"{self.log_prefix} 当前Normal模式可用动作: {list(final_actions.keys())}")

    async def _apply_normal_activation_filtering(
        self,
        actions_with_info: Dict[str, Any],
        chat_content: str = "",
        message_content: str = "",
    ) -> Dict[str, Any]:
        """
        应用Normal模式的激活类型过滤逻辑
        
        与Focus模式的区别：
        1. LLM_JUDGE类型转换为概率激活（避免LLM调用）
        2. RANDOM类型保持概率激活
        3. KEYWORD类型保持关键词匹配
        4. ALWAYS类型直接激活
        
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
        keyword_actions = {}
        
        for action_name, action_info in actions_with_info.items():
            # 使用normal_activation_type
            activation_type = action_info.get("normal_activation_type", ActionActivationType.ALWAYS)
            
            if activation_type == ActionActivationType.ALWAYS:
                always_actions[action_name] = action_info
            elif activation_type == ActionActivationType.RANDOM or activation_type == ActionActivationType.LLM_JUDGE:
                random_actions[action_name] = action_info
            elif activation_type == ActionActivationType.KEYWORD:
                keyword_actions[action_name] = action_info
            else:
                logger.warning(f"{self.log_prefix}未知的激活类型: {activation_type}，跳过处理")
        
        # 1. 处理ALWAYS类型（直接激活）
        for action_name, action_info in always_actions.items():
            activated_actions[action_name] = action_info
            logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: ALWAYS类型直接激活")
        
        # 2. 处理RANDOM类型（概率激活）
        for action_name, action_info in random_actions.items():
            probability = action_info.get("random_probability", 0.3)
            should_activate = random.random() < probability
            if should_activate:
                activated_actions[action_name] = action_info
                logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: RANDOM类型触发（概率{probability}）")
            else:
                logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: RANDOM类型未触发（概率{probability}）")
        
        # 3. 处理KEYWORD类型（关键词匹配）
        for action_name, action_info in keyword_actions.items():
            should_activate = self._check_keyword_activation(
                action_name,
                action_info,
                chat_content,
                message_content
            )
            if should_activate:
                activated_actions[action_name] = action_info
                keywords = action_info.get("activation_keywords", [])
                logger.debug(f"{self.log_prefix}激活动作: {action_name}，原因: KEYWORD类型匹配关键词（{keywords}）")
            else:
                keywords = action_info.get("activation_keywords", [])
                logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: KEYWORD类型未匹配关键词（{keywords}）")
                # print(f"keywords: {keywords}")
                # print(f"chat_content: {chat_content}")
        
        logger.debug(f"{self.log_prefix}Normal模式激活类型过滤完成: {list(activated_actions.keys())}")
        return activated_actions

    def _check_keyword_activation(
        self,
        action_name: str,
        action_info: Dict[str, Any],
        chat_content: str = "",
        message_content: str = "",
    ) -> bool:
        """
        检查是否匹配关键词触发条件
        
        Args:
            action_name: 动作名称
            action_info: 动作信息
            chat_content: 聊天内容（已经是格式化后的可读消息）
            
        Returns:
            bool: 是否应该激活此action
        """
        
        activation_keywords = action_info.get("activation_keywords", [])
        case_sensitive = action_info.get("keyword_case_sensitive", False)
        
        if not activation_keywords:
            logger.warning(f"{self.log_prefix}动作 {action_name} 设置为关键词触发但未配置关键词")
            return False
        
        # 使用构建好的聊天内容作为检索文本
        search_text = chat_content +message_content
        
        # 如果不区分大小写，转换为小写
        if not case_sensitive:
            search_text = search_text.lower()
        
        # 检查每个关键词
        matched_keywords = []
        for keyword in activation_keywords:
            check_keyword = keyword if case_sensitive else keyword.lower()
            if check_keyword in search_text:
                matched_keywords.append(keyword)
        
        
        # print(f"search_text: {search_text}")
        # print(f"activation_keywords: {activation_keywords}")
        
        if matched_keywords:
            logger.debug(f"{self.log_prefix}动作 {action_name} 匹配到关键词: {matched_keywords}")
            return True
        else:
            logger.debug(f"{self.log_prefix}动作 {action_name} 未匹配到任何关键词: {activation_keywords}")
            return False

    def get_available_actions_count(self) -> int:
        """获取当前可用动作数量（排除默认的no_action）"""
        current_actions = self.action_manager.get_using_actions_for_mode(ChatMode.NORMAL)
        # 排除no_action（如果存在）
        filtered_actions = {k: v for k, v in current_actions.items() if k != "no_action"}
        return len(filtered_actions)

    def should_skip_planning(self) -> bool:
        """判断是否应该跳过规划过程"""
        available_count = self.get_available_actions_count()
        if available_count == 0:
            logger.debug(f"{self.log_prefix} 没有可用动作，跳过规划")
            return True
        return False
