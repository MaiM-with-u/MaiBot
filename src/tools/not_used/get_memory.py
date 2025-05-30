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
                content = f"你记得这些事情: {memory_info}\n"
                content += "以上是你的回忆，不一定是目前聊天里的人说的，也不一定是现在发生的事情，请记住。\n"

            else:
                content = f"{topic}的记忆，你记不太清"

            return {"type": "memory", "id": topic_list, "content": content}
        except Exception as e:
            logger.error(f"记忆获取工具执行失败: {str(e)}")
            # 在失败时也保持格式一致，但id可能不适用或设为None/Error
            return {"type": "memory_error", "id": topic_list, "content": f"记忆获取失败: {str(e)}"}


# 注册工具
# register_tool(GetMemoryTool)
