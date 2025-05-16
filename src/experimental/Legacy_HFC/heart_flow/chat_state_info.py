from src.manager.mood_manager import mood_manager
import enum


class ChatState(enum.Enum):
    ABSENT = "没在看群"
    CHAT = "随便水群"
    FOCUSED = "认真水群"


class ChatStateInfo:
    def __init__(self):
        self.chat_status: ChatState = ChatState.ABSENT
        self.current_state_time = 120

        self.mood_manager = mood_manager
        self.mood = self.mood_manager.get_mood_prompt()
