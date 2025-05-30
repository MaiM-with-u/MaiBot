from src.tools.tool_can_use.base_tool import BaseTool
from src.common.logger import get_module_logger
from typing import Any

logger = get_module_logger("compare_numbers_tool")


class CompareNumbersTool(BaseTool):
    """比较两个数大小的工具"""

    name = "compare_numbers"
    description = "Compare two numbers and determine their relationship (greater than, less than, or equal). Returns comparison result and the larger number if they are not equal."
    parameters = {
        "type": "object",
        "properties": {
            "num1": {"type": "number", "description": "The first number to compare."},
            "num2": {"type": "number", "description": "The second number to compare."},
        },
        "required": ["num1", "num2"],
    }

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行比较两个数的大小

        Args:
            function_args: 工具参数

        Returns:
            dict: 工具执行结果
        """
        try:
            num1 = function_args.get("num1")
            num2 = function_args.get("num2")

            if num1 > num2:
                result = f"{num1} 大于 {num2}"
            elif num1 < num2:
                result = f"{num1} 小于 {num2}"
            else:
                result = f"{num1} 等于 {num2}"

            return {"type": "comparison_result", "id": f"{num1}_vs_{num2}", "content": result}
        except Exception as e:
            logger.error(f"比较数字失败: {str(e)}")
            return {"type": "info", "id": f"{num1}_vs_{num2}", "content": f"比较数字失败，炸了: {str(e)}"}


# 注册工具
# register_tool(CompareNumbersTool)
