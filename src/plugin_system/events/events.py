from enum import Enum


class EventType(Enum):
    """
    事件类型枚举类
    """

    ON_MESSAGE = "on_message"
    ON_PLAN = "on_plan"
    POST_LLM = "post_llm"
    AFTER_LLM = "after_llm"
    POST_SEND = "post_send"
    AFTER_SEND = "after_send"
