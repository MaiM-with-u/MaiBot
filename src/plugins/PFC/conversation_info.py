from typing import Optional, List, Dict, Any


class ConversationInfo:
    def __init__(self):
        self.done_action: List[Dict[str, Any]] = [] # Added type hint
        self.goal_list: List[Dict[str, Any]] = [] # Added type hint (assuming goal list contains dicts)
        self.knowledge_list: List[Dict[str, Any]] = [] # Added type hint
        self.memory_list = [] # Keep as is if type is unknown or mixed
        self.last_successful_reply_action: Optional[str] = None
        self.current_heartflow: Optional[str] = None # <--- 新增：存储当前心流