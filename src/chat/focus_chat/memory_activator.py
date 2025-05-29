from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.heart_flow.observation.structure_observation import StructureObservation
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.common.logger_manager import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from datetime import datetime
from src.chat.memory_system.Hippocampus import HippocampusManager
from typing import List, Dict
import difflib
import json
from json_repair import repair_json


logger = get_logger("memory_activator")


def get_keywords_from_json(json_str):
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
        if isinstance(fixed_json, str):
            result = json.loads(fixed_json)
        else:
            # 如果repair_json直接返回了字典对象，直接使用
            result = fixed_json

        # 提取关键词
        keywords = result.get("keywords", [])
        return keywords
    except Exception as e:
        logger.error(f"解析关键词JSON失败: {e}")
        return []


def init_prompt():
    # --- Group Chat Prompt ---
    memory_activator_prompt = """
    你是一个记忆分析器，你需要根据以下信息来进行回忆
    以下是一场聊天中的信息，请根据这些信息，总结出几个关键词作为记忆回忆的触发词
    
    {obs_info_text}
    
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
        # TODO: API-Adapter修改标记
        self.summary_model = LLMRequest(
            model=global_config.model.memory_summary,
            temperature=0.7,
            max_tokens=50,
            request_type="focus.memory_activator",
        )
        self.running_memory = []
        self.cached_keywords = set()  # 用于缓存历史关键词

    async def activate_memory(self, observations) -> List[Dict]:
        """
        激活记忆

        Args:
            observations: 现有的进行观察后的 观察列表

        Returns:
            List[Dict]: 激活的记忆列表
        """
        obs_info_text = ""
        for observation in observations:
            if isinstance(observation, ChattingObservation):
                obs_info_text += observation.get_observe_info()
            elif isinstance(observation, StructureObservation):
                working_info = observation.get_observe_info()
                for working_info_item in working_info:
                    obs_info_text += f"{working_info_item['type']}: {working_info_item['content']}\n"
            elif isinstance(observation, HFCloopObservation):
                obs_info_text += observation.get_observe_info()

        # logger.debug(f"回忆待检索内容：obs_info_text: {obs_info_text}")

        # 将缓存的关键词转换为字符串，用于prompt
        cached_keywords_str = ", ".join(self.cached_keywords) if self.cached_keywords else "暂无历史关键词"

        prompt = await global_prompt_manager.format_prompt(
            "memory_activator_prompt",
            obs_info_text=obs_info_text,
            cached_keywords=cached_keywords_str,
        )

        # logger.debug(f"prompt: {prompt}")

        response = await self.summary_model.generate_response(prompt)

        # logger.debug(f"response: {response}")

        # 只取response的第一个元素（字符串）
        response_str = response[0]
        keywords = list(get_keywords_from_json(response_str))

        # 更新关键词缓存
        if keywords:
            # 限制缓存大小，最多保留10个关键词
            if len(self.cached_keywords) > 10:
                # 转换为列表，移除最早的关键词
                cached_list = list(self.cached_keywords)
                self.cached_keywords = set(cached_list[-8:])

            # 添加新的关键词到缓存
            self.cached_keywords.update(keywords)
            logger.debug(f"当前激活的记忆关键词: {self.cached_keywords}")

        # 调用记忆系统获取相关记忆
        related_memory = await HippocampusManager.get_instance().get_memory_from_topic(
            valid_keywords=keywords, max_memory_num=3, max_memory_length=2, max_depth=3
        )
        # related_memory = await HippocampusManager.get_instance().get_memory_from_text(
        #     text=obs_info_text, max_memory_num=5, max_memory_length=2, max_depth=3, fast_retrieval=False
        # )

        # logger.debug(f"获取到的记忆: {related_memory}")

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
