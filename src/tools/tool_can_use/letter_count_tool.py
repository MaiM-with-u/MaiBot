import re
from src.tools.tool_can_use.base_tool import BaseTool, run_lua_code
from src.common.logger import get_module_logger
from typing import Dict, Any

logger = get_module_logger("letter_count_tool")


class LetterCountTool(BaseTool):
    """数单词内某字母个数的工具"""

    name = "word_letter_count"
    description = "当有人询问你或者提到某个英文单词内有多少个某字母时，可以使用这个工具来数字母（如果传入的是中文，传入之前要将中文转为英文）"
    parameters = {
        "type": "object",
        "properties": {
            "word": {"type": "string", "description": "英文单词"},
            "letter": {"type": "string", "description": "英文字母"},
        },
        "required": ["word", "letter"],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """
        执行数数该单词的某字母个数的函数

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本

        Returns:
            Dict: 工具执行结果
        """
        try:
            word = function_args.get("word")
            letter = function_args.get("letter")
            if re.match(r"^[a-zA-Z]$", letter) is None:
                raise ValueError("请输入单个英文字母")
            lua_code = """
                function LetterCount(inputStr, targetLetter)
                    local lower = (inputStr:gsub("[^"..targetLetter:lower().."]", "")):len()
                    local upper = (inputStr:gsub("[^"..targetLetter:upper().."]", "")):len()
                    return string.format("字母 %s 在字符串 %s 中出现的次数：%d个（小写）, %d个（大写）", targetLetter, inputStr, lower, upper)
                end
            """
            LetterCount = run_lua_code(lua_code).LetterCount
            return {"name": self.name, "content": LetterCount(word, letter)}
        except Exception as e:
            logger.error(f"数字母失败: {str(e)}")
            return {"name": self.name, "content": f"数字母失败: {str(e)}"}


# 注册工具
# register_tool(LetterCountTool)
