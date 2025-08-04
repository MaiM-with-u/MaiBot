import difflib
import json

from json_repair import repair_json
from typing import List, Dict
from datetime import datetime

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.memory_system.Hippocampus import hippocampus_manager


logger = get_logger("memory_activator")


def get_keywords_from_json(json_str) -> List:
    """
    从JSON字符串中提取关键词列表

    Args:
        json_str: JSON格式的字符串

    Returns:
        List[str]: 关键词列表
    """
    try:
        # 使用repair_json修复JSON格式
        fixed_json = repair_json(json_str)

        # 如果repair_json返回的是字符串，需要解析为Python对象
        result = json.loads(fixed_json) if isinstance(fixed_json, str) else fixed_json
        return result.get("keywords", [])
    except Exception as e:
        logger.error(f"解析关键词JSON失败: {e}")
        return []


def init_prompt():
    # --- Group Chat Prompt ---
    memory_activator_prompt = """
    你是一个记忆分析器，你需要根据以下信息来进行回忆
    以下是一段聊天记录，请根据这些信息，总结出几个关键词作为记忆回忆的触发词
    
    聊天记录:
    {obs_info_text}
    你想要回复的消息:
    {target_message}
    
    历史关键词（请避免重复提取这些关键词）：
    {cached_keywords}
    
    请输出一个json格式，包含以下字段：
    {{
        "keywords": ["关键词1", "关键词2", "关键词3",......]
    }}
    不要输出其他多余内容，只输出json格式就好
    """

    Prompt(memory_activator_prompt, "memory_activator_prompt")


class MemoryActivator:
    def __init__(self):
        self.key_words_model = LLMRequest(
            model_set=model_config.model_task_config.utils_small,
            request_type="memory.activator",
        )

        self.running_memory = []
        self.cached_keywords = set()  # 用于缓存历史关键词

    async def activate_memory_with_chat_history(self, target_message, chat_history_prompt) -> List[Dict]:
        """
        激活记忆
        """
        # 如果记忆系统被禁用，直接返回空列表
        if not global_config.memory.enable_memory:
            return []

        # 将缓存的关键词转换为字符串，用于prompt
        cached_keywords_str = ", ".join(self.cached_keywords) if self.cached_keywords else "暂无历史关键词"

        prompt = await global_prompt_manager.format_prompt(
            "memory_activator_prompt",
            obs_info_text=chat_history_prompt,
            target_message=target_message,
            cached_keywords=cached_keywords_str,
        )

        # logger.debug(f"prompt: {prompt}")

        response, (reasoning_content, model_name, _) = await self.key_words_model.generate_response_async(
            prompt, temperature=0.5
        )

        keywords = list(get_keywords_from_json(response))

        # 更新关键词缓存
        if keywords:
            # 限制缓存大小，最多保留10个关键词
            if len(self.cached_keywords) > 10:
                # 转换为列表，移除最早的关键词
                cached_list = list(self.cached_keywords)
                self.cached_keywords = set(cached_list[-8:])

            # 添加新的关键词到缓存
            self.cached_keywords.update(keywords)

        # 调用记忆系统获取相关记忆
        related_memory = await hippocampus_manager.get_memory_from_topic(
            valid_keywords=keywords, max_memory_num=3, max_memory_length=2, max_depth=3
        )

        logger.debug(f"当前记忆关键词: {self.cached_keywords} ")
        logger.debug(f"获取到的记忆: {related_memory}")

        # 激活时，所有已有记忆的duration+1，达到3则移除
        for m in self.running_memory[:]:
            m["duration"] = m.get("duration", 1) + 1
        self.running_memory = [m for m in self.running_memory if m["duration"] < 3]

        if related_memory:
            for topic, memory in related_memory:
                # 检查是否已存在相同topic或相似内容（相似度>=0.7）的记忆
                exists = any(
                    m["topic"] == topic or difflib.SequenceMatcher(None, m["content"], memory).ratio() >= 0.7
                    for m in self.running_memory
                )
                if not exists:
                    self.running_memory.append(
                        {"topic": topic, "content": memory, "timestamp": datetime.now().isoformat(), "duration": 1}
                    )
                    logger.debug(f"添加新记忆: {topic} - {memory}")

        # 限制同时加载的记忆条数，最多保留最后3条
        if len(self.running_memory) > 3:
            self.running_memory = self.running_memory[-3:]

        return self.running_memory


init_prompt()
