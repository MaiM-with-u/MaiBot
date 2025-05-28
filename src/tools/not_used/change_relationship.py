from typing import Any
from src.common.logger_manager import get_logger
from src.tools.tool_can_use.base_tool import BaseTool


logger = get_logger("relationship_tool")


class RelationshipTool(BaseTool):
    name = "change_relationship"
    description = "Modify relationship value with specific user based on received text and reply content, you can use this tool when you reply to someone's message"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Received text"},
            "changed_value": {"type": "number", "description": "Change value"},
            "reason": {"type": "string", "description": "Reason for change"},
        },
        "required": ["text", "changed_value", "reason"],
    }

    async def execute(self, function_args: dict[str, Any], message_txt: str = "") -> dict:
        """执行工具功能

        Args:
            function_args: 包含工具参数的字典
            message_txt: 原始消息文本

        Returns:
            dict: 包含执行结果的字典
        """
        try:
            text = function_args.get("text")
            changed_value = function_args.get("changed_value")
            reason = function_args.get("reason")

            return {"content": f"因为你刚刚因为{reason}，所以你和发[{text}]这条消息的人的关系值变化为{changed_value}"}

        except Exception as e:
            logger.error(f"修改关系值时发生错误: {str(e)}")
            return {"content": f"修改关系值失败: {str(e)}"}
