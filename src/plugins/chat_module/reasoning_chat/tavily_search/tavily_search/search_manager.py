import time
import asyncio
from typing import Dict, List, Optional, Tuple
import os
import re

from tavily import TavilyClient
from ....models.utils_model import LLM_request
from ....config.config import global_config
from src.common.logger import get_module_logger, LogConfig, LLM_STYLE_CONFIG

# 定义日志配置
llm_config = LogConfig(
    console_format=LLM_STYLE_CONFIG["console_format"],
    file_format=LLM_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("tavily_search", config=llm_config)

class SearchManager:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SearchManager()
        return cls._instance
    
    def __init__(self):
        """初始化搜索管理器"""
        # 确保单例模式
        if SearchManager._instance is not None:
            raise RuntimeError("SearchManager是单例类，请使用SearchManager.get_instance()获取实例")
        
        # 初始化搜索判断模型
        self.search_judge_model = LLM_request(
            model=global_config.llm_search_judge,
            temperature=0.2,
            max_tokens=100,
            request_type="search_judge"
        )
        
        # 配置tavily客户端
        self.tavily_api_key = os.environ.get("SEARCH_API_KEY", "")
        if not self.tavily_api_key:
            logger.warning("未找到Tavily API密钥，搜索功能将不可用")
        self.tavily_client = None
        if self.tavily_api_key:
            self.tavily_client = TavilyClient(self.tavily_api_key)
        
        # 初始化搜索配置
        self.enable_search = global_config.tavily_search.get("enable", False)
        self.search_probability = global_config.tavily_search.get("search_probability", 0.7)
        self.max_search_results = global_config.tavily_search.get("max_search_results", 3)
        
        logger.info(f"搜索管理器初始化完成，搜索功能启用状态: {self.enable_search}")
    
    async def should_search_and_extract_keywords(self, message_text: str) -> Tuple[bool, str, float, str]:
        """判断是否需要搜索并提取搜索关键词及主题
        
        Args:
            message_text: 用户消息文本
            
        Returns:
            (是否搜索, 搜索关键词, 置信度, 搜索主题)
        """
        if not self.enable_search or not self.tavily_client:
            return False, "", 0.0, "general"
        
        # 构建组合提示词，同时判断搜索需求并提取关键词
        prompt = f"""
        请同时完成以下三个任务：

        任务1：判断用户消息是否需要进行搜索引擎查询获取信息
        任务2：提取适合搜索的关键词或短语
        任务3：确定搜索主题类型

        用户消息: "{message_text}"

        判断标准:
        需要搜索的情况：询问最新消息、新闻、事实数据、专业知识、明确要求查找信息
        不需要搜索的情况：日常问候、情感表达、基于已有对话的提问、简单常识问题

        关键词提取要求:
        1. 提取核心问题相关的关键词或短语
        2. 保留重要的专有名词、人名、地名、时间等
        3. 去除闲聊内容和修饰性词语
        4. 使用完整的问句或关键词组合
        5. 控制在100个字符以内

        主题类型判断:
        - "news": 与新闻、时事、最新事件相关的查询
        - "finance": 与金融、投资、经济、股市相关的查询
        - "general": 其他类型的一般性查询

        返回格式：
        搜索评分：[0-1之间的数值]
        搜索关键词：[提取的关键词]
        搜索主题：[news/finance/general]

        请注意：搜索评分0表示完全不需要搜索，1表示非常需要搜索。
        """
        
        try:
            # 调用模型进行判断
            start_time = time.time()
            result, _, _ = await self.search_judge_model.generate_response(prompt)
            
            # 解析结果
            search_score = 0.0
            keywords = ""
            topic = "general"  # 默认主题
            
            for line in result.strip().split('\n'):
                line = line.strip()
                if line.startswith("搜索评分：") or line.startswith("搜索评分:"):
                    score_text = line.split('：' if '：' in line else ':', 1)[1].strip()
                    search_score = self._extract_score(score_text)
                elif line.startswith("搜索关键词：") or line.startswith("搜索关键词:"):
                    keywords = line.split('：' if '：' in line else ':', 1)[1].strip()
                elif line.startswith("搜索主题：") or line.startswith("搜索主题:"):
                    topic_text = line.split('：' if '：' in line else ':', 1)[1].strip().lower()
                    if topic_text in ["news", "finance", "general"]:
                        topic = topic_text
            
            # 如果关键词提取失败，使用原始消息
            if not keywords or len(keywords) < 5:
                keywords = message_text
            
            # 判断是否执行搜索
            should_search = search_score >= self.search_probability
            
            # 记录结果
            logger.info(f"搜索判断：分数={search_score:.2f}, 阈值={self.search_probability}, "
                        f"关键词='{keywords}', 主题='{topic}', 用时={time.time() - start_time:.2f}秒")
                        
            return should_search, keywords, search_score, topic
            
        except Exception as e:
            logger.error(f"搜索判断和关键词提取出错: {e}")
            return False, "", 0.0, "general"
    
    def _extract_score(self, result: str) -> float:
        """从模型结果中提取搜索分数"""
        try:
            # 清理结果文本
            cleaned_result = result.strip()
            # 尝试直接解析为浮点数
            score = float(cleaned_result)
            # 确保分数在0-1范围内
            return max(0.0, min(1.0, score))
        except ValueError:
            # 如果无法直接解析，尝试通过正则表达式提取
            score_match = re.search(r'(\d+(\.\d+)?)', cleaned_result)
            if score_match:
                try:
                    score = float(score_match.group(1))
                    return max(0.0, min(1.0, score))
                except ValueError:
                    pass
            # 默认返回0，表示不搜索
            logger.warning(f"无法从结果中提取搜索分数: {result}")
            return 0.0
    
    async def perform_search(self, query: str, topic: str = "general") -> Optional[str]:
        """执行搜索并整理结果
        
        Args:
            query: 搜索查询文本
            topic: 搜索主题，可以是"general"、"news"或"finance"
            
        Returns:
            整理后的搜索结果，如果搜索失败则返回None
        """
        if not self.tavily_client:
            return None
        
        try:
            # 执行搜索
            logger.info(f"开始搜索: 关键词='{query}', 主题='{topic}'")
            start_time = time.time()
            
            # 异步搜索
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.tavily_client.search(
                    query=query,
                    topic=topic,
                    max_results=self.max_search_results
                )
            )
            # response = response['title']+'\n'+response['content']
            search_time = time.time() - start_time
            logger.info(f"搜索完成，耗时: {search_time:.2f}秒，找到{len(response.get('results', []))}条结果")
            
            # 整理搜索结果
            return await self._process_search_results(query, response, topic)
        except Exception as e:
            logger.error(f"搜索出错: {e}")
            return None
    
    async def _process_search_results(self, query: str, response: Dict, topic: str = "general") -> Optional[str]:
        """处理和整理搜索结果
        
        Args:
            query: 原始搜索查询
            response: Tavily API的响应
            topic: 搜索主题
            
        Returns:
            整理后的知识文本
        """
        if not response or "results" not in response or not response["results"]:
            return None
        
        # 提取搜索结果
        results = response["results"]
        
        # 记录原始搜索结果
        logger.info(f"原始搜索结果({len(results)}条), 主题={topic}:")
        for i, result in enumerate(results, 1):
            title = result.get('title', '无标题')
            url = result.get('url', '未知来源')
            content_preview = result.get('content', '无内容')[:150].replace("\n", " ")
            published_date = result.get('published_date', '') if topic == "news" else ""
            date_info = f" | 发布日期: {published_date}" if published_date else ""
            logger.info(f"结果[{i}] 标题: {title} | 来源: {url}{date_info} | 内容预览: {content_preview}...")
        
        # 根据主题类型，处理搜索结果
        processed_results = []
        for result in results:
            title = result.get('title', '无标题')
            content = result.get('content', '无内容')
            
            # 不再限制内容长度，保留完整内容
            # 构建单条结果
            processed_result = f"标题: {title}\n"
            
            # 对于新闻主题，添加发布日期
            if topic == "news" and "published_date" in result:
                published_date = result.get('published_date', '')
                if published_date:
                    processed_result += f"发布日期: {published_date}\n"
                    
            processed_result += f"内容: {content}"
            processed_results.append(processed_result)
        
        # 将所有结果合并为一个字符串
        raw_content = "\n\n---\n\n".join(processed_results)
        
        prompt = f"""
        请将以下搜索结果整理为详细、完整的知识摘要，以便聊天机器人能够提供全面信息。
        
        搜索查询: "{query}"
        搜索主题: "{topic}"
        
        搜索结果:
        {raw_content}
        
        要求:
        1. 保持客观准确，不要添加不在原始结果中的信息
        2. 去除冗余和重复内容，但保留所有重要细节
        3. 按逻辑顺序组织信息
        4. 保留所有重要的数字、日期、名称、地点等关键事实
        5. 使用清晰的标题和小标题结构化信息
        6. 确保内容完整，不要截断或简化重要信息
        7. 如果有多个来源的信息，请按主题组织，而不是按来源
        8. 如果存在不同来源的矛盾信息，请指出这些差异
        
        格式要求:
        - 使用markdown格式，用标题和小标题组织内容
        - 对复杂概念提供简短解释
        - 确保输出信息丰富且完整
        
        请输出一个全面且完整的知识摘要:
        """
        
        try:
            # 创建一个新的搜索结果整理模型，使用更大的token限制
            start_time = time.time()
            
            # 创建一个新的搜索结果整理模型，使用更大的token限制
            try:
                # 创建一个全新的高容量结果整理模型
                summary_model = LLM_request(
                    model=global_config.llm_summary_by_topic,  # 使用摘要模型代替搜索判断模型
                    temperature=0.3,
                    max_tokens=1500,  # 大幅增加token输出限制
                    request_type="search_summary"
                )
                
                # 使用新模型进行结果整理
                result, reasoning_content, _ = await summary_model.generate_response(prompt)
                logger.info(f"使用摘要模型整理搜索结果完成，耗时: {time.time() - start_time:.2f}秒")
            except Exception as inner_e:
                logger.error(f"摘要模型调用失败: {inner_e}，尝试使用原始搜索模型")
                try:
                    # 如果摘要模型失败，回退到使用原始搜索判断模型
                    result, reasoning_content, _ = await self.search_judge_model.generate_response(prompt)
                    logger.info(f"使用原始搜索模型整理结果完成，耗时: {time.time() - start_time:.2f}秒")
                except Exception as fallback_e:
                    logger.error(f"所有模型整理失败: {fallback_e}")
                    # 如果所有模型都失败，返回原始内容
                    result = raw_content
            
            # 如果结果显得不完整（如以逗号、省略号结尾或中间有明显截断），补充说明
            result = result.strip()
            if result.endswith((",", ".", ":", "...", "…")) or "..." in result[-20:]:
                result += "\n\n(注：由于内容较长，摘要可能不完整，但已包含主要信息)"
            
            return result
        except Exception as e:
            logger.error(f"搜索结果整理出错: {e}")
            # 如果整理失败，返回原始内容的简化版本
            simplified_results = []
            for i, result in enumerate(results, 1):
                title = result.get('title', '无标题')
                content_summary = result.get('content', '无内容')
                if len(content_summary) > 150:
                    content_summary = content_summary[:147] + "..."
                simplified_results.append(f"{i}. {title}: {content_summary}")
            
            return "搜索结果摘要:\n\n" + "\n\n".join(simplified_results) 