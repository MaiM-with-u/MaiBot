# 定义了来自外部世界的信息
# 外部世界可以是某个聊天 不同平台的聊天 也可以是任意媒体
from datetime import datetime
from src.common.logger_manager import get_logger
from src.chat.focus_chat.heartFC_Cycleinfo import CycleDetail
from typing import List
# Import the new utility function

logger = get_logger("observation")


# 所有观察的基类
class HFCloopObservation:
    def __init__(self, observe_id):
        self.observe_info = ""
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间
        self.history_loop: List[CycleDetail] = []

    def get_observe_info(self):
        return self.observe_info

    def add_loop_info(self, loop_info: CycleDetail):
        self.history_loop.append(loop_info)

    async def observe(self):
        recent_active_cycles: List[CycleDetail] = []
        for cycle in reversed(self.history_loop):
            # 只关心实际执行了动作的循环
            # action_taken = cycle.loop_action_info["action_taken"]
            # if action_taken:
            recent_active_cycles.append(cycle)
            if len(recent_active_cycles) == 5:
                break

        cycle_info_block = ""
        action_detailed_str = ""
        consecutive_text_replies = 0
        responses_for_prompt = []

        cycle_last_reason = ""

        # 检查这最近的活动循环中有多少是连续的文本回复 (从最近的开始看)
        for cycle in recent_active_cycles:
            action_type = cycle.loop_plan_info["action_result"]["action_type"]
            action_reasoning = cycle.loop_plan_info["action_result"]["reasoning"]
            is_taken = cycle.loop_action_info["action_taken"]
            action_taken_time = cycle.loop_action_info["taken_time"]
            action_taken_time_str = datetime.fromtimestamp(action_taken_time).strftime("%H:%M:%S")
            # print(action_type)
            # print(action_reasoning)
            # print(is_taken)
            # print(action_taken_time_str)
            # print("--------------------------------")
            if action_reasoning != cycle_last_reason:
                cycle_last_reason = action_reasoning
                action_reasoning_str = f"你选择这个action的原因是:{action_reasoning}"
            else:
                action_reasoning_str = ""

            if action_type == "reply":
                consecutive_text_replies += 1
                response_text = cycle.loop_action_info["reply_text"]
                responses_for_prompt.append(response_text)

                if is_taken:
                    action_detailed_str += f"{action_taken_time_str}时，你选择回复(action:{action_type},内容是:'{response_text}')。{action_reasoning_str}\n"
                else:
                    action_detailed_str += f"{action_taken_time_str}时，你选择回复(action:{action_type},内容是:'{response_text}')，但是动作失败了。{action_reasoning_str}\n"
            elif action_type == "no_reply":
                # action_detailed_str += (
                #     f"{action_taken_time_str}时，你选择不回复(action:{action_type})，{action_reasoning_str}\n"
                # )
                pass
            else:
                if is_taken:
                    action_detailed_str += (
                        f"{action_taken_time_str}时，你选择执行了(action:{action_type})，{action_reasoning_str}\n"
                    )
                else:
                    action_detailed_str += f"{action_taken_time_str}时，你选择执行了(action:{action_type})，但是动作失败了。{action_reasoning_str}\n"

        if action_detailed_str:
            cycle_info_block = f"\n你最近做的事：\n{action_detailed_str}\n"
        else:
            cycle_info_block = "\n"

        # 根据连续文本回复的数量构建提示信息
        if consecutive_text_replies >= 3:  # 如果最近的三个活动都是文本回复
            cycle_info_block = f'你已经连续回复了三条消息（最近: "{responses_for_prompt[0]}"，第二近: "{responses_for_prompt[1]}"，第三近: "{responses_for_prompt[2]}"）。你回复的有点多了，请注意'
        elif consecutive_text_replies == 2:  # 如果最近的两个活动是文本回复
            cycle_info_block = f'你已经连续回复了两条消息（最近: "{responses_for_prompt[0]}"，第二近: "{responses_for_prompt[1]}"），请注意'

        # 包装提示块，增加可读性，即使没有连续回复也给个标记
        # if cycle_info_block:
        #     cycle_info_block = f"\n你最近的回复\n{cycle_info_block}\n"
        # else:
        #     cycle_info_block = "\n"

        # 获取history_loop中最新添加的
        if self.history_loop:
            last_loop = self.history_loop[0]
            start_time = last_loop.start_time
            end_time = last_loop.end_time
            if start_time is not None and end_time is not None:
                time_diff = int(end_time - start_time)
                if time_diff > 60:
                    cycle_info_block += f"距离你上一次阅读消息并思考和规划，已经过去了{int(time_diff / 60)}分钟\n"
                else:
                    cycle_info_block += f"距离你上一次阅读消息并思考和规划，已经过去了{time_diff}秒\n"
            else:
                cycle_info_block += "你还没看过消息\n"

        self.observe_info = cycle_info_block

    def to_dict(self) -> dict:
        """将观察对象转换为可序列化的字典"""
        # 只序列化基本信息，避免循环引用
        return {
            "observe_info": self.observe_info,
            "observe_id": self.observe_id,
            "last_observe_time": self.last_observe_time,
            # 不序列化history_loop，避免循环引用
            "history_loop_count": len(self.history_loop),
        }
