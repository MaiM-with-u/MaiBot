from src.tools.tool_can_use.base_tool import BaseTool
from src.chat.memory_system.Hippocampus import HippocampusManager
from src.common.logger import get_module_logger
from typing import Dict, Any

logger = get_module_logger("mid_chat_mem_tool")


class GetMemoryTool(BaseTool):
    """从记忆系统中获取相关记忆的工具"""

    name = "get_memory"
    description = "Use tool to retrieve relevant memories from the memory system"
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Related topics to query, separated by commas"},
            "max_memory_num": {"type": "integer", "description": "Maximum number of memories to return"},
        },
        "required": ["topic"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行记忆获取

        Args:
            function_args: 工具参数

        Returns:
            Dict: 工具执行结果
        """
        try:
            topic = function_args.get("topic")
            max_memory_num = function_args.get("max_memory_num", 2)

            # 将主题字符串转换为列表
            topic_list = topic.split(",")

            # 调用记忆系统
            related_memory = await HippocampusManager.get_instance().get_memory_from_topic(
                valid_keywords=topic_list, max_memory_num=max_memory_num, max_memory_length=2, max_depth=3
            )

            memory_info = ""
            if related_memory:
                for memory in related_memory:
                    memory_info += memory[1] + "\n"

            if memory_info:
                content = f"You remember these things: {memory_info}\n"
                content += "The above are your memories, not necessarily what people in the current chat said, nor necessarily what is happening now, please remember.\n"

            else:
                content = f"Memories about {topic}, you don't remember clearly"

            return {"type": "memory", "id": topic_list, "content": content}
        except Exception as e:
            logger.error(f"Memory retrieval tool execution failed: {str(e)}")
            # Keep format consistent on failure, but id may not apply or be set to None/Error
            return {"type": "memory_error", "id": topic_list, "content": f"Memory retrieval failed: {str(e)}"}


# 注册工具
# register_tool(GetMemoryTool)
