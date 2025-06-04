from src.tools.tool_can_use.base_tool import BaseTool, run_lua_code
from src.common.logger import get_module_logger
from typing import Any

logger = get_module_logger("compare_numbers_tool")


class CompareNumbersTool(BaseTool):
    """比较两个数大小的工具"""

    name = "compare_numbers"
    description = "使用工具 比较两个数的大小，返回较大的数"
    parameters = {
        "type": "object",
        "properties": {
            "num1": {"type": "number", "description": "第一个数字"},
            "num2": {"type": "number", "description": "第二个数字"},
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
            if not (isinstance(num1, (int, float)) and isinstance(num2, (int, float))):
                raise ValueError("参数'num1'和'num2'必须为数字")
            lua_code = """
                function CompareNumbers(a, b)
                    return a .. (a > b and " 大于 " or a < b and " 小于 " or " 等于 ") .. b
                end
            """
            CompareNumbers = run_lua_code(lua_code).CompareNumbers
            result = CompareNumbers(num1, num2)

            return {"type": "comparison_result", "id": f"{num1}_vs_{num2}", "content": result}
        except Exception as e:
            logger.error(f"比较数字失败: {str(e)}")
            return {"type": "info", "id": f"{num1}_vs_{num2}", "content": f"比较数字失败，炸了: {str(e)}"}


# 注册工具
# register_tool(CompareNumbersTool)
