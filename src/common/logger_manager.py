from src.common.logger import get_module_logger, LogConfig
from src.common.logger import (
    BACKGROUND_TASKS_STYLE_CONFIG,
    MAIN_STYLE_CONFIG,
    MEMORY_STYLE_CONFIG,
    PFC_STYLE_CONFIG,
    MOOD_STYLE_CONFIG,
    TOOL_USE_STYLE_CONFIG,
    RELATION_STYLE_CONFIG,
    CONFIG_STYLE_CONFIG,
    HEARTFLOW_STYLE_CONFIG,
    SCHEDULE_STYLE_CONFIG,
    LLM_STYLE_CONFIG,
    CHAT_STYLE_CONFIG,
    EMOJI_STYLE_CONFIG,
    SUB_HEARTFLOW_STYLE_CONFIG,
    SUB_HEARTFLOW_MIND_STYLE_CONFIG,
    SUBHEARTFLOW_MANAGER_STYLE_CONFIG,
    BASE_TOOL_STYLE_CONFIG,
    CHAT_STREAM_STYLE_CONFIG,
    PERSON_INFO_STYLE_CONFIG,
    WILLING_STYLE_CONFIG,
    PFC_ACTION_PLANNER_STYLE_CONFIG,
    MAI_STATE_CONFIG,
    LPMM_STYLE_CONFIG,
    HFC_STYLE_CONFIG,
    OBSERVATION_STYLE_CONFIG,
    PLANNER_STYLE_CONFIG,
    PROCESSOR_STYLE_CONFIG,
    ACTION_TAKEN_STYLE_CONFIG,
    TIANYI_STYLE_CONFIG,
    REMOTE_STYLE_CONFIG,
    TOPIC_STYLE_CONFIG,
    SENDER_STYLE_CONFIG,
    CONFIRM_STYLE_CONFIG,
    MODEL_UTILS_STYLE_CONFIG,
    PROMPT_STYLE_CONFIG,
    CHANGE_MOOD_TOOL_STYLE_CONFIG,
    CHANGE_RELATIONSHIP_TOOL_STYLE_CONFIG,
    GET_KNOWLEDGE_TOOL_STYLE_CONFIG,
    GET_TIME_DATE_TOOL_STYLE_CONFIG,
    LPMM_GET_KNOWLEDGE_TOOL_STYLE_CONFIG,
    MESSAGE_BUFFER_STYLE_CONFIG,
    CHAT_MESSAGE_STYLE_CONFIG,
    CHAT_IMAGE_STYLE_CONFIG,
    INIT_STYLE_CONFIG,
    INTEREST_CHAT_STYLE_CONFIG,
    API_SERVER_STYLE_CONFIG,
)

# 可根据实际需要补充更多模块配置
MODULE_LOGGER_CONFIGS = {
    "background_tasks": BACKGROUND_TASKS_STYLE_CONFIG,  # 后台任务
    "main": MAIN_STYLE_CONFIG,  # 主程序
    "memory": MEMORY_STYLE_CONFIG,  # 海马体
    "pfc": PFC_STYLE_CONFIG,  # PFC
    "mood": MOOD_STYLE_CONFIG,  # 心情
    "tool_use": TOOL_USE_STYLE_CONFIG,  # 工具使用
    "relation": RELATION_STYLE_CONFIG,  # 关系
    "config": CONFIG_STYLE_CONFIG,  # 配置
    "heartflow": HEARTFLOW_STYLE_CONFIG,  # 麦麦大脑袋
    "schedule": SCHEDULE_STYLE_CONFIG,  # 在干嘛
    "llm": LLM_STYLE_CONFIG,  # 麦麦组织语言
    "chat": CHAT_STYLE_CONFIG,  # 见闻
    "emoji": EMOJI_STYLE_CONFIG,  # 表情包
    "sub_heartflow": SUB_HEARTFLOW_STYLE_CONFIG,  # 麦麦水群
    "sub_heartflow_mind": SUB_HEARTFLOW_MIND_STYLE_CONFIG,  # 麦麦小脑袋
    "subheartflow_manager": SUBHEARTFLOW_MANAGER_STYLE_CONFIG,  # 麦麦水群[管理]
    "base_tool": BASE_TOOL_STYLE_CONFIG,  # 工具使用
    "chat_stream": CHAT_STREAM_STYLE_CONFIG,  # 聊天流
    "person_info": PERSON_INFO_STYLE_CONFIG,  # 人物信息
    "willing": WILLING_STYLE_CONFIG,  # 意愿
    "pfc_action_planner": PFC_ACTION_PLANNER_STYLE_CONFIG,  # PFC私聊规划
    "mai_state": MAI_STATE_CONFIG,  # 麦麦状态
    "lpmm": LPMM_STYLE_CONFIG,  # LPMM
    "hfc": HFC_STYLE_CONFIG,  # HFC
    "observation": OBSERVATION_STYLE_CONFIG,  # 聊天观察
    "planner": PLANNER_STYLE_CONFIG,  # 规划器
    "processor": PROCESSOR_STYLE_CONFIG,  # 处理器
    "action_taken": ACTION_TAKEN_STYLE_CONFIG,  # 动作
    "tianyi": TIANYI_STYLE_CONFIG,  # 天依
    "remote": REMOTE_STYLE_CONFIG,  # 远程
    "topic": TOPIC_STYLE_CONFIG,  # 话题
    "sender": SENDER_STYLE_CONFIG,  # 消息发送
    "confirm": CONFIRM_STYLE_CONFIG,  # EULA与PRIVACY确认
    "model_utils": MODEL_UTILS_STYLE_CONFIG,  # 模型工具
    "prompt": PROMPT_STYLE_CONFIG,  # 提示词
    "change_mood_tool": CHANGE_MOOD_TOOL_STYLE_CONFIG,  # 改变心情工具
    "change_relationship": CHANGE_RELATIONSHIP_TOOL_STYLE_CONFIG,  # 改变关系工具
    "get_knowledge_tool": GET_KNOWLEDGE_TOOL_STYLE_CONFIG,  # 获取知识工具
    "get_time_date": GET_TIME_DATE_TOOL_STYLE_CONFIG,  # 获取时间日期工具
    "lpm_get_knowledge_tool": LPMM_GET_KNOWLEDGE_TOOL_STYLE_CONFIG,  # LPMM获取知识工具
    "message_buffer": MESSAGE_BUFFER_STYLE_CONFIG,  # 消息缓冲
    "chat_message": CHAT_MESSAGE_STYLE_CONFIG,  # 聊天消息
    "chat_image": CHAT_IMAGE_STYLE_CONFIG,  # 聊天图片
    "init": INIT_STYLE_CONFIG,  # 初始化
    "interest_chat": INTEREST_CHAT_STYLE_CONFIG,  # 兴趣
    "api": API_SERVER_STYLE_CONFIG,  # API服务器
    # ...如有更多模块，继续添加...
}


def get_logger(module_name: str):
    style_config = MODULE_LOGGER_CONFIGS.get(module_name)
    if style_config:
        log_config = LogConfig(
            console_format=style_config["console_format"],
            file_format=style_config["file_format"],
        )
        return get_module_logger(module_name, config=log_config)
    # 若无特殊样式，使用默认
    return get_module_logger(module_name)
