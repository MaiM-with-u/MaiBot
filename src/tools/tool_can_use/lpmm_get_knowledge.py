from src.tools.tool_can_use.base_tool import BaseTool

# from src.common.database import db
from src.common.logger_manager import get_logger
from typing import Dict, Any
from src.chat.knowledge.knowledge_lib import qa_manager


logger = get_logger("lpmm_get_knowledge_tool")


class SearchKnowledgeFromLPMMTool(BaseTool):
    """从LPMM知识库中搜索相关信息的工具"""

    name = "lpmm_search_knowledge"
    description = "Search relevant information from knowledge base, use this tool if you need knowledge"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query keywords"},
            "threshold": {"type": "number", "description": "Similarity threshold, between 0.0 and 1.0"},
        },
        "required": ["query"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行知识库搜索

        Args:
            function_args: 工具参数

        Returns:
            Dict: 工具执行结果
        """
        try:
            query = function_args.get("query")
            # threshold = function_args.get("threshold", 0.4)

            # 调用知识库搜索

            knowledge_info = qa_manager.get_knowledge(query)

            logger.debug(f"知识库查询结果: {knowledge_info}")

            if knowledge_info:
                content = f"你知道这些知识: {knowledge_info}"
            else:
                content = f"你不太了解有关{query}的知识"
            return {"type": "lpmm_knowledge", "id": query, "content": content}
        except Exception as e:
            # 捕获异常并记录错误
            logger.error(f"知识库搜索工具执行失败: {str(e)}")
            # 在其他异常情况下，确保 id 仍然是 query (如果它被定义了)
            query_id = query if "query" in locals() else "unknown_query"
            return {"type": "info", "id": query_id, "content": f"lpmm知识库搜索失败，炸了: {str(e)}"}
