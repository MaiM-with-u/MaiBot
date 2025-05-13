from src.tools.tool_can_use.base_tool import BaseTool
from src.chat.utils.utils import get_embedding

# from src.common.database import db
from src.common.logger_manager import get_logger
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

            # 调用知识库搜索
            embedding = await get_embedding(query, request_type="info_retrieval")
            if embedding:
                knowledge_info = qa_manager.get_knowledge(query)
                logger.debug(f"知识库查询结果: {knowledge_info}")
                if knowledge_info:
                    content = f"你知道这些知识: {knowledge_info}"
                else:
                    content = f"你不太了解有关{query}的知识"
                return {"type": "lpmm_knowledge", "id": query, "content": content}
            # 如果获取嵌入失败
            return {"type": "info", "id": query, "content": f"无法获取关于'{query}'的嵌入向量，你lpmm知识库炸了"}
        except Exception as e:
            logger.error(f"知识库搜索工具执行失败: {str(e)}")
            # 在其他异常情况下，确保 id 仍然是 query (如果它被定义了)
            query_id = query if "query" in locals() else "unknown_query"
            return {"type": "info", "id": query_id, "content": f"lpmm知识库搜索失败，炸了: {str(e)}"}

    # def get_info_from_db(
    #     self, query_embedding: list, limit: int = 1, threshold: float = 0.5, return_raw: bool = False
    # ) -> Union[str, list]:
    #     """从数据库中获取相关信息

    #     Args:
    #         query_embedding: 查询的嵌入向量
    #         limit: 最大返回结果数
    #         threshold: 相似度阈值
    #         return_raw: 是否返回原始结果

    #     Returns:
    #         Union[str, list]: 格式化的信息字符串或原始结果列表
    #     """
    #     if not query_embedding:
    #         return "" if not return_raw else []

    #     # 使用余弦相似度计算
    #     pipeline = [
    #         {
    #             "$addFields": {
    #                 "dotProduct": {
    #                     "$reduce": {
    #                         "input": {"$range": [0, {"$size": "$embedding"}]},
    #                         "initialValue": 0,
    #                         "in": {
    #                             "$add": [
    #                                 "$$value",
    #                                 {
    #                                     "$multiply": [
    #                                         {"$arrayElemAt": ["$embedding", "$$this"]},
    #                                         {"$arrayElemAt": [query_embedding, "$$this"]},
    #                                     ]
    #                                 },
    #                             ]
    #                         },
    #                     }
    #                 },
    #                 "magnitude1": {
    #                     "$sqrt": {
    #                         "$reduce": {
    #                             "input": "$embedding",
    #                             "initialValue": 0,
    #                             "in": {"$add": ["$$value", {"$multiply": ["$$this", "$$this"]}]},
    #                         }
    #                     }
    #                 },
    #                 "magnitude2": {
    #                     "$sqrt": {
    #                         "$reduce": {
    #                             "input": query_embedding,
    #                             "initialValue": 0,
    #                             "in": {"$add": ["$$value", {"$multiply": ["$$this", "$$this"]}]},
    #                         }
    #                     }
    #                 },
    #             }
    #         },
    #         {"$addFields": {"similarity": {"$divide": ["$dotProduct", {"$multiply": ["$magnitude1", "$magnitude2"]}]}}},
    #         {
    #             "$match": {
    #                 "similarity": {"$gte": threshold}  # 只保留相似度大于等于阈值的结果
    #             }
    #         },
    #         {"$sort": {"similarity": -1}},
    #         {"$limit": limit},
    #         {"$project": {"content": 1, "similarity": 1}},
    #     ]

    #     results = list(db.knowledges.aggregate(pipeline))
    #     logger.debug(f"知识库查询结果数量: {len(results)}")

    #     if not results:
    #         return "" if not return_raw else []

    #     if return_raw:
    #         return results
    #     else:
    #         # 返回所有找到的内容，用换行分隔
    #         return "\n".join(str(result["content"]) for result in results)

    def _format_results(self, results: list) -> str:
        """格式化结果"""
        if not results:
            return "未找到相关知识。"

        formatted_string = "我找到了一些相关知识：\n"
        for i, result in enumerate(results):
            # chunk_id = result.get("chunk_id")
            text = result.get("text", "")
            source = result.get("source", "未知来源")
            source_type = result.get("source_type", "未知类型")
            similarity = result.get("similarity", 0.0)

            formatted_string += (
                f"{i + 1}. (相似度: {similarity:.2f}) 类型: {source_type}, 来源: {source} \n内容片段: {text}\n\n"
            )
            # 暂时去掉chunk_id
            # formatted_string += f"{i + 1}. (相似度: {similarity:.2f}) 类型: {source_type}, 来源: {source}, Chunk ID: {chunk_id} \n内容片段: {text}\n\n"

        return formatted_string


# 注册工具
# register_tool(SearchKnowledgeTool)
