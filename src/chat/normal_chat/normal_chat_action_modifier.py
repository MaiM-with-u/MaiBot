from typing import List, Any
from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.action_manager import ActionManager

logger = get_logger("normal_chat_action_modifier")


class NormalChatActionModifier:
    """Normal Chat动作修改器

    负责根据Normal Chat的上下文和状态动态调整可用的动作集合
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
        **kwargs: Any,
    ):
        """为Normal Chat修改可用动作集合

        Args:
            chat_stream: 聊天流对象
            recent_replies: 最近的回复记录
            **kwargs: 其他参数
        """

        # 合并所有动作变更
        merged_action_changes = {"add": [], "remove": []}
        reasons = []

        # 1. 移除Normal Chat不适用的动作
        excluded_actions = ["exit_focus_chat_action", "no_reply", "reply"]
        for action_name in excluded_actions:
            if action_name in self.action_manager.get_using_actions():
                merged_action_changes["remove"].append(action_name)
                reasons.append(f"移除{action_name}(Normal Chat不适用)")

        # 2. 检查动作的关联类型
        if chat_stream:
            chat_context = chat_stream.context if hasattr(chat_stream, "context") else None
            if chat_context:
                type_mismatched_actions = []

                current_using_actions = self.action_manager.get_using_actions()
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

        # 应用动作变更
        for action_name in merged_action_changes["add"]:
            if action_name in self.all_actions and action_name not in excluded_actions:
                success = self.action_manager.add_action_to_using(action_name)
                if success:
                    logger.debug(f"{self.log_prefix} 添加动作: {action_name}")

        for action_name in merged_action_changes["remove"]:
            success = self.action_manager.remove_action_from_using(action_name)
            if success:
                logger.debug(f"{self.log_prefix} 移除动作: {action_name}")

        # 记录变更原因
        if merged_action_changes["add"] or merged_action_changes["remove"]:
            logger.info(f"{self.log_prefix} 动作调整完成: {' | '.join(reasons)}")
            logger.debug(f"{self.log_prefix} 当前可用动作: {list(self.action_manager.get_using_actions().keys())}")

    def get_available_actions_count(self) -> int:
        """获取当前可用动作数量（排除默认的no_action）"""
        current_actions = self.action_manager.get_using_actions()
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
