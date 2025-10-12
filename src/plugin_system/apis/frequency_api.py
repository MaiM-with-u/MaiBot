from src.common.logger import get_logger
from src.chat.frequency_control.frequency_control import frequency_control_manager

logger = get_logger("frequency_api")


def get_current_talk_frequency(chat_id: str) -> float:
    return frequency_control_manager.get_or_create_frequency_control(chat_id).get_talk_frequency_adjust()


def set_talk_frequency_adjust(chat_id: str, talk_frequency_adjust: float) -> None:
    frequency_control_manager.get_or_create_frequency_control(chat_id).set_talk_frequency_adjust(talk_frequency_adjust)


def get_talk_frequency_adjust(chat_id: str) -> float:
    return frequency_control_manager.get_or_create_frequency_control(chat_id).get_talk_frequency_adjust()
