from typing import Optional
from dataclasses import dataclass, field


@dataclass
class MessageAndActionModel:
    time: float = field(default_factory=float)
    user_id: str = field(default_factory=str)
    user_platform: str = field(default_factory=str)
    user_nickname: str = field(default_factory=str)
    user_cardname: Optional[str] = None
    processed_plain_text: Optional[str] = None
    display_message: Optional[str] = None
    chat_info_platform: str = field(default_factory=str)
    is_action_record: bool = field(default=False)
    action_name: Optional[str] = None
