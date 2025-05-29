from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.heart_flow.observation.observation import Observation
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
import time
import traceback
from src.common.logger_manager import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import chat_manager
from .base_processor import BaseProcessor
from src.chat.focus_chat.info.mind_info import MindInfo
from typing import List, Optional
from src.chat.heart_flow.observation.working_observation import WorkingMemoryObservation
from src.chat.focus_chat.working_memory.working_memory import WorkingMemory
from typing import Dict
from src.chat.focus_chat.info.info_base import InfoBase
from json_repair import repair_json
from src.chat.focus_chat.info.workingmemory_info import WorkingMemoryInfo
import asyncio
import json

logger = get_logger("processor")


def init_prompt():
    memory_proces_prompt = """
你的名字是{bot_name}

现在是{time_now}，你正在上网，和qq群里的网友们聊天，以下是正在进行的聊天内容：
{chat_observe_info}

以下是你已经总结的记忆摘要，你可以调取这些记忆查看内容来帮助你聊天，不要一次调取太多记忆，最多调取3个左右记忆：
{memory_str}

观察聊天内容和已经总结的记忆，思考是否有新内容需要总结成记忆，如果有，就输出 true，否则输出 false
如果当前聊天记录的内容已经被总结，千万不要总结新记忆，输出false
如果已经总结的记忆包含了当前聊天记录的内容，千万不要总结新记忆，输出false
如果已经总结的记忆摘要,包含了当前聊天记录的内容，千万不要总结新记忆，输出false

如果有相近的记忆，请合并记忆，输出merge_memory，格式为[["id1", "id2"], ["id3", "id4"],...]，你可以进行多组合并，但是每组合并只能有两个记忆id，不要输出其他内容

请根据聊天内容选择你需要调取的记忆并考虑是否添加新记忆，以JSON格式输出，格式如下：
```json
{{
    "selected_memory_ids": ["id1", "id2", ...],
    "new_memory": "true" or "false",
    "merge_memory": [["id1", "id2"], ["id3", "id4"],...]
    
}}
```
"""
    Prompt(memory_proces_prompt, "prompt_memory_proces")


