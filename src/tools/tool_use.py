import json
from src.common.logger_manager import get_logger
from src.tools.tool_can_use import get_all_tool_definitions, get_tool_instance

logger = get_logger("tool_use")


class ToolUser:
    @staticmethod
    def _define_tools():
        """获取所有已注册工具的定义

        Returns:
            list: 工具定义列表
        """
        return get_all_tool_definitions()

    @staticmethod
    async def _execute_tool_call(tool_call):
        """执行特定的工具调用

        Args:
            tool_call: 工具调用对象
            message_txt: 原始消息文本

        Returns:
            dict: 工具调用结果
        """
        try:
            function_name = tool_call["function"]["name"]
            function_args = json.loads(tool_call["function"]["arguments"])

            # 获取对应工具实例
            tool_instance = get_tool_instance(function_name)
            if not tool_instance:
                logger.warning(f"未知工具名称: {function_name}")
                return None

            # 执行工具
            result = await tool_instance.execute(function_args)
            if result:
                # 直接使用 function_name 作为 tool_type
                tool_type = function_name

                return {
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": function_name,
                    "type": tool_type,
                    "content": result["content"],
                }
            return None
        except Exception as e:
            logger.error(f"执行工具调用时发生错误: {str(e)}")
            return None
