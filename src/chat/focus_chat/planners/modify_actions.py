from typing import List, Optional, Any
from src.chat.heart_flow.observation.observation import Observation
from src.common.logger_manager import get_logger
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.message_receive.chat_stream import chat_manager
from typing import Dict
from src.config.config import global_config
import random
from src.chat.focus_chat.planners.action_manager import ActionManager

logger = get_logger("action_manager")


class ActionModifier:
    """动作处理器

    用于处理Observation对象，将其转换为ObsInfo对象。
    """

    log_prefix = "动作处理"

    def __init__(self, action_manager: ActionManager):
        """初始化观察处理器"""
        self.action_manager = action_manager
        self.all_actions = self.action_manager.get_registered_actions()

    async def modify_actions(
        self,
        observations: Optional[List[Observation]] = None,
        running_memorys: Optional[List[Dict]] = None,
        **kwargs: Any,
    ):
        # print(f"observations: {observations}")
        # processed_infos = []

        # 处理Observation对象
        if observations:
            # action_info = ActionInfo()
            # all_actions = None
            hfc_obs = None
            chat_obs = None

            # 收集所有观察对象
            for obs in observations:
                if isinstance(obs, HFCloopObservation):
                    hfc_obs = obs
                if isinstance(obs, ChattingObservation):
                    chat_obs = obs

            # 合并所有动作变更
            merged_action_changes = {"add": [], "remove": []}
            reasons = []

            # 处理HFCloopObservation
            if hfc_obs:
                obs = hfc_obs
                all_actions = self.all_actions
                action_changes = await self.analyze_loop_actions(obs)
                if action_changes["add"] or action_changes["remove"]:
                    # 合并动作变更
                    merged_action_changes["add"].extend(action_changes["add"])
                    merged_action_changes["remove"].extend(action_changes["remove"])

                    # 收集变更原因
                    # if action_changes["add"]:
                    #     reasons.append(f"添加动作{action_changes['add']}因为检测到大量无回复")
                    # if action_changes["remove"]:
                    #     reasons.append(f"移除动作{action_changes['remove']}因为检测到连续回复")

            # 处理ChattingObservation
            if chat_obs:
                obs = chat_obs
                # 检查动作的关联类型
                chat_context = chat_manager.get_stream(obs.chat_id).context
                type_mismatched_actions = []

                for action_name in all_actions.keys():
                    data = all_actions[action_name]
                    if data.get("associated_types"):
                        if not chat_context.check_types(data["associated_types"]):
                            type_mismatched_actions.append(action_name)
                            logger.debug(f"{self.log_prefix} 动作 {action_name} 关联类型不匹配，移除该动作")

                if type_mismatched_actions:
                    # 合并到移除列表中
                    merged_action_changes["remove"].extend(type_mismatched_actions)
                    reasons.append(f"移除动作{type_mismatched_actions}因为关联类型不匹配")

            for action_name in merged_action_changes["add"]:
                if action_name in self.action_manager.get_registered_actions():
                    self.action_manager.add_action_to_using(action_name)
                    logger.debug(f"{self.log_prefix} 添加动作: {action_name}, 原因: {reasons}")

            for action_name in merged_action_changes["remove"]:
                self.action_manager.remove_action_from_using(action_name)
                logger.debug(f"{self.log_prefix} 移除动作: {action_name}, 原因: {reasons}")

            # 如果有任何动作变更，设置到action_info中
            # if merged_action_changes["add"] or merged_action_changes["remove"]:
            #     action_info.set_action_changes(merged_action_changes)
            #     action_info.set_reason(" | ".join(reasons))

            # processed_infos.append(action_info)

        # return processed_infos

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

        # 统计no_reply的数量
        no_reply_count = 0
        reply_sequence = []  # 记录最近的动作序列

        for cycle in recent_cycles:
            action_type = cycle.loop_plan_info["action_result"]["action_type"]
            if action_type == "no_reply":
                no_reply_count += 1
            reply_sequence.append(action_type == "reply")

        # 检查no_reply比例
        print(f"no_reply_count: {no_reply_count}, len(recent_cycles): {len(recent_cycles)}")
        # print(1111111111111111111111111111111111111111111111111111111111111111111111111111111111111111)
        if len(recent_cycles) >= (5 * global_config.chat.exit_focus_threshold) and (
            no_reply_count / len(recent_cycles)
        ) >= (0.8 * global_config.chat.exit_focus_threshold):
            if global_config.chat.chat_mode == "auto":
                result["add"].append("exit_focus_chat")
                result["remove"].append("no_reply")
                result["remove"].append("reply")

        # 获取最近三次的reply状态
        last_three = reply_sequence[-3:] if len(reply_sequence) >= 3 else reply_sequence

        # 根据最近的reply情况决定是否移除reply动作
        if len(last_three) >= 3 and all(last_three):
            # 如果最近三次都是reply，直接移除
            result["remove"].append("reply")
        elif len(last_three) >= 2 and all(last_three[-2:]):
            # 如果最近两次都是reply，40%概率移除
            if random.random() < 0.4:
                result["remove"].append("reply")
        elif last_three and last_three[-1]:
            # 如果最近一次是reply，20%概率移除
            if random.random() < 0.2:
                result["remove"].append("reply")

        return result
