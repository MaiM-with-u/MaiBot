import time
from typing import List, Optional, Dict, Any


class CycleInfo:
    """循环信息记录类"""

    def __init__(self, cycle_id: int):
        self.cycle_id = cycle_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.action_taken = False
        self.action_type = "unknown"
        self.reasoning = ""
        self.timers: Dict[str, float] = {}
        self.thinking_id = ""
        self.replanned = False

        # 添加响应信息相关字段
        self.response_info: Dict[str, Any] = {
            "response_text": [],  # 回复的文本列表
            "emoji_info": "",  # 表情信息
            "anchor_message_id": "",  # 锚点消息ID
            "reply_message_ids": [],  # 回复消息ID列表
            "sub_mind_thinking": "",  # 子思维思考内容
        }

    def to_dict(self) -> Dict[str, Any]:
        """将循环信息转换为字典格式"""
        return {
            "cycle_id": self.cycle_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "action_taken": self.action_taken,
            "action_type": self.action_type,
            "reasoning": self.reasoning,
            "timers": self.timers,
            "thinking_id": self.thinking_id,
            "response_info": self.response_info,
        }

    def complete_cycle(self):
        """完成循环，记录结束时间"""
        self.end_time = time.time()

    def set_action_info(self, action_type: str, reasoning: str, action_taken: bool):
        """设置动作信息"""
        self.action_type = action_type
        self.reasoning = reasoning
        self.action_taken = action_taken

    def set_thinking_id(self, thinking_id: str):
        """设置思考消息ID"""
        self.thinking_id = thinking_id

    def set_response_info(
        self,
        response_text: Optional[List[str]] = None,
        emoji_info: Optional[str] = None,
        anchor_message_id: Optional[str] = None,
        reply_message_ids: Optional[List[str]] = None,
        sub_mind_thinking: Optional[str] = None,
    ):
        """设置响应信息"""
        if response_text is not None:
            self.response_info["response_text"] = response_text
        if emoji_info is not None:
            self.response_info["emoji_info"] = emoji_info
        if anchor_message_id is not None:
            self.response_info["anchor_message_id"] = anchor_message_id
        if reply_message_ids is not None:
            self.response_info["reply_message_ids"] = reply_message_ids
        if sub_mind_thinking is not None:
            self.response_info["sub_mind_thinking"] = sub_mind_thinking
