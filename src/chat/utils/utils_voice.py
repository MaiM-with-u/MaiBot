from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

from src.common.logger import get_logger
from rich.traceback import install

install(extra_lines=3)

logger = get_logger("chat_voice")


async def get_voice_text(voice_base64: str) -> str:
    """获取音频文件转录文本"""
    if not global_config.voice.enable_asr:
        logger.warning("语音识别未启用，无法处理语音消息")
        return "[语音]"
    try:
        _llm = LLMRequest(model_set=model_config.model_task_config.voice, request_type="audio")
        text = await _llm.generate_response_for_voice(voice_base64)
        if text is None:
            logger.warning("未能生成语音文本")
            return "[语音(文本生成失败)]"

        logger.debug(f"描述是{text}")

        return f"[语音：{text}]"
    except Exception as e:
        logger.error(f"语音转文字失败: {str(e)}")
        return "[语音]"
