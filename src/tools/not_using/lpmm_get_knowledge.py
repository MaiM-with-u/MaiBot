from src.tools.tool_can_use.base_tool import BaseTool

# from src.common.database import db
from src.common.logger import get_logger
from typing import Dict, Any
from src.chat.knowledge.knowledge_lib import qa_manager


logger = get_logger("lpmm_get_knowledge_tool")


class SearchKnowledgeFromLPMMTool(BaseTool):
    """从LPMM知识库中搜索相关信息的工具"""

    name = "lpmm_search_knowledge"
    description = "从知识库中搜索相关信息，如果你需要知识，就使用这个工具"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询关键词"},
            "threshold": {"type": "number", "description": "相似度阈值，0.0到1.0之间"},
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

            # 检查LPMM知识库是否启用
            if qa_manager is None:
                logger.debug("LPMM知识库已禁用，跳过知识获取")
                return {"type": "info", "id": query, "content": "LPMM知识库已禁用"}

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
