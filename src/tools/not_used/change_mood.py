from typing import Any

from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.tools.tool_can_use.base_tool import BaseTool
from src.manager.mood_manager import mood_manager

logger = get_logger("change_mood_tool")


class ChangeMoodTool(BaseTool):
    """改变心情的工具"""

    name = "change_mood"
    description = "Change mood based on received content and your own reply content, you can use this tool when you reply to someone's message"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text that causes you to change your mood"},
            "response_set": {"type": "list", "description": "Your reply to the text"},
        },
        "required": ["text", "response_set"],
    }

    async def execute(self, function_args: dict[str, Any], message_txt: str = "") -> dict[str, Any]:
        """执行心情改变

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本

        Returns:
            dict: 工具执行结果
        """
        try:
            response_set = function_args.get("response_set")
            _message_processed_plain_text = function_args.get("text")

            # gpt = ResponseGenerator()

            if response_set is None:
                response_set = ["你还没有回复"]

            _ori_response = ",".join(response_set)
            # _stance, emotion = await gpt._get_emotion_tags(ori_response, message_processed_plain_text)
            emotion = "平静"
            mood_manager.update_mood_from_emotion(emotion, global_config.mood.mood_intensity_factor)
            return {"name": "change_mood", "content": f"你的心情刚刚变化了，现在的心情是: {emotion}"}
        except Exception as e:
            logger.error(f"心情改变工具执行失败: {str(e)}")
            return {"name": "change_mood", "content": f"心情改变失败: {str(e)}"}


# 注册工具
# register_tool(ChangeMoodTool)