class WorkingMemoryProcessor(BaseProcessor):
    log_prefix = "工作记忆"

    def __init__(self, subheartflow_id: str):
        super().__init__()

        self.subheartflow_id = subheartflow_id

        self.llm_model = LLMRequest(
            model=global_config.model.focus_chat_mind,
            temperature=global_config.model.focus_chat_mind["temp"],
            max_tokens=800,
            request_type="focus.processor.working_memory",
        )

        name = chat_manager.get_stream_name(self.subheartflow_id)
        self.log_prefix = f"[{name}] "

    async def process_info(
        self, observations: Optional[List[Observation]] = None, running_memorys: Optional[List[Dict]] = None, *infos
    ) -> List[InfoBase]:
        """处理信息对象

        Args:
            *infos: 可变数量的InfoBase类型的信息对象

        Returns:
            List[InfoBase]: 处理后的结构化信息列表
        """
        working_memory = None
        chat_info = ""
        try:
            for observation in observations:
                if isinstance(observation, WorkingMemoryObservation):
                    working_memory = observation.get_observe_info()
                    # working_memory_obs = observation
                if isinstance(observation, ChattingObservation):
                    chat_info = observation.get_observe_info()
                    # chat_info_truncate = observation.talking_message_str_truncate

            if not working_memory:
                logger.debug(f"{self.log_prefix} 没有找到工作记忆对象")
                mind_info = MindInfo()
                return [mind_info]
        except Exception as e:
            logger.error(f"{self.log_prefix} 处理观察时出错: {e}")
            logger.error(traceback.format_exc())
            return []

        all_memory = working_memory.get_all_memories()
        memory_prompts = []
        for memory in all_memory:
            # memory_content = memory.data
            memory_summary = memory.summary
            memory_id = memory.id
            memory_brief = memory_summary.get("brief")
            # memory_detailed = memory_summary.get("detailed")
            memory_keypoints = memory_summary.get("keypoints")
            memory_events = memory_summary.get("events")
            memory_single_prompt = f"记忆id:{memory_id},记忆摘要:{memory_brief}\n"
            memory_prompts.append(memory_single_prompt)

        memory_choose_str = "".join(memory_prompts)

        # 使用提示模板进行处理
        prompt = (await global_prompt_manager.get_prompt_async("prompt_memory_proces")).format(
            bot_name=global_config.bot.nickname,
            time_now=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            chat_observe_info=chat_info,
            memory_str=memory_choose_str,
        )

        # 调用LLM处理记忆
        content = ""
        try:
            # logger.debug(f"{self.log_prefix} 处理工作记忆的prompt: {prompt}")

            content, _ = await self.llm_model.generate_response_async(prompt=prompt)
            if not content:
                logger.warning(f"{self.log_prefix} LLM返回空结果，处理工作记忆失败。")
        except Exception as e:
            logger.error(f"{self.log_prefix} 执行LLM请求或处理响应时出错: {e}")
            logger.error(traceback.format_exc())

        # 解析LLM返回的JSON
        try:
            result = repair_json(content)
            if isinstance(result, str):
                result = json.loads(result)
            if not isinstance(result, dict):
                logger.error(f"{self.log_prefix} 解析LLM返回的JSON失败，结果不是字典类型: {type(result)}")
                return []

            selected_memory_ids = result.get("selected_memory_ids", [])
            new_memory = result.get("new_memory", "")
            merge_memory = result.get("merge_memory", [])
        except Exception as e:
            logger.error(f"{self.log_prefix} 解析LLM返回的JSON失败: {e}")
            logger.error(traceback.format_exc())
            return []

        logger.debug(f"{self.log_prefix} 解析LLM返回的JSON成功: {result}")

        # 根据selected_memory_ids，调取记忆
        memory_str = ""
        if selected_memory_ids:
            for memory_id in selected_memory_ids:
                memory = await working_memory.retrieve_memory(memory_id)
                if memory:
                    # memory_content = memory.data
                    memory_summary = memory.summary
                    memory_id = memory.id
                    memory_brief = memory_summary.get("brief")
                    # memory_detailed = memory_summary.get("detailed")
                    memory_keypoints = memory_summary.get("keypoints")
                    memory_events = memory_summary.get("events")
                    for keypoint in memory_keypoints:
                        memory_str += f"记忆要点:{keypoint}\n"
                    for event in memory_events:
                        memory_str += f"记忆事件:{event}\n"
                    # memory_str += f"记忆摘要:{memory_detailed}\n"
                    # memory_str += f"记忆主题:{memory_brief}\n"

        working_memory_info = WorkingMemoryInfo()
        if memory_str:
            working_memory_info.add_working_memory(memory_str)
            logger.debug(f"{self.log_prefix} 取得工作记忆: {memory_str}")
        else:
            logger.debug(f"{self.log_prefix} 没有找到工作记忆")

        # 根据聊天内容添加新记忆
        if new_memory:
            # 使用异步方式添加新记忆，不阻塞主流程
            logger.debug(f"{self.log_prefix} {new_memory}新记忆: ")
            asyncio.create_task(self.add_memory_async(working_memory, chat_info))

        if merge_memory:
            for merge_pairs in merge_memory:
                memory1 = await working_memory.retrieve_memory(merge_pairs[0])
                memory2 = await working_memory.retrieve_memory(merge_pairs[1])
                if memory1 and memory2:
                    memory_str = f"记忆id:{memory1.id},记忆摘要:{memory1.summary.get('brief')}\n"
                    memory_str += f"记忆id:{memory2.id},记忆摘要:{memory2.summary.get('brief')}\n"
                    asyncio.create_task(self.merge_memory_async(working_memory, merge_pairs[0], merge_pairs[1]))

        return [working_memory_info]

    async def add_memory_async(self, working_memory: WorkingMemory, content: str):
        """异步添加记忆，不阻塞主流程

        Args:
            working_memory: 工作记忆对象
            content: 记忆内容
        """
        try:
            await working_memory.add_memory(content=content, from_source="chat_text")
            logger.debug(f"{self.log_prefix} 异步添加新记忆成功: {content[:30]}...")
        except Exception as e:
            logger.error(f"{self.log_prefix} 异步添加新记忆失败: {e}")
            logger.error(traceback.format_exc())

    async def merge_memory_async(self, working_memory: WorkingMemory, memory_id1: str, memory_id2: str):
        """异步合并记忆，不阻塞主流程

        Args:
            working_memory: 工作记忆对象
            memory_str: 记忆内容
        """
        try:
            merged_memory = await working_memory.merge_memory(memory_id1, memory_id2)
            logger.debug(f"{self.log_prefix} 异步合并记忆成功: {memory_id1} 和 {memory_id2}...")
            logger.debug(f"{self.log_prefix} 合并后的记忆梗概: {merged_memory.summary.get('brief')}")
            logger.debug(f"{self.log_prefix} 合并后的记忆详情: {merged_memory.summary.get('detailed')}")
            logger.debug(f"{self.log_prefix} 合并后的记忆要点: {merged_memory.summary.get('keypoints')}")
            logger.debug(f"{self.log_prefix} 合并后的记忆事件: {merged_memory.summary.get('events')}")

        except Exception as e:
            logger.error(f"{self.log_prefix} 异步合并记忆失败: {e}")
            logger.error(traceback.format_exc())


init_prompt()
