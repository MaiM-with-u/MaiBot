import json
import time
import traceback
from typing import Dict, Any, Optional, Tuple
from rich.traceback import install
from datetime import datetime
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.common.logger import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.utils.chat_message_builder import (
    build_readable_actions,
    get_actions_by_timestamp_with_chat,
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
)
from src.chat.utils.utils import get_chat_type_and_target_info
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.plugin_system.base.component_types import ActionInfo, ChatMode, ComponentType
from src.plugin_system.core.component_registry import component_registry

logger = get_logger("planner")

install(extra_lines=3)


def init_prompt():
    Prompt(
        """
{time_block}
{identity_block}
你现在需要根据聊天内容，选择的合适的action来参与聊天。
{chat_context_description}，以下是具体的聊天内容
{chat_content_block}



{moderation_prompt}

现在请你根据{by_what}选择合适的action和触发action的消息:
{actions_before_now_block}

{no_action_block}
{action_options_text}

你必须从上面列出的可用action中选择一个，并说明触发action的消息id（不是消息原文）和选择该action的原因。

请根据动作示例，以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "planner_prompt",
    )

    Prompt(
        """
动作：{action_name}
动作描述：{action_description}
{action_require}
{{
    "action": "{action_name}",{action_parameters}{target_prompt}
    "reason":"触发action的原因"
}}
""",
        "action_prompt",
    )


class ActionPlanner:
    def __init__(self, chat_id: str, action_manager: ActionManager):
        self.chat_id = chat_id
        self.log_prefix = f"[{get_chat_manager().get_stream_name(chat_id) or chat_id}]"
        self.action_manager = action_manager
        # LLM规划器配置
        self.planner_llm = LLMRequest(
            model=global_config.model.planner,
            request_type="planner",  # 用于动作规划
        )

        self.last_obs_time_mark = 0.0

    def find_message_by_id(self, message_id: str, message_id_list: list) -> Optional[Dict[str, Any]]:
        # sourcery skip: use-next
        """
        根据message_id从message_id_list中查找对应的原始消息

        Args:
            message_id: 要查找的消息ID
            message_id_list: 消息ID列表，格式为[{'id': str, 'message': dict}, ...]

        Returns:
            找到的原始消息字典，如果未找到则返回None
        """
        for item in message_id_list:
            if item.get("id") == message_id:
                return item.get("message")
        return None

    async def plan(
        self, mode: ChatMode = ChatMode.FOCUS
    ) -> Tuple[Dict[str, Dict[str, Any] | str], Optional[Dict[str, Any]]]:
        """
        规划器 (Planner): 使用LLM根据上下文决定做出什么动作。
        """

        action = "no_reply"  # 默认动作
        reasoning = "规划器初始化默认"
        action_data = {}
        current_available_actions: Dict[str, ActionInfo] = {}
        target_message: Optional[Dict[str, Any]] = None  # 初始化target_message变量
        prompt: str = ""

        try:
            is_group_chat = True
            is_group_chat, chat_target_info = get_chat_type_and_target_info(self.chat_id)
            logger.debug(f"{self.log_prefix}获取到聊天信息 - 群聊: {is_group_chat}, 目标信息: {chat_target_info}")

            current_available_actions_dict = self.action_manager.get_using_actions()

            # 获取完整的动作信息
            all_registered_actions: Dict[str, ActionInfo] = component_registry.get_components_by_type(  # type: ignore
                ComponentType.ACTION
            )
            current_available_actions = {}
            for action_name in current_available_actions_dict:
                if action_name in all_registered_actions:
                    current_available_actions[action_name] = all_registered_actions[action_name]
                else:
                    logger.warning(f"{self.log_prefix}使用中的动作 {action_name} 未在已注册动作中找到")

            # --- 构建提示词 (调用修改后的 PromptBuilder 方法) ---
            prompt, message_id_list = await self.build_planner_prompt(
                is_group_chat=is_group_chat,  # <-- Pass HFC state
                chat_target_info=chat_target_info,  # <-- 传递获取到的聊天目标信息
                current_available_actions=current_available_actions,  # <-- Pass determined actions
                mode=mode,
            )

            # --- 调用 LLM (普通文本生成) ---
            llm_content = None
            try:
                llm_content, (reasoning_content, _) = await self.planner_llm.generate_response_async(prompt=prompt)

                if global_config.debug.show_prompt:
                    logger.info(f"{self.log_prefix}规划器原始提示词: {prompt}")
                    logger.info(f"{self.log_prefix}规划器原始响应: {llm_content}")
                    if reasoning_content:
                        logger.info(f"{self.log_prefix}规划器推理: {reasoning_content}")
                else:
                    logger.debug(f"{self.log_prefix}规划器原始提示词: {prompt}")
                    logger.debug(f"{self.log_prefix}规划器原始响应: {llm_content}")
                    if reasoning_content:
                        logger.debug(f"{self.log_prefix}规划器推理: {reasoning_content}")

            except Exception as req_e:
                logger.error(f"{self.log_prefix}LLM 请求执行失败: {req_e}")
                reasoning = f"LLM 请求失败，模型出现问题: {req_e}"
                action = "no_reply"

            if llm_content:
                try:
                    parsed_json = json.loads(repair_json(llm_content))

                    if isinstance(parsed_json, list):
                        if parsed_json:
                            parsed_json = parsed_json[-1]
                            logger.warning(f"{self.log_prefix}LLM返回了多个JSON对象，使用最后一个: {parsed_json}")
                        else:
                            parsed_json = {}

                    if not isinstance(parsed_json, dict):
                        logger.error(f"{self.log_prefix}解析后的JSON不是字典类型: {type(parsed_json)}")
                        parsed_json = {}

                    action = parsed_json.get("action", "no_reply")
                    reasoning = parsed_json.get("reasoning", "未提供原因")

                    # 将所有其他属性添加到action_data
                    for key, value in parsed_json.items():
                        if key not in ["action", "reasoning"]:
                            action_data[key] = value

                    # 在FOCUS模式下，非no_reply动作需要target_message_id
                    if mode == ChatMode.FOCUS and action != "no_reply":
                        if target_message_id := parsed_json.get("target_message_id"):
                            # 根据target_message_id查找原始消息
                            target_message = self.find_message_by_id(target_message_id, message_id_list)
                        else:
                            logger.warning(f"{self.log_prefix}FOCUS模式下动作'{action}'缺少target_message_id")

                    if action == "no_action":
                        reasoning = "normal决定不使用额外动作"
                    elif action != "no_reply" and action != "reply" and action not in current_available_actions:
                        logger.warning(
                            f"{self.log_prefix}LLM 返回了当前不可用或无效的动作: '{action}' (可用: {list(current_available_actions.keys())})，将强制使用 'no_reply'"
                        )
                        reasoning = f"LLM 返回了当前不可用的动作 '{action}' (可用: {list(current_available_actions.keys())})。原始理由: {reasoning}"
                        action = "no_reply"

                except Exception as json_e:
                    logger.warning(f"{self.log_prefix}解析LLM响应JSON失败 {json_e}. LLM原始输出: '{llm_content}'")
                    traceback.print_exc()
                    reasoning = f"解析LLM响应JSON失败: {json_e}. 将使用默认动作 'no_reply'."
                    action = "no_reply"

        except Exception as outer_e:
            logger.error(f"{self.log_prefix}Planner 处理过程中发生意外错误，规划失败，将执行 no_reply: {outer_e}")
            traceback.print_exc()
            action = "no_reply"
            reasoning = f"Planner 内部处理错误: {outer_e}"

        is_parallel = False
        if mode == ChatMode.NORMAL:
            if action in current_available_actions:
                is_parallel = current_available_actions[action].parallel_action

        action_result = {
            "action_type": action,
            "action_data": action_data,
            "reasoning": reasoning,
            "timestamp": time.time(),
            "is_parallel": is_parallel,
        }

        return (
            {
                "action_result": action_result,
                "action_prompt": prompt,
            },
            target_message,
        )

    async def build_planner_prompt(
        self,
        is_group_chat: bool,  # Now passed as argument
        chat_target_info: Optional[dict],  # Now passed as argument
        current_available_actions: Dict[str, ActionInfo],
        mode: ChatMode = ChatMode.FOCUS,
    ) -> tuple[str, list]:  # sourcery skip: use-join
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:
            message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp=time.time(),
                limit=int(global_config.chat.max_context_size * 0.6),
            )

            chat_content_block, message_id_list = build_readable_messages_with_id(
                messages=message_list_before_now,
                timestamp_mode="normal_no_YMD",
                read_mark=self.last_obs_time_mark,
                truncate=True,
                show_actions=True,
            )

            actions_before_now = get_actions_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=time.time()-3600,
                timestamp_end=time.time(),
                limit=5,
            )

            actions_before_now_block = build_readable_actions(
                actions=actions_before_now,
            )
            
            actions_before_now_block = f"你刚刚选择并执行过的action是：\n{actions_before_now_block}"

            self.last_obs_time_mark = time.time()

            if mode == ChatMode.FOCUS:
                mentioned_bonus = ""
                if global_config.chat.mentioned_bot_inevitable_reply:
                    mentioned_bonus = "\n- 有人提到你"
                if global_config.chat.at_bot_inevitable_reply:
                    mentioned_bonus = "\n- 有人提到你，或者at你"

                
                by_what = "聊天内容"
                target_prompt = '\n    "target_message_id":"触发action的消息id"'
                no_action_block = f"""重要说明：
- 'no_reply' 表示只进行不进行回复，等待合适的回复时机
- 当你刚刚发送了消息，没有人回复时，选择no_reply
- 当你一次发送了太多消息，为了避免打扰聊天节奏，选择no_reply

动作：reply
动作描述：参与聊天回复，发送文本进行表达
- 你想要闲聊或者随便附和{mentioned_bonus}
- 如果你刚刚进行了回复，不要对同一个话题重复回应
{{
    "action": "reply",
    "target_message_id":"触发action的消息id",
    "reason":"回复的原因"
}}

"""
            else:
                by_what = "聊天内容和用户的最新消息"
                target_prompt = ""
                no_action_block = """重要说明：
- 'reply' 表示只进行普通聊天回复，不执行任何额外动作
- 其他action表示在普通回复的基础上，执行相应的额外动作"""

            chat_context_description = "你现在正在一个群聊中"
            chat_target_name = None  # Only relevant for private
            if not is_group_chat and chat_target_info:
                chat_target_name = (
                    chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or "对方"
                )
                chat_context_description = f"你正在和 {chat_target_name} 私聊"

            action_options_block = ""

            for using_actions_name, using_actions_info in current_available_actions.items():
                if using_actions_info.action_parameters:
                    param_text = "\n"
                    for param_name, param_description in using_actions_info.action_parameters.items():
                        param_text += f'    "{param_name}":"{param_description}"\n'
                    param_text = param_text.rstrip("\n")
                else:
                    param_text = ""

                require_text = ""
                for require_item in using_actions_info.action_require:
                    require_text += f"- {require_item}\n"
                require_text = require_text.rstrip("\n")

                using_action_prompt = await global_prompt_manager.get_prompt_async("action_prompt")
                using_action_prompt = using_action_prompt.format(
                    action_name=using_actions_name,
                    action_description=using_actions_info.description,
                    action_parameters=param_text,
                    action_require=require_text,
                    target_prompt=target_prompt,
                )

                action_options_block += using_action_prompt

            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"

            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            bot_name = global_config.bot.nickname
            if global_config.bot.alias_names:
                bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
            else:
                bot_nickname = ""
            bot_core_personality = global_config.personality.personality_core
            identity_block = f"你的名字是{bot_name}{bot_nickname}，你{bot_core_personality}："

            planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")
            prompt = planner_prompt_template.format(
                time_block=time_block,
                by_what=by_what,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                actions_before_now_block=actions_before_now_block,
                no_action_block=no_action_block,
                action_options_text=action_options_block,
                moderation_prompt=moderation_prompt_block,
                identity_block=identity_block,
            )
            return prompt, message_id_list
        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错", []


init_prompt()
