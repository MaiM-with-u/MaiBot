import time
import json  # <--- 确保导入 json
import traceback
from typing import List, Dict, Any, Optional
from rich.traceback import install
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.focus_chat.heartflow_prompt_builder import prompt_builder
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info.obs_info import ObsInfo
from src.chat.focus_chat.info.cycle_info import CycleInfo
from src.chat.focus_chat.info.mind_info import MindInfo
from src.chat.focus_chat.info.structured_info import StructuredInfo
from src.common.logger_manager import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.individuality.individuality import Individuality
from src.chat.focus_chat.planners.action_factory import ActionManager
from src.chat.focus_chat.planners.action_factory import ActionInfo
from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat
from src.plugins.group_nickname.nickname_manager import nickname_manager

logger = get_logger("planner")

install(extra_lines=3)

def init_prompt():
    Prompt(
        """你的名字是{bot_name},{prompt_personality}，{chat_context_description}。需要基于以下信息决定如何参与对话：
{nickname_info_block}
{chat_content_block}
{mind_info_block}
{cycle_info_block}

请综合分析聊天内容和你看到的新消息，参考聊天规划，选择合适的action:

{action_options_text}

你必须从上面列出的可用action中选择一个，并说明原因。
你的决策必须以严格的 JSON 格式输出，且仅包含 JSON 内容，不要有任何其他文字或解释。

请你以下面格式输出你选择的action：
{{
    "action": "action_name",
    "reasoning": "你的决策理由",
    "参数1": "参数1的值",
    "参数2": "参数2的值",
    "参数3": "参数3的值",
    ...
}}

请输出你的决策 JSON：""",
"planner_prompt",)
    
    Prompt(
        """
action_name: {action_name}
    描述：{action_description}
    参数：
    {action_parameters}
    动作要求：
    {action_require}
        """,
        "action_prompt",
    )
    

