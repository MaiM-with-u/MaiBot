# Configure logger

from src.common.logger import get_module_logger, LogConfig, LPMM_STYLE_CONFIG

lpmm_log_config = LogConfig(
    console_format=LPMM_STYLE_CONFIG["console_format"],
    file_format=LPMM_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("LPMM", config=lpmm_log_config)
