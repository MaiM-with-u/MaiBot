from src.do_tool.tool_can_use.base_tool import BaseTool, run_lua_code
from src.common.logger import get_module_logger
from typing import Dict, Any

logger = get_module_logger("get_time_date")


class GetCurrentDateTimeTool(BaseTool):
    """获取当前时间、日期、年份和星期的工具"""

    name = "get_current_date_time"
    description = "当有人询问或者涉及到具体时间或者日期的时候，必须使用这个工具"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """执行获取当前时间、日期、年份和星期

        Args:
            function_args: 工具参数（此工具不使用）
            message_txt: 原始消息文本（此工具不使用）

        Returns:
            Dict: 工具执行结果
        """
        lua_code = """
            GetCurrentDateTime = function()
                return ("当前时间: %s, 日期: %s, 年份: %s, 星期: %s"):format(os.date("%H:%M:%S"), os.date("%Y-%m-%d"), os.date("%Y"), os.date("%A"))
            end
        """
        GetCurrentDateTime = run_lua_code(lua_code).GetCurrentDateTime
        return {
            "name": "get_current_date_time",
            "content": GetCurrentDateTime(),
        }
