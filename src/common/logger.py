from loguru import logger
from typing import Dict, Optional, Union, List, Tuple
import sys
from types import ModuleType
from pathlib import Path
from src.env import env
# from ..plugins.chat.config import global_config


# 保存原生处理器ID
default_handler_id = None
for handler_id in logger._core.handlers:
    default_handler_id = handler_id
    break

# 移除默认处理器
if default_handler_id is not None:
    logger.remove(default_handler_id)

# 类型别名
LoguruLogger = logger.__class__

# 全局注册表：记录模块与处理器ID的映射
_handler_registry: Dict[str, List[int]] = {}
_custom_style_handlers: Dict[Tuple[str, str], List[int]] = {}  # 记录自定义样式处理器ID

# 获取日志存储根地址
current_file_path = Path(__file__).resolve()
LOG_ROOT = "logs"

SIMPLE_OUTPUT =  env.getenv("SIMPLE_OUTPUT", "false").strip().lower()
if SIMPLE_OUTPUT == "true":
    SIMPLE_OUTPUT = True
else:
    SIMPLE_OUTPUT = False
print(f"SIMPLE_OUTPUT: {SIMPLE_OUTPUT}")

if not SIMPLE_OUTPUT:
    # 默认全局配置
    DEFAULT_CONFIG = {
        # 日志级别配置
        "console_level": "INFO",
        "file_level": "DEBUG",
        # 格式配置
        "console_format": (
            "<level>{time:YYYY-MM-DD HH:mm:ss}</level> | <cyan>{extra[module]: <12}</cyan> | <level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | {message}",
        "log_dir": LOG_ROOT,
        "rotation": "00:00",
        "retention": "3 days",
        "compression": "zip",
    }
else:
    DEFAULT_CONFIG = {
        # 日志级别配置
        "console_level": "INFO",
        "file_level": "DEBUG",
        # 格式配置
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <cyan>{extra[module]}</cyan> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | {message}",
        "log_dir": LOG_ROOT,
        "rotation": "00:00",
        "retention": "3 days",
        "compression": "zip",
    }


MAIN_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>主程序</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 主程序 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-yellow>主程序</light-yellow> | <light-yellow>{message}</light-yellow>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 主程序 | {message}",
    },
}

# 海马体日志样式配置
MEMORY_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>海马体</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 海马体 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-yellow>海马体</light-yellow> | <light-yellow>{message}</light-yellow>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 海马体 | {message}",
    },
}

# pfc配置
PFC_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>PFC</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | PFC | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-green>PFC</light-green> | <light-green>{message}</light-green>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | PFC | {message}",
    },
}

# MOOD
MOOD_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-green>心情</light-green> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 心情 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <magenta>心情</magenta> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 心情 | {message}",
    },
}
# tool use
TOOL_USE_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<magenta>工具使用</magenta> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 工具使用 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <magenta>工具使用</magenta> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 工具使用 | {message}",
    },
}


# relationship
RELATION_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-magenta>关系</light-magenta> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 关系 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-magenta>关系</light-magenta> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 关系 | {message}",
    },
}

# config
CONFIG_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-cyan>配置</light-cyan> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 配置 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-cyan>配置</light-cyan> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 配置 | {message}",
    },
}

SENDER_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>消息发送</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 消息发送 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <green>消息发送</green> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 消息发送 | {message}",
    },
}

HEARTFLOW_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>麦麦大脑袋</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦大脑袋 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-green>麦麦大脑袋</light-green> | <light-green>{message}</light-green>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦大脑袋 | {message}",
    },
}

SCHEDULE_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>在干嘛</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 在干嘛 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <cyan>在干嘛</cyan> | <cyan>{message}</cyan>",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 在干嘛 | {message}",
    },
}

LLM_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>麦麦组织语言</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦组织语言 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-green>麦麦组织语言</light-green> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦组织语言 | {message}",
    },
}


# Topic日志样式配置
TOPIC_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>话题</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 话题 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-blue>主题</light-blue> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 话题 | {message}",
    },
}

# Topic日志样式配置
CHAT_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>见闻</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 见闻 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>见闻</light-blue> | <green>{message}</green>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 见闻 | {message}",
    },
}

REMOTE_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>远程</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 远程 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-yellow>远程</light-yellow> | {message}",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 远程 | {message}",
    },
}

SUB_HEARTFLOW_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>麦麦小脑袋</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦小脑袋 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>麦麦小脑袋</light-blue> | <light-blue>{message}</light-blue>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦小脑袋 | {message}",
    },
}

BASE_TOOL_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>工具使用</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 工具使用 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>工具使用</light-blue> | <light-blue>{message}</light-blue>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 工具使用 | {message}",
    },
}

