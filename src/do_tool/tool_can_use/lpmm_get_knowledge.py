from src.do_tool.tool_can_use.base_tool import BaseTool
from src.plugins.chat.utils import get_embedding

# from src.common.database import db
from src.common.logger import get_module_logger
from typing import Dict, Any
from src.plugins.knowledge.knowledge_lib import qa_manager


logger = get_module_logger("lpmm_get_knowledge_tool")


class SearchKnowledgeFromLPMMTool(BaseTool):
    """从LPMM知识库中搜索相关信息的工具"""

    name = "lpmm_search_knowledge"
    description = "从知识库中搜索相关信息"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询关键词"},
            "threshold": {"type": "number", "description": "相似度阈值，0.0到1.0之间"},
        },
        "required": ["query"],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """执行知识库搜索

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本

        Returns:
            Dict: 工具执行结果
        """
        try:
            query = function_args.get("query", message_txt)
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
                return {"name": "search_knowledge", "content": content}
            return {"name": "search_knowledge", "content": f"无法获取关于'{query}'的嵌入向量"}
        except Exception as e:
            logger.error(f"知识库搜索工具执行失败: {str(e)}")
            return {"name": "search_knowledge", "content": f"知识库搜索失败: {str(e)}"}

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


# 注册工具
# register_tool(SearchKnowledgeTool)
