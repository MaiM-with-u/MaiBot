# Configure logger

from src.common.logger import get_module_logger, LogConfig, KNOWLEDGE_STYLE_CONFIG

lib_config = LogConfig(
    # 使用知识专用样式
    console_format=KNOWLEDGE_STYLE_CONFIG["console_format"],
    file_format=KNOWLEDGE_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("knowledge", config=lib_config)
