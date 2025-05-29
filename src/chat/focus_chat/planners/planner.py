import json  # <--- 确保导入 json
import traceback
from typing import List, Dict, Any, Optional
from rich.traceback import install
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info.obs_info import ObsInfo
from src.chat.focus_chat.info.cycle_info import CycleInfo
from src.chat.focus_chat.info.mind_info import MindInfo
from src.chat.focus_chat.info.action_info import ActionInfo
from src.chat.focus_chat.info.structured_info import StructuredInfo
from src.chat.focus_chat.info.self_info import SelfInfo
from src.common.logger_manager import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.individuality.individuality import individuality
from src.chat.focus_chat.planners.action_manager import ActionManager

logger = get_logger("planner")

install(extra_lines=3)


def init_prompt():
    Prompt(
        """
你的自我认知是：
{self_info_block}

{extra_info_block}

你需要基于以下信息决定如何参与对话
这些信息可能会有冲突，请你整合这些信息，并选择一个最合适的action：
{chat_content_block}

{mind_info_block}
{cycle_info_block}

请综合分析聊天内容和你看到的新消息，参考聊天规划，选择合适的action:
注意，除了下面动作选项之外，你在群聊里不能做其他任何事情，这是你能力的边界，现在请你选择合适的action:

{action_options_text}

你必须从上面列出的可用action中选择一个，并说明原因。
你的决策必须以严格的 JSON 格式输出，且仅包含 JSON 内容，不要有任何其他文字或解释。

{moderation_prompt}

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
        "planner_prompt",
    )

    Prompt(
        """
action_name: {action_name}
    描述：{action_description}
    参数：
{action_parameters}
    动作要求：
{action_require}""",
        "action_prompt",
    )


class ActionPlanner:
    def __init__(self, log_prefix: str, action_manager: ActionManager):
        self.log_prefix = log_prefix
        # LLM规划器配置
        self.planner_llm = LLMRequest(
            model=global_config.model.focus_planner,
            max_tokens=1000,
            request_type="focus_planner",  # 用于动作规划
        )

        self.action_manager = action_manager

    async def plan(self, all_plan_info: List[InfoBase], cycle_timers: dict) -> Dict[str, Any]:
        """
        规划器 (Planner): 使用LLM根据上下文决定做出什么动作。

        参数:
            all_plan_info: 所有计划信息
            cycle_timers: 计时器字典
        """

        action = "no_reply"  # 默认动作
        reasoning = "规划器初始化默认"
        action_data = {}

        try:
            # 获取观察信息
            extra_info: list[str] = []

            # 首先处理动作变更
            for info in all_plan_info:
                if isinstance(info, ActionInfo) and info.has_changes():
                    add_actions = info.get_add_actions()
                    remove_actions = info.get_remove_actions()
                    reason = info.get_reason()
                    print(f"{self.log_prefix} 动作变更: {add_actions} {remove_actions} {reason}")

                    # 处理动作的增加
                    for action_name in add_actions:
                        if action_name in self.action_manager.get_registered_actions():
                            self.action_manager.add_action_to_using(action_name)
                            logger.debug(f"{self.log_prefix}添加动作: {action_name}, 原因: {reason}")

                    # 处理动作的移除
                    for action_name in remove_actions:
                        self.action_manager.remove_action_from_using(action_name)
                        logger.debug(f"{self.log_prefix}移除动作: {action_name}, 原因: {reason}")

                    # 如果当前选择的动作被移除了，更新为no_reply
                    if action in remove_actions:
                        action = "no_reply"
                        reasoning = f"之前选择的动作{action}已被移除，原因: {reason}"

            # 继续处理其他信息
            self_info = ""
            current_mind = ""
            cycle_info = ""
            structured_info = ""
            is_group_chat = True
            for info in all_plan_info:
                if isinstance(info, ObsInfo):
                    observed_messages = info.get_talking_message()
                    observed_messages_str = info.get_talking_message_str_truncate()
                    chat_type = info.get_chat_type()
                    is_group_chat = chat_type == "group"
                elif isinstance(info, MindInfo):
                    current_mind = info.get_current_mind()
                elif isinstance(info, CycleInfo):
                    cycle_info = info.get_observe_info()
                elif isinstance(info, SelfInfo):
                    self_info = info.get_processed_info()
                elif isinstance(info, StructuredInfo):
                    structured_info = info.get_processed_info()
                    # print(f"structured_info: {structured_info}")
                elif not isinstance(info, ActionInfo):  # 跳过已处理的ActionInfo
                    extra_info.append(info.get_processed_info())

            # 获取当前可用的动作
            current_available_actions = self.action_manager.get_using_actions()

            # 如果没有可用动作或只有no_reply动作，直接返回no_reply
            if not current_available_actions or (
                len(current_available_actions) == 1 and "no_reply" in current_available_actions
            ):
                action = "no_reply"
                reasoning = "没有可用的动作" if not current_available_actions else "只有no_reply动作可用，跳过规划"
                logger.info(f"{self.log_prefix}{reasoning}")
                self.action_manager.restore_actions()
                logger.debug(
                    f"{self.log_prefix}沉默后恢复到默认动作集, 当前可用: {list(self.action_manager.get_using_actions().keys())}"
                )
                return {
                    "action_result": {"action_type": action, "action_data": action_data, "reasoning": reasoning},
                    "current_mind": current_mind,
                    "observed_messages": observed_messages,
                }

            # --- 构建提示词 (调用修改后的 PromptBuilder 方法) ---
            prompt = await self.build_planner_prompt(
                self_info_block=self_info,
                is_group_chat=is_group_chat,  # <-- Pass HFC state
                chat_target_info=None,
                observed_messages_str=observed_messages_str,  # <-- Pass local variable
                current_mind=current_mind,  # <-- Pass argument
                structured_info=structured_info,  # <-- Pass SubMind info
                current_available_actions=current_available_actions,  # <-- Pass determined actions
                cycle_info=cycle_info,  # <-- Pass cycle info
                extra_info=extra_info,
            )

            # --- 调用 LLM (普通文本生成) ---
            llm_content = None
            try:
                llm_content, reasoning_content, _ = await self.planner_llm.generate_response(prompt=prompt)
                logger.debug(f"{self.log_prefix}[Planner] LLM 原始 JSON 响应 (预期): {llm_content}")
                logger.debug(f"{self.log_prefix}[Planner] LLM 原始理由 响应 (预期): {reasoning_content}")
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

                    # 将所有其他属性添加到action_data
                    action_data = {}
                    for key, value in parsed_json.items():
                        if key not in ["action", "reasoning"]:
                            action_data[key] = value

                    # 对于reply动作不需要额外处理，因为相关字段已经在上面的循环中添加到action_data

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
            action = "no_reply"
            reasoning = f"Planner 内部处理错误: {outer_e}"

        logger.debug(
            f"{self.log_prefix}规划器Prompt:\n{prompt}\n\n决策动作:{action},\n动作信息: '{action_data}'\n理由: {reasoning}"
        )

        # 恢复到默认动作集
        self.action_manager.restore_actions()
        logger.debug(
            f"{self.log_prefix}规划后恢复到默认动作集, 当前可用: {list(self.action_manager.get_using_actions().keys())}"
        )

        action_result = {"action_type": action, "action_data": action_data, "reasoning": reasoning}

        plan_result = {
            "action_result": action_result,
            "current_mind": current_mind,
            "observed_messages": observed_messages,
        }

        return plan_result

    async def build_planner_prompt(
        self,
        self_info_block: str,
        is_group_chat: bool,  # Now passed as argument
        chat_target_info: Optional[dict],  # Now passed as argument
        observed_messages_str: str,
        current_mind: Optional[str],
        structured_info: Optional[str],
        current_available_actions: Dict[str, ActionInfo],
        cycle_info: Optional[str],
        extra_info: list[str],
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
                    param_text += f"    {param_name}: {param_description}\n"

                require_text = ""
                for require_item in using_actions_info["require"]:
                    require_text += f"  - {require_item}\n"

                using_action_prompt = using_action_prompt.format(
                    action_name=using_actions_name,
                    action_description=using_actions_info["description"],
                    action_parameters=param_text,
                    action_require=require_text,
                )

                action_options_block += using_action_prompt

            extra_info_block = "\n".join(extra_info)
            extra_info_block += f"\n{structured_info}"
            if extra_info or structured_info:
                extra_info_block = f"以下是一些额外的信息，现在请你阅读以下内容，进行决策\n{extra_info_block}\n以上是一些额外的信息，现在请你阅读以下内容，进行决策"
            else:
                extra_info_block = ""

            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"

            planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")
            prompt = planner_prompt_template.format(
                self_info_block=self_info_block,
                # bot_name=global_config.bot.nickname,
                prompt_personality=personality_block,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                mind_info_block=mind_info_block,
                cycle_info_block=cycle_info,
                action_options_text=action_options_block,
                # action_available_block=action_available_block,
                extra_info_block=extra_info_block,
                moderation_prompt=moderation_prompt_block,
            )
            return prompt

        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错"


init_prompt()
