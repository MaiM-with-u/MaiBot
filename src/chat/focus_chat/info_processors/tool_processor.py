from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
import time
from src.common.logger_manager import get_logger
from src.individuality.individuality import individuality
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.tools.tool_use import ToolUser
from src.chat.utils.json_utils import process_llm_tool_calls
from src.person_info.relationship_manager import relationship_manager
from .base_processor import BaseProcessor
from typing import List, Optional, Dict
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.info.structured_info import StructuredInfo
from src.chat.focus_chat.info.info_base import InfoBase

logger = get_logger("processor")


def init_prompt():
    # ... 原有代码 ...

    # 添加工具执行器提示词
    tool_executor_prompt = """
You are a specialized tool execution assistant. Your name is {bot_name}. The current time is {time_now}.

Your current additional information:
{memory_str}

Current chat content in the group:
{chat_observe_info}

Please carefully analyze the chat content and consider the following points:
1. Does the content contain questions that require information queries?
2. Are there specific operations that need to be executed?
3. Are there explicit tool usage instructions?
4. Consider your relationship with the user and the current conversation atmosphere.

If you need to use tools, please directly call the corresponding tool functions. If no tools are needed, simply output "No tools needed".
"""
    Prompt(tool_executor_prompt, "tool_executor_prompt")


class ToolProcessor(BaseProcessor):
    log_prefix = "工具执行器"

    def __init__(self, subheartflow_id: str):
        super().__init__()
        self.subheartflow_id = subheartflow_id
        self.log_prefix = f"[{subheartflow_id}:ToolExecutor] "
        self.llm_model = LLMRequest(
            model=global_config.model.tool_use,
            max_tokens=500,
            request_type="tool_execution",
        )
        self.structured_info = []

    async def process_info(
        self, observations: Optional[List[Observation]] = None, running_memorys: Optional[List[Dict]] = None, *infos
    ) -> List[InfoBase]:
        """处理信息对象

        Args:
            *infos: 可变数量的InfoBase类型的信息对象

        Returns:
            list: 处理后的结构化信息列表
        """

        result_infos = []

        if observations:
            for observation in observations:
                if isinstance(observation, ChattingObservation):
                    # 执行工具调用
                    tool_results, used_tools, prompt = await self.execute_tools(observation, running_memorys)
                    
                    # 为每个工具调用结果创建StructuredInfo对象并返回
                    for tool_result in tool_results:
                        structured_info = StructuredInfo()
                        structured_info.set_info(tool_result.get("type"), tool_result.get("content"))
                        result_infos.append(structured_info)
                        logger.info(f"{self.log_prefix} 工具调用成功: {tool_result.get('type')} - {tool_result.get('content')}")
        
        logger.debug(f"result_infos: {result_infos}")
        return result_infos

    async def execute_tools(self, observation: ChattingObservation, running_memorys: Optional[List[Dict]] = None):
        """
        并行执行工具，返回结构化信息

        参数:
            sub_mind: 子思维对象
            chat_target_name: 聊天目标名称，默认为"对方"
            is_group_chat: 是否为群聊，默认为False
            return_details: 是否返回详细信息，默认为False
            cycle_info: 循环信息对象，可用于记录详细执行信息

        返回:
            如果return_details为False:
                List[Dict]: 工具执行结果的结构化信息列表
            如果return_details为True:
                Tuple[List[Dict], List[str], str]: (工具执行结果列表, 使用的工具列表, 工具执行提示词)
        """
        tool_instance = ToolUser()
        tools = tool_instance._define_tools()

        # logger.debug(f"observation: {observation}")
        # logger.debug(f"observation.chat_target_info: {observation.chat_target_info}")
        # logger.debug(f"observation.is_group_chat: {observation.is_group_chat}")
        # logger.debug(f"observation.person_list: {observation.person_list}")

        is_group_chat = observation.is_group_chat

        chat_observe_info = observation.get_observe_info()
        person_list = observation.person_list

        memory_str = ""
        if running_memorys:
            memory_str = "以下是当前在聊天中，你回忆起的记忆：\n"
            for running_memory in running_memorys:
                memory_str += f"{running_memory['topic']}: {running_memory['content']}\n"

        # 构建关系信息
        relation_prompt = "【关系信息】\n"
        for person in person_list:
            relation_prompt += await relationship_manager.build_relationship_info(person, is_id=True)

        # 获取个性信息

        # prompt_personality = individuality.get_prompt(x_person=2, level=2)

        # 获取时间信息
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # 构建专用于工具调用的提示词
        prompt = await global_prompt_manager.format_prompt(
            "tool_executor_prompt",
            memory_str=memory_str,
            # extra_info="extra_structured_info",
            chat_observe_info=chat_observe_info,
            # chat_target_name=chat_target_name,
            is_group_chat=is_group_chat,
            # relation_prompt=relation_prompt,
            # prompt_personality=prompt_personality,
            # mood_info=mood_info,
            bot_name=individuality.name,
            time_now=time_now,
        )

        # 调用LLM，专注于工具使用
        # logger.debug(f"开始执行工具调用{prompt}")
        response, _, tool_calls = await self.llm_model.generate_response_tool_async(prompt=prompt, tools=tools)

        logger.debug(f"获取到工具原始输出:\n{tool_calls}")
        # 处理工具调用和结果收集，类似于SubMind中的逻辑
        new_structured_items = []
        used_tools = []  # 记录使用了哪些工具

        if tool_calls:
            success, valid_tool_calls, error_msg = process_llm_tool_calls(tool_calls)
            if success and valid_tool_calls:
                for tool_call in valid_tool_calls:
                    try:
                        # 记录使用的工具名称
                        tool_name = tool_call.get("name", "unknown_tool")
                        used_tools.append(tool_name)

                        result = await tool_instance._execute_tool_call(tool_call)

                        name = result.get("type", "unknown_type")
                        content = result.get("content", "")

                        logger.info(f"工具{name}，获得信息:{content}")
                        if result:
                            new_item = {
                                "type": result.get("type", "unknown_type"),
                                "id": result.get("id", f"tool_exec_{time.time()}"),
                                "content": result.get("content", ""),
                                "ttl": 3,
                            }
                            new_structured_items.append(new_item)
                    except Exception as e:
                        logger.error(f"{self.log_prefix}工具执行失败: {e}")

        return new_structured_items, used_tools, prompt


init_prompt()