CHAT_STREAM_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>聊天流</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 聊天流 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>聊天流</light-blue> | <light-blue>{message}</light-blue>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 聊天流 | {message}",
    },
}

PERSON_INFO_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>人物信息</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 人物信息 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>人物信息</light-blue> | <light-blue>{message}</light-blue>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 人物信息 | {message}",
    },
}

BACKGROUND_TASKS_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>后台任务</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 后台任务 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>后台任务</light-blue> | <light-blue>{message}</light-blue>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 后台任务 | {message}",
    },
}

SUBHEARTFLOW_MANAGER_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>小脑袋管理</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 小脑袋管理 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-blue>小脑袋管理</light-blue> | <light-blue>{message}</light-blue>"
        ),  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 小脑袋管理 | {message}",
    },
}

WILLING_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>意愿</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 意愿 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-blue>意愿 | {message} </light-blue>",  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 意愿 | {message}",
    },
}

PFC_ACTION_PLANNER_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>PFC私聊规划</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | PFC私聊规划 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-blue>PFC私聊规划 | {message} </light-blue>",  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | PFC私聊规划 | {message}",
    },
}

EMOJI_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>表情</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 表情 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-blue>表情 | {message} </light-blue>",  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 表情 | {message}",
    },
}

MAI_STATE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-blue>麦麦状态</light-blue> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦状态 | {message}",
    },
    "simple": {
        "console_format": "<level>{time:MM-DD HH:mm}</level> | <light-blue>麦麦状态 | {message} </light-blue>",  # noqa: E501
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 麦麦状态 | {message}",
    },
}

# LPMM配置
LPMM_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>LPMM</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | LPMM | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-green>LPMM</light-green> | <light-green>{message}</light-green>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | LPMM | {message}",
    },
}