class ActionPlanner:
    def __init__(self, log_prefix: str, action_manager: ActionManager, stream_id: str, chat_stream: ChatStream):
        self.log_prefix = log_prefix
        # LLM规划器配置
        self.planner_llm = LLMRequest(
            model=global_config.llm_plan,
            max_tokens=1000,
            request_type="action_planning",  # 用于动作规划
        )
        self.action_manager = action_manager
        self.stream_id = stream_id
        self.chat_stream = chat_stream

    async def plan(self, all_plan_info: List[InfoBase], cycle_timers: dict) -> Dict[str, Any]:
        """
        规划器 (Planner): 使用LLM根据上下文决定做出什么动作。

        参数:
            all_plan_info: 所有计划信息
            cycle_timers: 计时器字典
        """

        action = "no_reply"  # 默认动作
        reasoning = "规划器初始化默认"

        try:
            # 获取观察信息
            for info in all_plan_info:
                if isinstance(info, ObsInfo):
                    logger.debug(f"{self.log_prefix} 观察信息: {info}")
                    observed_messages = info.get_talking_message()
                    observed_messages_str = info.get_talking_message_str_truncate()
                    chat_type = info.get_chat_type()
                    if chat_type == "group":
                        is_group_chat = True
                    else:
                        is_group_chat = False
                elif isinstance(info, MindInfo):
                    logger.debug(f"{self.log_prefix} 思维信息: {info}")
                    current_mind = info.get_current_mind()
                elif isinstance(info, CycleInfo):
                    logger.debug(f"{self.log_prefix} 循环信息: {info}")
                    cycle_info = info.get_observe_info()
                elif isinstance(info, StructuredInfo):
                    logger.debug(f"{self.log_prefix} 结构化信息: {info}")
                    structured_info = info.get_data()

            current_available_actions = self.action_manager.get_using_actions()
            
            # --- 构建提示词 (调用修改后的 PromptBuilder 方法) ---
            prompt = await self.build_planner_prompt(
                is_group_chat=is_group_chat,  # <-- Pass HFC state
                chat_target_info=None,
                observed_messages_str=observed_messages_str,  # <-- Pass local variable
                current_mind=current_mind,  # <-- Pass argument
                # structured_info=structured_info,  # <-- Pass SubMind info
                current_available_actions=current_available_actions,  # <-- Pass determined actions
                cycle_info=cycle_info,  # <-- Pass cycle info
            )

            # --- 调用 LLM (普通文本生成) ---
            llm_content = None
            try:
                llm_content, _, _ = await self.planner_llm.generate_response(prompt=prompt)
                logger.debug(f"{self.log_prefix}[Planner] LLM 原始 JSON 响应 (预期): {llm_content}")
            except Exception as req_e:
                logger.error(f"{self.log_prefix}[Planner] LLM 请求执行失败: {req_e}")
                reasoning = f"LLM 请求失败，你的模型出现问题: {req_e}"
                action = "no_reply"

            if llm_content:
                try:
                    # 尝试去除可能的 markdown 代码块标记
                    cleaned_content = (
                        llm_content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    )
                    if not cleaned_content:
                        raise json.JSONDecodeError("Cleaned content is empty", cleaned_content, 0)
                    parsed_json = json.loads(cleaned_content)

                    # 提取决策，提供默认值
                    extracted_action = parsed_json.get("action", "no_reply")
                    extracted_reasoning = parsed_json.get("reasoning", "LLM未提供理由")

                    # 新的reply格式
                    if extracted_action == "reply":
                        action_data = {
                            "text": parsed_json.get("text", []),
                            "emojis": parsed_json.get("emojis", []),
                            "target": parsed_json.get("target", ""),
                        }
                    else:
                        action_data = {}  # 其他动作可能不需要额外数据

                    if extracted_action not in current_available_actions:
                        logger.warning(
                            f"{self.log_prefix}LLM 返回了当前不可用或无效的动作: '{extracted_action}' (可用: {list(current_available_actions.keys())})，将强制使用 'no_reply'"
                        )
                        action = "no_reply"
                        reasoning = f"LLM 返回了当前不可用的动作 '{extracted_action}' (可用: {list(current_available_actions.keys())})。原始理由: {extracted_reasoning}"
                    else:
                        # 动作有效且可用
                        action = extracted_action
                        reasoning = extracted_reasoning

                except Exception as json_e:
                    logger.warning(
                        f"{self.log_prefix}解析LLM响应JSON失败，模型返回不标准: {json_e}. LLM原始输出: '{llm_content}'"
                    )
                    reasoning = f"解析LLM响应JSON失败: {json_e}. 将使用默认动作 'no_reply'."
                    action = "no_reply"

        except Exception as outer_e:
            logger.error(f"{self.log_prefix}Planner 处理过程中发生意外错误，规划失败，将执行 no_reply: {outer_e}")
            traceback.print_exc()
            action = "no_reply"  # 发生未知错误，标记为 error 动作
            reasoning = f"Planner 内部处理错误: {outer_e}"

        logger.debug(
            f"{self.log_prefix}规划器Prompt:\n{prompt}\n\n决策动作:{action},\n动作信息: '{action_data}'\n理由: {reasoning}"
        )

        # 恢复原始动作集
        self.action_manager.restore_actions()
        logger.debug(
            f"{self.log_prefix}恢复了原始动作集, 当前可用: {list(self.action_manager.get_using_actions().keys())}"
        )

        action_result = {"action_type": action, "action_data": action_data, "reasoning": reasoning}

        plan_result = {
            "action_result": action_result,
            "current_mind": current_mind,
            "observed_messages": observed_messages,
        }

        # 返回结果字典
        return plan_result

    
    async def build_planner_prompt(
        self,
        is_group_chat: bool,  # Now passed as argument
        chat_target_info: Optional[dict],  # Now passed as argument
        observed_messages_str: str,
        current_mind: Optional[str],
        current_available_actions: Dict[str, ActionInfo],
        cycle_info: Optional[str],
    ) -> str:
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:
            # --- Determine chat context ---
            chat_context_description = "你现在正在一个群聊中"
            chat_target_name = None  # Only relevant for private
            if not is_group_chat and chat_target_info:
                chat_target_name = (
                    chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or "对方"
                )
                chat_context_description = f"你正在和 {chat_target_name} 私聊"


            chat_content_block = ""
            if observed_messages_str:
                chat_content_block = f"聊天记录：\n{observed_messages_str}"
            else:
                chat_content_block = "你还未开始聊天"

            mind_info_block = ""
            if current_mind:
                mind_info_block = f"对聊天的规划：{current_mind}"
            else:
                mind_info_block = "你刚参与聊天"

            individuality = Individuality.get_instance()
            personality_block = individuality.get_prompt(x_person=2, level=2)


            action_options_block = ""
            for using_actions_name, using_actions_info in current_available_actions.items():
                # print(using_actions_name)
                # print(using_actions_info)
                # print(using_actions_info["parameters"])
                # print(using_actions_info["require"])
                # print(using_actions_info["description"])

                using_action_prompt = await global_prompt_manager.get_prompt_async("action_prompt")
                
                param_text = ""
                for param_name, param_description in using_actions_info["parameters"].items():
                    param_text += f"{param_name}: {param_description}\n"
                
                require_text = ""
                for require_item in using_actions_info["require"]:
                    require_text += f"- {require_item}\n"
                
                using_action_prompt = using_action_prompt.format(
                    action_name=using_actions_name,
                    action_description=using_actions_info["description"],
                    action_parameters=param_text,
                    action_require=require_text,
                )
                
                action_options_block += using_action_prompt

            # 需要获取用于上下文的历史消息
            message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                chat_id=self.stream_id,
                timestamp=time.time(),  # 使用当前时间作为参考点
                limit=global_config.observation_context_size,  # 使用与 prompt 构建一致的 limit
            )
            # 调用工具函数获取格式化后的绰号字符串
            nickname_injection_str = await nickname_manager.get_nickname_prompt_injection(
                self.chat_stream, message_list_before_now
            )

            
            planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")
            prompt = planner_prompt_template.format(
                bot_name=global_config.BOT_NICKNAME,
                prompt_personality=personality_block,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                mind_info_block=mind_info_block,
                cycle_info_block=cycle_info,
                action_options_text=action_options_block,
                nickname_info_block=nickname_injection_str,
            )
            return prompt

        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错"


init_prompt()
