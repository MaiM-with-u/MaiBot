from loguru import logger
from typing import Dict, Optional, Union, List
import sys
import os
from types import ModuleType
from pathlib import Path
from dotenv import load_dotenv

# from ..plugins.chat.config import global_config

# 自定义模块日志配置
# 每个模块的配置包含三个属性：
# - tag: 模块的中文显示名称，用于日志输出
# - color: 模块日志的颜色标记，使用 loguru 支持的颜色名称
# - message_color: 消息内容的颜色标记，默认为 level
MODULE_CONFIGS = {
    "memory": {"tag": "海马体", "color": "light-yellow", "message_color": "light-yellow"},
    "mood": {"tag": "心情", "color": "light-green"},
    "relation": {"tag": "关系", "color": "light-magenta"},
    "sender": {"tag": "消息发送", "color": "light-yellow"},
    "heartflow": {"tag": "麦麦大脑袋", "color": "light-yellow", "message_color": "light-yellow"},
    "schedule": {"tag": "在干嘛", "color": "cyan", "message_color": "cyan"},
    "llm": {"tag": "麦麦组织语言", "color": "light-yellow"},
    "topic": {"tag": "话题", "color": "light-blue"},
    "chat": {"tag": "见闻", "color": "light-blue"},
    "sub_heartflow": {"tag": "麦麦小脑袋", "color": "light-blue", "message_color": "light-blue"},
}

# 加载 .env.prod 文件
env_path = Path(__file__).resolve().parent.parent.parent / ".env.prod"
load_dotenv(dotenv_path=env_path)

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

# 获取日志存储根地址
current_file_path = Path(__file__).resolve()
LOG_ROOT = "logs"

SIMPLE_OUTPUT = os.getenv("SIMPLE_OUTPUT", "false")
print(f"SIMPLE_OUTPUT: {SIMPLE_OUTPUT}")

# 默认配置
DEFAULT_CONFIG = {
    "console_level": "INFO",
    "file_level": "DEBUG",
    "console_format": (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[module]: <12}</cyan> | "
        "<level>{message}</level>"
    ) if not SIMPLE_OUTPUT == "true" else "<green>{time:MM-DD HH:mm}</green> | <cyan>{extra[module]}</cyan> | {message}",
    "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | {message}",
    "log_dir": LOG_ROOT,
    "rotation": "00:00",
    "retention": "3 days",
    "compression": "zip",
}

# 样式配置模板
BASE_STYLE_TEMPLATE = {
    "advanced": {
        "console_format": (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[module]: <12}</cyan> | "
            "<{color}>{module_tag}</{color}> | "
            "<{message_color}>{message}</{message_color}>"
        ),
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | {module_tag} | {message}",
    },
    "simple": {
        "console_format": "<green>{time:MM-DD HH:mm}</green> | <{color}>{module_tag}</{color}> | <{message_color}>{"
                          "message}</{message_color}>",
        "file_format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | {module_tag} | {message}",
    },
}


# 生成所有模块的样式配置
def create_style_config(module_config: dict) -> dict:
    tag = module_config["tag"]
    color = module_config["color"]
    message_color = module_config.get("message_color", "level")  # 默认为 level

    advanced_console = (
        BASE_STYLE_TEMPLATE["advanced"]["console_format"]
        .replace("{color}", color)
        .replace("{module_tag}", tag)
        .replace("{message_color}", message_color)
    )
    advanced_file = BASE_STYLE_TEMPLATE["advanced"]["file_format"].replace("{module_tag}", tag)
    simple_console = (
        BASE_STYLE_TEMPLATE["simple"]["console_format"]
        .replace("{color}", color)
        .replace("{module_tag}", tag)
        .replace("{message_color}", message_color)
    )
    simple_file = BASE_STYLE_TEMPLATE["simple"]["file_format"].replace("{module_tag}", tag)

    return {
        "advanced": {
            "console_format": advanced_console,
            "file_format": advanced_file,
        },
        "simple": {
            "console_format": simple_console,
            "file_format": simple_file,
        },
    }


# 创建所有模块的样式配置
STYLE_CONFIGS = {
    name: create_style_config(config)
    for name, config in MODULE_CONFIGS.items()
}

# 根据SIMPLE_OUTPUT选择配置
for module_name in STYLE_CONFIGS:
    STYLE_CONFIGS[module_name] = STYLE_CONFIGS[module_name]["simple" if SIMPLE_OUTPUT == "true" else "advanced"]

# 导出所有配置
globals().update({f"{name.upper()}_STYLE_CONFIG": config for name, config in STYLE_CONFIGS.items()})


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
        level=os.getenv("CONSOLE_LOG_LEVEL", console_level or current_config["console_level"]),
        format=current_config["console_format"],
        filter=lambda record: record["extra"].get("module") == module_name,
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
        level=os.getenv("FILE_LOG_LEVEL", file_level or current_config["file_level"]),
        format=current_config["file_format"],
        rotation=current_config["rotation"],
        retention=current_config["retention"],
        compression=current_config["compression"],
        encoding="utf-8",
        filter=lambda record: record["extra"].get("module") == module_name,
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


def remove_module_logger(module_name: str) -> None:
    """清理指定模块的日志处理器"""
    if module_name in _handler_registry:
        for handler_id in _handler_registry[module_name]:
            logger.remove(handler_id)
        del _handler_registry[module_name]


# 添加全局默认处理器（只处理未注册模块的日志--->控制台）
# print(os.getenv("DEFAULT_CONSOLE_LOG_LEVEL", "SUCCESS"))
DEFAULT_GLOBAL_HANDLER = logger.add(
    sink=sys.stderr,
    level=os.getenv("DEFAULT_CONSOLE_LOG_LEVEL", "SUCCESS"),
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
    level=os.getenv("DEFAULT_FILE_LOG_LEVEL", "DEBUG"),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name: <15} | {message}",
    rotation=DEFAULT_CONFIG["rotation"],
    retention=DEFAULT_CONFIG["retention"],
    compression=DEFAULT_CONFIG["compression"],
    encoding="utf-8",
    filter=lambda record: is_unregistered_module(record),  # 只处理未注册模块的日志，并过滤nonebot
    enqueue=True,
)
