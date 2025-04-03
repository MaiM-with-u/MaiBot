from typing import List, Tuple
from src.common.logger import get_module_logger
from src.plugins.memory_system.Hippocampus import HippocampusManager
from ..models.utils_model import LLM_request
from ..config.config import global_config
from ..chat.message import Message

logger = get_module_logger("knowledge_fetcher")

class KnowledgeFetcher:
    """知识调取器"""
    
    def __init__(self):
        self.llm = LLM_request(
            model=global_config.llm_normal,
            temperature=0.7,
            max_tokens=1000,
            request_type="knowledge_fetch"
        )
        
    async def fetch(self, query: str, chat_history: List[Message]) -> Tuple[str, str]:
        """获取相关知识
        
        Args:
            query: 查询内容
            chat_history: 聊天历史
            
        Returns:
            Tuple[str, str]: (获取的知识, 知识来源)
        """
        # 构建查询上下文
        chat_history_text = ""
        for msg in chat_history:
            # sender = msg.message_info.user_info.user_nickname or f"用户{msg.message_info.user_info.user_id}"
            chat_history_text += f"{msg.detailed_plain_text}\n"
        
        # 从记忆中获取相关知识
        related_memory = await HippocampusManager.get_instance().get_memory_from_text(
            text=f"{query}\n{chat_history_text}",
            max_memory_num=3,
            max_memory_length=2,
            max_depth=3,
            fast_retrieval=False
        )
        
        if related_memory:
            knowledge = ""
            sources = []
            for memory in related_memory:
                knowledge += memory[1] + "\n"
                sources.append(f"记忆片段{memory[0]}")
            return knowledge.strip(), "，".join(sources)
        
        return "未找到相关知识", "无记忆匹配" 