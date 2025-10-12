import json
import time
import traceback
import random
import re
from typing import Dict, Optional, Tuple, List, TYPE_CHECKING, Union
from rich.traceback import install
from datetime import datetime
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.common.data_models.info_data_model import ActionPlannerInfo
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
from src.plugin_system.base.component_types import ActionInfo, ComponentType, ActionActivationType
from src.plugin_system.core.component_registry import component_registry

if TYPE_CHECKING:
    from src.common.data_models.info_data_model import TargetPersonInfo
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("planner")

install(extra_lines=3)


def init_prompt():
    Prompt(
        """
{time_block}
{name_block}
你的兴趣是：{interest}
{chat_context_description}，以下是具体的聊天内容
**聊天内容**
{chat_content_block}

**可选的action**
reply
动作描述：
1.你可以选择呼叫了你的名字，但是你没有做出回应的消息进行回复
2.你可以自然的顺着正在进行的聊天内容进行回复或自然的提出一个问题
3.不要回复你自己发送的消息
4.不要单独对表情包进行回复
{{
    "action": "reply",
    "target_message_id":"想要回复的消息id",
    "reason":"回复的原因"
}}

no_reply
动作描述：
保持沉默，不回复直到有新消息
控制聊天频率，不要太过频繁的发言
{{
    "action": "no_reply",
}}

no_reply_until_call
动作描述：
保持沉默，直到有人直接叫你的名字
当前话题不感兴趣时使用，或有人不喜欢你的发言时使用
{{
    "action": "no_reply_until_call",
}}

{action_options_text}

**你之前的action执行和思考记录**
{actions_before_now_block}

请选择**可选的**且符合使用条件的action，并说明触发action的消息id(消息id格式:m+数字)
不要回复你自己发送的消息
先输出你的选择思考理由，再输出你选择的action，理由是一段平文本，不要分点，精简。
**动作选择要求**
请你根据聊天内容,用户的最新消息和以下标准选择合适的动作:
{plan_style}
{moderation_prompt}

请选择所有符合使用要求的action，动作用json格式输出，如果输出多个json，每个json都要单独用```json包裹，你可以重复使用同一个动作或不同动作:
**示例**
// 理由文本
```json
{{
    "action":"动作名",
    "target_message_id":"触发动作的消息id",
    //对应参数
}}
```
```json
{{
    "action":"动作名",
    "target_message_id":"触发动作的消息id",
    //对应参数
}}
```""",
        "planner_prompt",
    )

    Prompt(
        """
{action_name}
动作描述：{action_description}
使用条件{parallel_text}：
{action_require}
{{
    "action": "{action_name}",{action_parameters},
    "target_message_id":"触发action的消息id",
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
            model_set=model_config.model_task_config.planner, request_type="planner"
        )  # 用于动作规划

        self.last_obs_time_mark = 0.0


        self.plan_log: List[Tuple[str, float, Union[List[ActionPlannerInfo], str]]] = []

    def find_message_by_id(
        self, message_id: str, message_id_list: List[Tuple[str, "DatabaseMessages"]]
    ) -> Optional["DatabaseMessages"]:
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
            if item[0] == message_id:
                return item[1]
        return None

    def _parse_single_action(
        self,
        action_json: dict,
        message_id_list: List[Tuple[str, "DatabaseMessages"]],
        current_available_actions: List[Tuple[str, ActionInfo]],
        extracted_reasoning: str = "",
    ) -> List[ActionPlannerInfo]:
        """解析单个action JSON并返回ActionPlannerInfo列表"""
        action_planner_infos = []

        try:
            action = action_json.get("action", "no_reply")
            reasoning = action_json.get("reason", "未提供原因")
            action_data = {key: value for key, value in action_json.items() if key not in ["action", "reason"]}
            # 非no_reply动作需要target_message_id
            target_message = None

            if target_message_id := action_json.get("target_message_id"):
                # 根据target_message_id查找原始消息
                target_message = self.find_message_by_id(target_message_id, message_id_list)
                if target_message is None:
                    logger.warning(f"{self.log_prefix}无法找到target_message_id '{target_message_id}' 对应的消息")
                    # 选择最新消息作为target_message
                    target_message = message_id_list[-1][1]
            else:
                target_message = message_id_list[-1][1]
                logger.debug(f"{self.log_prefix}动作'{action}'缺少target_message_id，使用最新消息作为target_message")

            # 验证action是否可用
            available_action_names = [action_name for action_name, _ in current_available_actions]
            internal_action_names = ["no_reply", "reply", "wait_time", "no_reply_until_call"]

            if action not in internal_action_names and action not in available_action_names:
                logger.warning(
                    f"{self.log_prefix}LLM 返回了当前不可用或无效的动作: '{action}' (可用: {available_action_names})，将强制使用 'no_reply'"
                )
                reasoning = (
                    f"LLM 返回了当前不可用的动作 '{action}' (可用: {available_action_names})。原始理由: {reasoning}"
                )
                action = "no_reply"

            # 创建ActionPlannerInfo对象
            # 将列表转换为字典格式
            available_actions_dict = dict(current_available_actions)
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type=action,
                    reasoning=reasoning,
                    action_data=action_data,
                    action_message=target_message,
                    available_actions=available_actions_dict,
                    action_reasoning=extracted_reasoning if extracted_reasoning else None,
                )
            )

        except Exception as e:
            logger.error(f"{self.log_prefix}解析单个action时出错: {e}")
            # 将列表转换为字典格式
            available_actions_dict = dict(current_available_actions)
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type="no_reply",
                    reasoning=f"解析单个action时出错: {e}",
                    action_data={},
                    action_message=None,
                    available_actions=available_actions_dict,
                    action_reasoning=extracted_reasoning if extracted_reasoning else None,
                )
            )

        return action_planner_infos

    async def plan(
        self,
        available_actions: Dict[str, ActionInfo],
        loop_start_time: float = 0.0,
    ) -> List[ActionPlannerInfo]:
        # sourcery skip: use-named-expression
        """
        规划器 (Planner): 使用LLM根据上下文决定做出什么动作。
        """

        # 获取聊天上下文
        message_list_before_now = get_raw_msg_before_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp=time.time(),
            limit=int(global_config.chat.max_context_size * 0.6),
        )
        message_id_list: list[Tuple[str, "DatabaseMessages"]] = []
        chat_content_block, message_id_list = build_readable_messages_with_id(
            messages=message_list_before_now,
            timestamp_mode="normal_no_YMD",
            read_mark=self.last_obs_time_mark,
            truncate=True,
            show_actions=True,
        )

        message_list_before_now_short = message_list_before_now[-int(global_config.chat.max_context_size * 0.3) :]
        chat_content_block_short, message_id_list_short = build_readable_messages_with_id(
            messages=message_list_before_now_short,
            timestamp_mode="normal_no_YMD",
            truncate=False,
            show_actions=False,
        )

        self.last_obs_time_mark = time.time()

        # 获取必要信息
        is_group_chat, chat_target_info, current_available_actions = self.get_necessary_info()

        # 应用激活类型过滤
        filtered_actions = self._filter_actions_by_activation_type(available_actions, chat_content_block_short)

        logger.debug(f"{self.log_prefix}过滤后有{len(filtered_actions)}个可用动作")

        # 构建包含所有动作的提示词
        prompt, message_id_list = await self.build_planner_prompt(
            is_group_chat=is_group_chat,
            chat_target_info=chat_target_info,
            current_available_actions=filtered_actions,
            chat_content_block=chat_content_block,
            message_id_list=message_id_list,
            interest=global_config.personality.interest,
        )

        # 调用LLM获取决策
        reasoning, actions = await self._execute_main_planner(
            prompt=prompt,
            message_id_list=message_id_list,
            filtered_actions=filtered_actions,
            available_actions=available_actions,
            loop_start_time=loop_start_time,
        )

        logger.info(f"{self.log_prefix}Planner:{reasoning}。选择了{len(actions)}个动作: {' '.join([a.action_type for a in actions])}")

        self.add_plan_log(reasoning, actions)

        return actions

    def add_plan_log(self, reasoning: str, actions: List[ActionPlannerInfo]):
        self.plan_log.append((reasoning, time.time(), actions))
        if len(self.plan_log) > 20:
            self.plan_log.pop(0)
    
    def add_plan_excute_log(self, result: str):
        self.plan_log.append(("", time.time(), result))
        if len(self.plan_log) > 20:
            self.plan_log.pop(0)

    def get_plan_log_str(self) -> str:
        plan_log_str = ""
        for reasoning, time, content in self.plan_log:
            if isinstance(content, list) and all(isinstance(action, ActionPlannerInfo) for action in content):
                time = datetime.fromtimestamp(time).strftime("%H:%M:%S")
                plan_log_str += f"{time}:{reasoning}|你使用了{','.join([action.action_type for action in content])}\n"
            else:
                time = datetime.fromtimestamp(time).strftime("%H:%M:%S")
                plan_log_str += f"{time}:{content}\n"
                
        return plan_log_str


    async def build_planner_prompt(
        self,
        is_group_chat: bool,
        chat_target_info: Optional["TargetPersonInfo"],
        current_available_actions: Dict[str, ActionInfo],
        message_id_list: List[Tuple[str, "DatabaseMessages"]],
        chat_content_block: str = "",
        interest: str = "",
    ) -> tuple[str, List[Tuple[str, "DatabaseMessages"]]]:
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:

            actions_before_now_block=self.get_plan_log_str()

            # 构建聊天上下文描述
            chat_context_description = "你现在正在一个群聊中"

            # 构建动作选项块
            action_options_block = await self._build_action_options_block(current_available_actions)

            # 其他信息
            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"
            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            bot_name = global_config.bot.nickname
            bot_nickname = (
                f",也有人叫你{','.join(global_config.bot.alias_names)}" if global_config.bot.alias_names else ""
            )
            name_block = f"你的名字是{bot_name}{bot_nickname}，请注意哪些是你自己的发言。"

            # 获取主规划器模板并填充
            planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")
            prompt = planner_prompt_template.format(
                time_block=time_block,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                actions_before_now_block=actions_before_now_block,
                action_options_text=action_options_block,
                moderation_prompt=moderation_prompt_block,
                name_block=name_block,
                interest=interest,
                plan_style=global_config.personality.plan_style,
            )

            return prompt, message_id_list
        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错", []

    def get_necessary_info(self) -> Tuple[bool, Optional["TargetPersonInfo"], Dict[str, ActionInfo]]:
        """
        获取 Planner 需要的必要信息
        """
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

        return is_group_chat, chat_target_info, current_available_actions

    def _filter_actions_by_activation_type(
        self, available_actions: Dict[str, ActionInfo], chat_content_block: str
    ) -> Dict[str, ActionInfo]:
        """根据激活类型过滤动作"""
        filtered_actions = {}

        for action_name, action_info in available_actions.items():
            if action_info.activation_type == ActionActivationType.NEVER:
                logger.debug(f"{self.log_prefix}动作 {action_name} 设置为 NEVER 激活类型，跳过")
                continue
            elif action_info.activation_type in [ActionActivationType.LLM_JUDGE, ActionActivationType.ALWAYS]:
                filtered_actions[action_name] = action_info
            elif action_info.activation_type == ActionActivationType.RANDOM:
                if random.random() < action_info.random_activation_probability:
                    filtered_actions[action_name] = action_info
            elif action_info.activation_type == ActionActivationType.KEYWORD:
                if action_info.activation_keywords:
                    for keyword in action_info.activation_keywords:
                        if keyword in chat_content_block:
                            filtered_actions[action_name] = action_info
                            break
            else:
                logger.warning(f"{self.log_prefix}未知的激活类型: {action_info.activation_type}，跳过处理")

        return filtered_actions

    async def _build_action_options_block(self, current_available_actions: Dict[str, ActionInfo]) -> str:
        # sourcery skip: use-join
        """构建动作选项块"""
        if not current_available_actions:
            return ""

        action_options_block = ""
        for action_name, action_info in current_available_actions.items():
            # 构建参数文本
            param_text = ""
            if action_info.action_parameters:
                param_text = "\n"
                for param_name, param_description in action_info.action_parameters.items():
                    param_text += f'    "{param_name}":"{param_description}"\n'
                param_text = param_text.rstrip("\n")

            # 构建要求文本
            require_text = ""
            for require_item in action_info.action_require:
                require_text += f"- {require_item}\n"
            require_text = require_text.rstrip("\n")
            
            if not action_info.parallel_action:
                parallel_text = "(当选择这个动作时，请不要选择其他动作)"
            else:
                parallel_text = ""

            # 获取动作提示模板并填充
            using_action_prompt = await global_prompt_manager.get_prompt_async("action_prompt")
            using_action_prompt = using_action_prompt.format(
                action_name=action_name,
                action_description=action_info.description,
                action_parameters=param_text,
                action_require=require_text,
                parallel_text=parallel_text,
            )

            action_options_block += using_action_prompt

        return action_options_block

    async def _execute_main_planner(
        self,
        prompt: str,
        message_id_list: List[Tuple[str, "DatabaseMessages"]],
        filtered_actions: Dict[str, ActionInfo],
        available_actions: Dict[str, ActionInfo],
        loop_start_time: float,
    ) -> Tuple[str,List[ActionPlannerInfo]]:
        """执行主规划器"""
        llm_content = None
        actions: List[ActionPlannerInfo] = []

        try:
            # 调用LLM
            llm_content, (reasoning_content, _, _) = await self.planner_llm.generate_response_async(prompt=prompt)

            # logger.info(f"{self.log_prefix}规划器原始提示词: {prompt}")
            # logger.info(f"{self.log_prefix}规划器原始响应: {llm_content}")

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
            return f"LLM 请求失败，模型出现问题: {req_e}",[
                ActionPlannerInfo(
                    action_type="no_reply",
                    reasoning=f"LLM 请求失败，模型出现问题: {req_e}",
                    action_data={},
                    action_message=None,
                    available_actions=available_actions,
                )
            ]

        # 解析LLM响应
        extracted_reasoning = ""
        if llm_content:
            try:
                json_objects, extracted_reasoning = self._extract_json_from_markdown(llm_content)
                if json_objects:
                    logger.debug(f"{self.log_prefix}从响应中提取到{len(json_objects)}个JSON对象")
                    filtered_actions_list = list(filtered_actions.items())
                    for json_obj in json_objects:
                        actions.extend(self._parse_single_action(json_obj, message_id_list, filtered_actions_list, extracted_reasoning))
                else:
                    # 尝试解析为直接的JSON
                    logger.warning(f"{self.log_prefix}LLM没有返回可用动作: {llm_content}")
                    extracted_reasoning = "LLM没有返回可用动作"
                    actions = self._create_no_reply("LLM没有返回可用动作", available_actions)

            except Exception as json_e:
                logger.warning(f"{self.log_prefix}解析LLM响应JSON失败 {json_e}. LLM原始输出: '{llm_content}'")
                extracted_reasoning = f"解析LLM响应JSON失败: {json_e}"
                actions = self._create_no_reply(f"解析LLM响应JSON失败: {json_e}", available_actions)
                traceback.print_exc()
        else:
            extracted_reasoning = "规划器没有获得LLM响应"
            actions = self._create_no_reply("规划器没有获得LLM响应", available_actions)

        # 添加循环开始时间到所有非no_reply动作
        for action in actions:
            action.action_data = action.action_data or {}
            action.action_data["loop_start_time"] = loop_start_time

        logger.debug(f"{self.log_prefix}规划器选择了{len(actions)}个动作: {' '.join([a.action_type for a in actions])}")

        return extracted_reasoning,actions

    def _create_no_reply(self, reasoning: str, available_actions: Dict[str, ActionInfo]) -> List[ActionPlannerInfo]:
        """创建no_reply"""
        return [
            ActionPlannerInfo(
                action_type="no_reply",
                reasoning=reasoning,
                action_data={},
                action_message=None,
                available_actions=available_actions,
            )
        ]

    def _extract_json_from_markdown(self, content: str) -> Tuple[List[dict], str]:
        # sourcery skip: for-append-to-extend
        """从Markdown格式的内容中提取JSON对象和推理内容"""
        json_objects = []
        reasoning_content = ""

        # 使用正则表达式查找```json包裹的JSON内容
        json_pattern = r"```json\s*(.*?)\s*```"
        matches = re.findall(json_pattern, content, re.DOTALL)

        # 提取JSON之前的内容作为推理文本
        if matches:
            # 找到第一个```json的位置
            first_json_pos = content.find("```json")
            if first_json_pos > 0:
                reasoning_content = content[:first_json_pos].strip()
                # 清理推理内容中的注释标记
                reasoning_content = re.sub(r"^//\s*", "", reasoning_content, flags=re.MULTILINE)
                reasoning_content = reasoning_content.strip()

        for match in matches:
            try:
                # 清理可能的注释和格式问题
                json_str = re.sub(r"//.*?\n", "\n", match)  # 移除单行注释
                json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)  # 移除多行注释
                if json_str := json_str.strip():
                    json_obj = json.loads(repair_json(json_str))
                    if isinstance(json_obj, dict):
                        json_objects.append(json_obj)
                    elif isinstance(json_obj, list):
                        for item in json_obj:
                            if isinstance(item, dict):
                                json_objects.append(item)
            except Exception as e:
                logger.warning(f"解析JSON块失败: {e}, 块内容: {match[:100]}...")
                continue

        return json_objects, reasoning_content


init_prompt()