# 兴趣log
INTEREST_STYLE_CONFIG = {
    "advanced": {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>兴趣</light-yellow> | "
            "<level>{message}</level>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 兴趣 | {message}",
    },
    "simple": {
        "console_format": (
            "<level>{time:MM-DD HH:mm}</level> | <light-green>兴趣</light-green> | <light-green>{message}</light-green>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | 兴趣 | {message}",
    },
}

CONFIRM_STYLE_CONFIG = {
    "console_format": "<RED>{message}</RED>",  # noqa: E501
    "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | EULA与PRIVACY确认 | {message}",
}

# 根据SIMPLE_OUTPUT选择配置
MAIN_STYLE_CONFIG = MAIN_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else MAIN_STYLE_CONFIG["advanced"]
EMOJI_STYLE_CONFIG = EMOJI_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else EMOJI_STYLE_CONFIG["advanced"]
PFC_ACTION_PLANNER_STYLE_CONFIG = (
    PFC_ACTION_PLANNER_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else PFC_ACTION_PLANNER_STYLE_CONFIG["advanced"]
)
REMOTE_STYLE_CONFIG = REMOTE_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else REMOTE_STYLE_CONFIG["advanced"]
BASE_TOOL_STYLE_CONFIG = BASE_TOOL_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else BASE_TOOL_STYLE_CONFIG["advanced"]
PERSON_INFO_STYLE_CONFIG = PERSON_INFO_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else PERSON_INFO_STYLE_CONFIG["advanced"]
SUBHEARTFLOW_MANAGER_STYLE_CONFIG = (
    SUBHEARTFLOW_MANAGER_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else SUBHEARTFLOW_MANAGER_STYLE_CONFIG["advanced"]
)
BACKGROUND_TASKS_STYLE_CONFIG = (
    BACKGROUND_TASKS_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else BACKGROUND_TASKS_STYLE_CONFIG["advanced"]
)
MEMORY_STYLE_CONFIG = MEMORY_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else MEMORY_STYLE_CONFIG["advanced"]
CHAT_STREAM_STYLE_CONFIG = CHAT_STREAM_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else CHAT_STREAM_STYLE_CONFIG["advanced"]
TOPIC_STYLE_CONFIG = TOPIC_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else TOPIC_STYLE_CONFIG["advanced"]
SENDER_STYLE_CONFIG = SENDER_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else SENDER_STYLE_CONFIG["advanced"]
LLM_STYLE_CONFIG = LLM_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else LLM_STYLE_CONFIG["advanced"]
CHAT_STYLE_CONFIG = CHAT_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else CHAT_STYLE_CONFIG["advanced"]
MOOD_STYLE_CONFIG = MOOD_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else MOOD_STYLE_CONFIG["advanced"]
RELATION_STYLE_CONFIG = RELATION_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else RELATION_STYLE_CONFIG["advanced"]
SCHEDULE_STYLE_CONFIG = SCHEDULE_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else SCHEDULE_STYLE_CONFIG["advanced"]
HEARTFLOW_STYLE_CONFIG = HEARTFLOW_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else HEARTFLOW_STYLE_CONFIG["advanced"]
SUB_HEARTFLOW_STYLE_CONFIG = (
    SUB_HEARTFLOW_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else SUB_HEARTFLOW_STYLE_CONFIG["advanced"]
)  # noqa: E501
WILLING_STYLE_CONFIG = WILLING_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else WILLING_STYLE_CONFIG["advanced"]
MAI_STATE_CONFIG = MAI_STATE_CONFIG["simple"] if SIMPLE_OUTPUT else MAI_STATE_CONFIG["advanced"]
CONFIG_STYLE_CONFIG = CONFIG_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else CONFIG_STYLE_CONFIG["advanced"]
TOOL_USE_STYLE_CONFIG = TOOL_USE_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else TOOL_USE_STYLE_CONFIG["advanced"]
PFC_STYLE_CONFIG = PFC_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else PFC_STYLE_CONFIG["advanced"]
LPMM_STYLE_CONFIG = LPMM_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else LPMM_STYLE_CONFIG["advanced"]
INTEREST_STYLE_CONFIG = INTEREST_STYLE_CONFIG["simple"] if SIMPLE_OUTPUT else INTEREST_STYLE_CONFIG["advanced"]


def is_registered_module(record: dict) -> bool:
    """检查是否为已注册的模块"""
    return record["extra"].get("module") in _handler_registry


def is_unregistered_module(record: dict) -> bool:
    """检查是否为未注册的模块"""
    return not is_registered_module(record)


def log_patcher(record: dict) -> None:
    """自动填充未设置模块名的日志记录，保留原生模块名称"""
    if "module" not in record["extra"]:
        # 尝试从name中提取模块名
        module_name = record.get("name", "")
        if module_name == "":
            module_name = "root"
        record["extra"]["module"] = module_name


# 应用全局修补器
logger.configure(patcher=log_patcher)


class LogConfig:
    """日志配置类"""

    def __init__(self, **kwargs):
        self.config = DEFAULT_CONFIG.copy()
        self.config.update(kwargs)

    def to_dict(self) -> dict:
        return self.config.copy()

    def update(self, **kwargs):
        self.config.update(kwargs)


def get_module_logger(
    module: Union[str, ModuleType],
    *,
    console_level: Optional[str] = None,
    file_level: Optional[str] = None,
    extra_handlers: Optional[List[dict]] = None,
    config: Optional[LogConfig] = None,
) -> LoguruLogger:
    module_name = module if isinstance(module, str) else module.__name__
    current_config = config.config if config else DEFAULT_CONFIG

    # 清理旧处理器
    if module_name in _handler_registry:
        for handler_id in _handler_registry[module_name]:
            logger.remove(handler_id)
        del _handler_registry[module_name]

    handler_ids = []

    # 控制台处理器
    console_id = logger.add(
        sink=sys.stderr,
        level= env.getenv("CONSOLE_LOG_LEVEL", console_level or current_config["console_level"]),
        format=current_config["console_format"],
        filter=lambda record: record["extra"].get("module") == module_name and "custom_style" not in record["extra"],
        enqueue=True,
    )
    handler_ids.append(console_id)

    # 文件处理器
    log_dir = Path(current_config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / module_name / "{time:YYYY-MM-DD}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_id = logger.add(
        sink=str(log_file),
        level= env.getenv("FILE_LOG_LEVEL", file_level or current_config["file_level"]),
        format=current_config["file_format"],
        rotation=current_config["rotation"],
        retention=current_config["retention"],
        compression=current_config["compression"],
        encoding="utf-8",
        filter=lambda record: record["extra"].get("module") == module_name and "custom_style" not in record["extra"],
        enqueue=True,
    )
    handler_ids.append(file_id)

    # 额外处理器
    if extra_handlers:
        for handler in extra_handlers:
            handler_id = logger.add(**handler)
            handler_ids.append(handler_id)

    # 更新注册表
    _handler_registry[module_name] = handler_ids

    return logger.bind(module=module_name)


def add_custom_style_handler(
    module_name: str,
    style_name: str,
    console_format: str,
    console_level: str = "INFO",
    # file_format: Optional[str] = None, # 暂时只支持控制台
    # file_level: str = "DEBUG",
    # config: Optional[LogConfig] = None, # 暂时不使用全局配置
) -> None:
    """为指定模块和样式名添加自定义日志处理器（目前仅支持控制台）."""
    handler_key = (module_name, style_name)

    # 如果已存在该模块和样式的处理器，则不重复添加
    if handler_key in _custom_style_handlers:
        # print(f"Custom handler for {handler_key} already exists.")
        return

    handler_ids = []

    # 添加自定义控制台处理器
    try:
        custom_console_id = logger.add(
            sink=sys.stderr,
            level= env.getenv(f"{module_name.upper()}_{style_name.upper()}_CONSOLE_LEVEL", console_level),
            format=console_format,
            filter=lambda record: record["extra"].get("module") == module_name
            and record["extra"].get("custom_style") == style_name,
            enqueue=True,
        )
        handler_ids.append(custom_console_id)
        # print(f"Added custom console handler {custom_console_id} for {handler_key}")
    except Exception as e:
        logger.error(f"Failed to add custom console handler for {handler_key}: {e}")
        # 如果添加失败，确保列表为空，避免记录不存在的ID
        handler_ids = []

    # # 文件处理器 (可选，按需启用)
    # if file_format:
    #     current_config = config.config if config else DEFAULT_CONFIG
    #     log_dir = Path(current_config["log_dir"])
    #     log_dir.mkdir(parents=True, exist_ok=True)
    #     # 可以考虑将自定义样式的日志写入单独文件或模块主文件
    #     log_file = log_dir / module_name / f"{style_name}_{{time:YYYY-MM-DD}}.log"
    #     log_file.parent.mkdir(parents=True, exist_ok=True)
    #     try:
    #         custom_file_id = logger.add(
    #             sink=str(log_file),
    #             level= env.getenv(f"{module_name.upper()}_{style_name.upper()}_FILE_LEVEL", file_level),
    #             format=file_format,
    #             rotation=current_config["rotation"],
    #             retention=current_config["retention"],
    #             compression=current_config["compression"],
    #             encoding="utf-8",
    #             filter=lambda record: record["extra"].get("module") == module_name
    #             and record["extra"].get("custom_style") == style_name,
    #             enqueue=True,
    #         )
    #         handler_ids.append(custom_file_id)
    #     except Exception as e:
    #         logger.error(f"Failed to add custom file handler for {handler_key}: {e}")

    # 更新自定义处理器注册表
    if handler_ids:
        _custom_style_handlers[handler_key] = handler_ids


def remove_custom_style_handler(module_name: str, style_name: str) -> None:
    """移除指定模块和样式名的自定义日志处理器."""
    handler_key = (module_name, style_name)
    if handler_key in _custom_style_handlers:
        for handler_id in _custom_style_handlers[handler_key]:
            try:
                logger.remove(handler_id)
                # print(f"Removed custom handler {handler_id} for {handler_key}")
            except ValueError:
                # 可能已经被移除或不存在
                # print(f"Handler {handler_id} for {handler_key} already removed or invalid.")
                pass
        del _custom_style_handlers[handler_key]


def remove_module_logger(module_name: str) -> None:
    """清理指定模块的日志处理器"""
    if module_name in _handler_registry:
        for handler_id in _handler_registry[module_name]:
            logger.remove(handler_id)
        del _handler_registry[module_name]


# 添加全局默认处理器（只处理未注册模块的日志--->控制台）
# print( env.getenv("DEFAULT_CONSOLE_LOG_LEVEL", "SUCCESS"))
DEFAULT_GLOBAL_HANDLER = logger.add(
    sink=sys.stderr,
    level= env.getenv("DEFAULT_CONSOLE_LOG_LEVEL", "SUCCESS"),
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name: <12}</cyan> | "
        "<level>{message}</level>"
    ),
    filter=lambda record: is_unregistered_module(record),  # 只处理未注册模块的日志，并过滤nonebot
    enqueue=True,
)

# 添加全局默认文件处理器（只处理未注册模块的日志--->logs文件夹）
log_dir = Path(DEFAULT_CONFIG["log_dir"])
log_dir.mkdir(parents=True, exist_ok=True)
other_log_dir = log_dir / "other"
other_log_dir.mkdir(parents=True, exist_ok=True)

DEFAULT_FILE_HANDLER = logger.add(
    sink=str(other_log_dir / "{time:YYYY-MM-DD}.log"),
    level= env.getenv("DEFAULT_FILE_LOG_LEVEL", "DEBUG"),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name: <15} | {message}",
    rotation=DEFAULT_CONFIG["rotation"],
    retention=DEFAULT_CONFIG["retention"],
    compression=DEFAULT_CONFIG["compression"],
    encoding="utf-8",
    filter=lambda record: is_unregistered_module(record),  # 只处理未注册模块的日志，并过滤nonebot
    enqueue=True,
)
