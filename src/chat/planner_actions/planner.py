import json
import time
import traceback
import asyncio
from typing import Dict, Optional, Tuple, List, Any
from rich.traceback import install
from datetime import datetime
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.common.data_models.database_data_model import DatabaseMessages
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
from src.plugin_system.base.component_types import ActionInfo, ChatMode, ComponentType, ActionActivationType
from src.plugin_system.core.component_registry import component_registry
import random

logger = get_logger("planner")

install(extra_lines=3)


def init_prompt():
    Prompt(
        """
{time_block}
{name_block}
你现在需要根据聊天内容，选择的合适的action来参与聊天。
请你根据以下行事风格来决定action:
{plan_style}

{chat_context_description}，以下是具体的聊天内容
{chat_content_block}

{moderation_prompt}

现在请你根据聊天内容和用户的最新消息选择合适的action和触发action的消息:
{actions_before_now_block}

动作：no_action
动作描述：不进行动作，等待合适的时机
- 当你刚刚发送了消息，没有人回复时，选择no_action
- 当你一次发送了太多消息，为了避免过于烦人，可以不回复
{{
    "action": "no_action",
    "reason":"不动作的原因"
}}

动作：reply
动作描述：参与聊天回复，发送文本进行表达
- 你想要闲聊或者随便附和
- 有人提到了你，但是你还没有回应
- {mentioned_bonus}
- 如果你刚刚进行了回复，不要对同一个话题重复回应
{{
    "action": "reply",
    "target_message_id":"想要回复的消息id",
    "reason":"回复的原因"
}}

你必须从上面列出的可用action中选择一个，并说明触发action的消息id（不是消息原文）和选择该action的原因。消息id格式:m+数字

请根据动作示例，以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "planner_prompt",
    )
    
    Prompt(
        """
{time_block}
{name_block}

{chat_context_description}，以下是具体的聊天内容
{chat_content_block}

{moderation_prompt}

现在，最新的聊天消息引起了你的兴趣，你想要对其中的消息进行回复，回复标准如下：
- 你想要闲聊或者随便附和
- 有人提到了你，但是你还没有回应
- {mentioned_bonus}
- 如果你刚刚进行了回复，不要对同一个话题重复回应

请你选中一条需要回复的消息并输出其id,输出格式如下：
{{
    "action": "reply",
    "target_message_id":"想要回复的消息id，消息id格式:m+数字",
    "reason":"回复的原因"
}}

请根据示例，以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "planner_reply_prompt",
    )
    
    

    Prompt(
        """
动作：{action_name}
动作描述：{action_description}
{action_require}
{{
    "action": "{action_name}",{action_parameters},
    "target_message_id":"触发action的消息id",
    "reason":"触发action的原因"
}}
""",
        "action_prompt",
    )

    Prompt(
    """
{name_block}

{chat_context_description}，{time_block}，现在请你根据以下聊天内容，选择一个或多个action来参与聊天。如果没有合适的action，请选择no_action。,
{chat_content_block}

{moderation_prompt}
现在请你根据聊天内容和用户的最新消息选择合适的action和触发action的消息:


no_action：不选择任何动作
{{
    "action": "no_action",
    "reason":"不动作的原因"
}}

{action_options_text}

这是你最近执行过的动作，请注意如果相同的内容已经被执行，请不要重复执行：
{actions_before_now_block}

请选择，并说明触发action的消息id和选择该action的原因。消息id格式:m+数字
请根据动作示例，以严格的 JSON 格式输出，且仅包含 JSON 内容：
""",
        "sub_planner_prompt",
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
        self.planner_small_llm = LLMRequest(
            model_set=model_config.model_task_config.planner_small, request_type="planner_small"
        )  # 用于动作规划

        self.last_obs_time_mark = 0.0
    
    def find_message_by_id(
        self, message_id: str, message_id_list: List[Tuple[str, DatabaseMessages]]
    ) -> Optional[DatabaseMessages]:
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
    
    def _parse_single_action(self, action_json: dict, message_id_list: List[Tuple[str, DatabaseMessages]], current_available_actions: List[Tuple[str, ActionInfo]]) -> List[ActionPlannerInfo]:
        """解析单个action JSON并返回ActionPlannerInfo列表"""
        action_planner_infos = []

        try:
            action = action_json.get("action", "no_action")
            reasoning = action_json.get("reason", "未提供原因")
            action_data = {}

            # 将所有其他属性添加到action_data
            for key, value in action_json.items():
                if key not in ["action", "reasoning"]:
                    action_data[key] = value

            # 非no_action动作需要target_message_id
            target_message = None
            if action != "no_action":
                if target_message_id := action_json.get("target_message_id"):
                    # 根据target_message_id查找原始消息
                    target_message = self.find_message_by_id(target_message_id, message_id_list)
                    if target_message is None:
                        logger.warning(f"{self.log_prefix}无法找到target_message_id '{target_message_id}' 对应的消息")
                        # 选择最新消息作为target_message
                        target_message = message_id_list[-1]
                else:
                    logger.warning(f"{self.log_prefix}动作'{action}'缺少target_message_id")

            # 验证action是否可用
            available_action_names = [action_name for action_name, _ in current_available_actions]
            if action != "no_action" and action != "reply" and action not in available_action_names:
                logger.warning(
                    f"{self.log_prefix}LLM 返回了当前不可用或无效的动作: '{action}' (可用: {available_action_names})，将强制使用 'no_action'"
                )
                reasoning = (
                    f"LLM 返回了当前不可用的动作 '{action}' (可用: {available_action_names})。原始理由: {reasoning}"
                )
                action = "no_action"

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
                )
            )

        except Exception as e:
            logger.error(f"{self.log_prefix}解析单个action时出错: {e}")
            # 将列表转换为字典格式
            available_actions_dict = dict(current_available_actions)
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type="no_action",
                    reasoning=f"解析单个action时出错: {e}",
                    action_data={},
                    action_message=None,
                    available_actions=available_actions_dict,
                )
            )

        return action_planner_infos

    async def sub_plan(
        self,
        action_list: List[Tuple[str, ActionInfo]],
        chat_content_block: str,
        message_id_list: List[Tuple[str, DatabaseMessages]],
        is_group_chat: bool = False,
        chat_target_info: Optional[dict] = None,
        # current_available_actions: Dict[str, ActionInfo] = {},
    ) -> List[ActionPlannerInfo]:
        # 构建副planner并执行(单个副planner)
        try:
            actions_before_now = get_actions_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=time.time() - 1200,
                timestamp_end=time.time(),
                limit=20,
            )
            
            # 获取最近的actions
            # 只保留action_type在action_list中的ActionPlannerInfo
            action_names_in_list = [name for name, _ in action_list]
            # actions_before_now是List[Dict[str, Any]]格式，需要提取action_type字段
            filtered_actions = []
            for action_record in actions_before_now:
                # print(action_record)
                # print(action_record['action_name'])
                # print(action_names_in_list)
                action_type = action_record['action_name']
                if action_type in action_names_in_list:
                    filtered_actions.append(action_record)
        

            actions_before_now_block = build_readable_actions(
                actions=filtered_actions,
                mode="absolute",
            )

            chat_context_description = "你现在正在一个群聊中"
            chat_target_name = None
            if not is_group_chat and chat_target_info:
                chat_target_name = (
                    chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or "对方"
                )
                chat_context_description = f"你正在和 {chat_target_name} 私聊"

            action_options_block = ""

            for using_actions_name, using_actions_info in action_list:
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
                )

                action_options_block += using_action_prompt

            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"
            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            bot_name = global_config.bot.nickname
            if global_config.bot.alias_names:
                bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
            else:
                bot_nickname = ""
            name_block = f"你的名字是{bot_name}{bot_nickname}，请注意哪些是你自己的发言。"

            planner_prompt_template = await global_prompt_manager.get_prompt_async("sub_planner_prompt")
            prompt = planner_prompt_template.format(
                time_block=time_block,
                chat_context_description=chat_context_description,
                chat_content_block=chat_content_block,
                actions_before_now_block=actions_before_now_block,
                action_options_text=action_options_block,
                moderation_prompt=moderation_prompt_block,
                name_block=name_block,
            )
            # return prompt, message_id_list
        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            # 返回一个默认的no_action而不是字符串
            return [
                ActionPlannerInfo(
                    action_type="no_action",
                    reasoning=f"构建 Planner Prompt 时出错: {e}",
                    action_data={},
                    action_message=None,
                    available_actions=action_list,
                )
            ]

        # --- 调用 LLM (普通文本生成) ---
        llm_content = None
        action_planner_infos = []  # 存储多个ActionPlannerInfo对象

        try:
            llm_content, (reasoning_content, _, _) = await self.planner_small_llm.generate_response_async(prompt=prompt)

            if global_config.debug.show_prompt:
                logger.info(f"{self.log_prefix}副规划器原始提示词: {prompt}")
                logger.info(f"{self.log_prefix}副规划器原始响应: {llm_content}")
                if reasoning_content:
                    logger.info(f"{self.log_prefix}副规划器推理: {reasoning_content}")
            else:
                logger.debug(f"{self.log_prefix}副规划器原始提示词: {prompt}")
                logger.debug(f"{self.log_prefix}副规划器原始响应: {llm_content}")
                if reasoning_content:
                    logger.debug(f"{self.log_prefix}副规划器推理: {reasoning_content}")

        except Exception as req_e:
            logger.error(f"{self.log_prefix}副规划器LLM 请求执行失败: {req_e}")
            # 返回一个默认的no_action
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type="no_action",
                    reasoning=f"副规划器LLM 请求失败，模型出现问题: {req_e}",
                    action_data={},
                    action_message=None,
                    available_actions=action_list,
                )
            )
            return action_planner_infos

        if llm_content:
            try:
                parsed_json = json.loads(repair_json(llm_content))

                # 处理不同的JSON格式
                if isinstance(parsed_json, list):
                    # 如果是列表，处理每个action
                    if parsed_json:
                        logger.info(f"{self.log_prefix}LLM返回了{len(parsed_json)}个action")
                        for action_item in parsed_json:
                            if isinstance(action_item, dict):
                                action_planner_infos.extend(
                                    self._parse_single_action(action_item, message_id_list, action_list)
                                )
                            else:
                                logger.warning(f"{self.log_prefix}列表中的action项不是字典类型: {type(action_item)}")
                    else:
                        logger.warning(f"{self.log_prefix}LLM返回了空列表")
                        action_planner_infos.append(
                            ActionPlannerInfo(
                                action_type="no_action",
                                reasoning="LLM返回了空列表，选择no_action",
                                action_data={},
                                action_message=None,
                                available_actions=action_list,
                            )
                        )
                elif isinstance(parsed_json, dict):
                    # 如果是单个字典，处理单个action
                    action_planner_infos.extend(self._parse_single_action(parsed_json, message_id_list, action_list))
                else:
                    logger.error(f"{self.log_prefix}解析后的JSON不是字典或列表类型: {type(parsed_json)}")
                    action_planner_infos.append(
                        ActionPlannerInfo(
                            action_type="no_action",
                            reasoning=f"解析后的JSON类型错误: {type(parsed_json)}",
                            action_data={},
                            action_message=None,
                            available_actions=action_list,
                        )
                    )

            except Exception as json_e:
                logger.warning(f"{self.log_prefix}解析LLM响应JSON失败 {json_e}. LLM原始输出: '{llm_content}'")
                traceback.print_exc()
                action_planner_infos.append(
                    ActionPlannerInfo(
                        action_type="no_action",
                        reasoning=f"解析LLM响应JSON失败: {json_e}. 将使用默认动作 'no_action'.",
                        action_data={},
                        action_message=None,
                        available_actions=action_list,
                    )
                )
        else:
            # 如果没有LLM内容，返回默认的no_action
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type="no_action",
                    reasoning="副规划器没有获得LLM响应",
                    action_data={},
                    action_message=None,
                    available_actions=action_list,
                )
            )

        # 如果没有解析到任何action，返回默认的no_action
        if not action_planner_infos:
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type="no_action",
                    reasoning="副规划器没有解析到任何有效action",
                    action_data={},
                    action_message=None,
                    available_actions=action_list,
                )
            )

        logger.info(f"{self.log_prefix}副规划器返回了{len(action_planner_infos)}个action")
        return action_planner_infos

    async def plan(
        self,
        available_actions: Dict[str, ActionInfo],
        mode: ChatMode = ChatMode.FOCUS,
        loop_start_time: float = 0.0,
    ) -> Tuple[List[ActionPlannerInfo], Optional[DatabaseMessages]]:
        """
        规划器 (Planner): 使用LLM根据上下文决定做出什么动作。
        """

        action: str = "no_action"  # 默认动作
        reasoning: str = "规划器初始化默认"
        action_data = {}
        current_available_actions: Dict[str, ActionInfo] = {}
        target_message: Optional[DatabaseMessages] = None  # 初始化target_message变量
        prompt: str = ""
        message_id_list: list = []

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

        
        message_list_before_now_short = message_list_before_now[-int(global_config.chat.max_context_size * 0.3):]
        
        chat_content_block_short, message_id_list_short = build_readable_messages_with_id(
            messages=message_list_before_now_short,
            timestamp_mode="normal_no_YMD",
            truncate=False,
            show_actions=False,
        )

        self.last_obs_time_mark = time.time()

        try:
            logger.info(f"{self.log_prefix}开始构建副Planner")
            sub_planner_actions: Dict[str, ActionInfo] = {}

            for action_name, action_info in available_actions.items():
                if action_info.activation_type in [ActionActivationType.LLM_JUDGE, ActionActivationType.ALWAYS]:
                    sub_planner_actions[action_name] = action_info
                elif action_info.activation_type == ActionActivationType.RANDOM:
                    if random.random() < action_info.random_activation_probability:
                        sub_planner_actions[action_name] = action_info
                elif action_info.activation_type == ActionActivationType.KEYWORD:
                    if action_info.activation_keywords:
                        for keyword in action_info.activation_keywords:
                            if keyword in chat_content_block_short:
                                sub_planner_actions[action_name] = action_info
                elif action_info.activation_type == ActionActivationType.NEVER:
                    pass
                else:
                    logger.warning(f"{self.log_prefix}未知的激活类型: {action_info.activation_type}，跳过处理")

            sub_planner_actions_num = len(sub_planner_actions)
            sub_planner_size = global_config.chat.planner_size
            if global_config.chat.planner_size > int(global_config.chat.planner_size):
                if random.random() < global_config.chat.planner_size - int(global_config.chat.planner_size):
                    sub_planner_size = int(global_config.chat.planner_size) + 1
            sub_planner_num = int(sub_planner_actions_num / sub_planner_size)
            if sub_planner_actions_num % sub_planner_size != 0:
                sub_planner_num += 1

            logger.info(f"{self.log_prefix}副规划器数量: {sub_planner_num}, 副规划器大小: {sub_planner_size}")

            # 将sub_planner_actions随机分配到sub_planner_num个List中
            sub_planner_lists = []
            if sub_planner_actions_num > 0:
                # 将actions转换为列表并随机打乱
                action_items = list(sub_planner_actions.items())
                random.shuffle(action_items)

                # 初始化所有子列表
                for i in range(sub_planner_num):
                    sub_planner_lists.append([])

                # 分配actions到各个子列表
                for i, (action_name, action_info) in enumerate(action_items):
                    # 确保每个列表至少有一个action
                    if i < sub_planner_num:
                        sub_planner_lists[i].append((action_name, action_info))
                    else:
                        # 随机选择一个列表添加action，但不超过最大大小限制
                        available_lists = [j for j, lst in enumerate(sub_planner_lists) if len(lst) < sub_planner_size]
                        if available_lists:
                            target_list = random.choice(available_lists)
                            sub_planner_lists[target_list].append((action_name, action_info))

                logger.info(
                    f"{self.log_prefix}成功将{len(sub_planner_actions)}个actions分配到{sub_planner_num}个子列表中"
                )
                for i, lst in enumerate(sub_planner_lists):
                    logger.debug(f"{self.log_prefix}子列表{i + 1}: {len(lst)}个actions")
            else:
                logger.info(f"{self.log_prefix}没有可用的actions需要分配")

            # 先获取必要信息
            is_group_chat, chat_target_info, current_available_actions = self.get_necessary_info()

            # 并行执行所有副规划器
            async def execute_sub_plan(action_list):
                return await self.sub_plan(
                    action_list=action_list,
                    # actions_before_now=actions_before_now,
                    chat_content_block=chat_content_block_short,
                    message_id_list=message_id_list_short,
                    is_group_chat=is_group_chat,
                    chat_target_info=chat_target_info,
                    # current_available_actions=current_available_actions,
                )

            # 创建所有任务
            sub_plan_tasks = [execute_sub_plan(action_list) for action_list in sub_planner_lists]

            # 并行执行所有任务
            sub_plan_results = await asyncio.gather(*sub_plan_tasks)

            # 收集所有结果
            all_sub_planner_results = []
            for sub_result in sub_plan_results:
                all_sub_planner_results.extend(sub_result)

            logger.info(f"{self.log_prefix}所有副规划器共返回了{len(all_sub_planner_results)}个action")

            # --- 构建提示词 (调用修改后的 PromptBuilder 方法) ---
            prompt, message_id_list = await self.build_planner_prompt(
                is_group_chat=is_group_chat,  # <-- Pass HFC state
                chat_target_info=chat_target_info,  # <-- 传递获取到的聊天目标信息
                # current_available_actions="",  # <-- Pass determined actions
                mode=mode,
                chat_content_block=chat_content_block,
                # actions_before_now_block=actions_before_now_block,
                message_id_list=message_id_list,
            )

            # --- 调用 LLM (普通文本生成) ---
            llm_content = None
            try:
                llm_content, (reasoning_content, _, _) = await self.planner_llm.generate_response_async(prompt=prompt)

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
                action = "no_action"

            if llm_content:
                try:
                    parsed_json = json.loads(repair_json(llm_content))

                    # 处理不同的JSON格式，复用_parse_single_action函数
                    if isinstance(parsed_json, list):
                        if parsed_json:
                            # 使用最后一个action（保持原有逻辑）
                            parsed_json = parsed_json[-1]
                            logger.warning(f"{self.log_prefix}LLM返回了多个JSON对象，使用最后一个: {parsed_json}")
                        else:
                            parsed_json = {}

                    if isinstance(parsed_json, dict):
                        # 使用_parse_single_action函数解析单个action
                        # 将字典转换为列表格式
                        current_available_actions_list = list(current_available_actions.items())
                        action_planner_infos = self._parse_single_action(
                            parsed_json, message_id_list, current_available_actions_list
                        )

                        if action_planner_infos:
                            # 获取第一个（也是唯一一个）action的信息
                            action_info = action_planner_infos[0]
                            action = action_info.action_type
                            reasoning = action_info.reasoning
                            action_data.update(action_info.action_data)
                            target_message = action_info.action_message

                            # 处理target_message为None的情况（保持原有的重试逻辑）
                            if target_message is None and action != "no_action":
                                # 尝试获取最新消息作为target_message
                                target_message = message_id_list[-1]
                                if target_message is None:
                                    logger.warning(f"{self.log_prefix}无法获取任何消息作为target_message")
                        else:
                            # 如果没有解析到action，使用默认值
                            action = "no_action"
                            reasoning = "解析action失败"
                            target_message = None
                    else:
                        logger.error(f"{self.log_prefix}解析后的JSON不是字典类型: {type(parsed_json)}")
                        action = "no_action"
                        reasoning = f"解析后的JSON类型错误: {type(parsed_json)}"
                        target_message = None

                except Exception as json_e:
                    logger.warning(f"{self.log_prefix}解析LLM响应JSON失败 {json_e}. LLM原始输出: '{llm_content}'")
                    traceback.print_exc()
                    action = "no_action"
                    reasoning = f"解析LLM响应JSON失败: {json_e}. 将使用默认动作 'no_action'."
                    target_message = None

        except Exception as outer_e:
            logger.error(f"{self.log_prefix}Planner 处理过程中发生意外错误，规划失败，将执行 no_action: {outer_e}")
            traceback.print_exc()
            action = "no_action"
            reasoning = f"Planner 内部处理错误: {outer_e}"

        is_parallel = True
        if mode == ChatMode.NORMAL and action in current_available_actions:
            if is_parallel:
                is_parallel = current_available_actions[action].parallel_action

        action_data["loop_start_time"] = loop_start_time

        # 过滤掉no_action，除非所有结果都是no_action
        def filter_no_actions(action_list):
            """过滤no_action，如果所有都是no_action则返回一个"""
            non_no_actions = [a for a in action_list if a.action_type != "no_action"]
            if non_no_actions:
                return non_no_actions
            else:
                # 如果所有都是no_action，返回第一个
                return [action_list[0]] if action_list else []

        # 根据is_parallel决定返回值
        if is_parallel:
            # 如果为真，将主规划器的结果和副规划器的结果都返回
            main_actions = []

            # 添加主规划器的action（如果不是no_action）
            if action != "no_action":
                main_actions.append(
                    ActionPlannerInfo(
                        action_type=action,
                        reasoning=reasoning,
                        action_data=action_data,
                        action_message=target_message,
                        available_actions=available_actions,
                    )
                )

            # 先合并主副规划器的结果
            all_actions = main_actions + all_sub_planner_results

            # 然后统一过滤no_action
            actions = filter_no_actions(all_actions)

            # 如果所有结果都是no_action，返回一个no_action
            if not actions:
                actions = [
                    ActionPlannerInfo(
                        action_type="no_action",
                        reasoning="所有规划器都选择不执行动作",
                        action_data={},
                        action_message=None,
                        available_actions=available_actions,
                    )
                ]

            logger.info(
                f"{self.log_prefix}并行模式：返回主规划器{len(main_actions)}个action + 副规划器{len(all_sub_planner_results)}个action，过滤后总计{len(actions)}个action"
            )
        else:
            # 如果为假，只返回副规划器的结果
            actions = filter_no_actions(all_sub_planner_results)

            # 如果所有结果都是no_action，返回一个no_action
            if not actions:
                actions = [
                    ActionPlannerInfo(
                        action_type="no_action",
                        reasoning="副规划器都选择不执行动作",
                        action_data={},
                        action_message=None,
                        available_actions=available_actions,
                    )
                ]

            logger.info(f"{self.log_prefix}非并行模式：返回副规划器的{len(actions)}个action（已过滤no_action）")

        return actions, target_message

    async def build_planner_prompt(
        self,
        is_group_chat: bool,  # Now passed as argument
        chat_target_info: Optional[dict],  # Now passed as argument
        # current_available_actions: Dict[str, ActionInfo],
        mode: ChatMode = ChatMode.FOCUS,
        # actions_before_now_block :str = "",
        chat_content_block :str = "",
        message_id_list :List[Tuple[str, DatabaseMessages]] = None,
    ) -> tuple[str, List[DatabaseMessages]]:  # sourcery skip: use-join
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:
            actions_before_now = get_actions_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=time.time() - 600,
                timestamp_end=time.time(),
                limit=6,
            )

            actions_before_now_block = build_readable_actions(
                actions=actions_before_now,
            )
            
        
            if actions_before_now_block:
                actions_before_now_block = f"你刚刚选择并执行过的action是：\n{actions_before_now_block}"
            else:
                actions_before_now_block = ""

            mentioned_bonus = ""
            if global_config.chat.mentioned_bot_inevitable_reply:
                mentioned_bonus = "\n- 有人提到你"
            if global_config.chat.at_bot_inevitable_reply:
                mentioned_bonus = "\n- 有人提到你，或者at你"


            chat_context_description = "你现在正在一个群聊中"
            chat_target_name = None
            if not is_group_chat and chat_target_info:
                chat_target_name = (
                    chat_target_info.get("person_name") or chat_target_info.get("user_nickname") or "对方"
                )
                chat_context_description = f"你正在和 {chat_target_name} 私聊"
                
                
            # 别删，之后可能会允许主Planner扩展

            # action_options_block = ""

            # if current_available_actions:
            #     for using_actions_name, using_actions_info in current_available_actions.items():
            #         if using_actions_info.action_parameters:
            #             param_text = "\n"
            #             for param_name, param_description in using_actions_info.action_parameters.items():
            #                 param_text += f'    "{param_name}":"{param_description}"\n'
            #             param_text = param_text.rstrip("\n")
            #         else:
            #             param_text = ""

            #         require_text = ""
            #         for require_item in using_actions_info.action_require:
            #             require_text += f"- {require_item}\n"
            #         require_text = require_text.rstrip("\n")

            #         using_action_prompt = await global_prompt_manager.get_prompt_async("action_prompt")
            #         using_action_prompt = using_action_prompt.format(
            #             action_name=using_actions_name,
            #             action_description=using_actions_info.description,
            #             action_parameters=param_text,
            #             action_require=require_text,
            #         )

            #         action_options_block += using_action_prompt
            # else:
            #     action_options_block = ""

            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"

            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            bot_name = global_config.bot.nickname
            if global_config.bot.alias_names:
                bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
            else:
                bot_nickname = ""
            name_block = f"你的名字是{bot_name}{bot_nickname}，请注意哪些是你自己的发言。"

            if mode == ChatMode.FOCUS:
                planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")
                prompt = planner_prompt_template.format(
                    time_block=time_block,
                    chat_context_description=chat_context_description,
                    chat_content_block=chat_content_block,
                    actions_before_now_block=actions_before_now_block,
                    mentioned_bonus=mentioned_bonus,
                    # action_options_text=action_options_block,
                    moderation_prompt=moderation_prompt_block,
                    name_block=name_block,
                    plan_style=global_config.personality.plan_style,
                )
                return prompt, message_id_list
            else:
                planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_reply_prompt")
                prompt = planner_prompt_template.format(
                    time_block=time_block,
                    chat_context_description=chat_context_description,
                    chat_content_block=chat_content_block,
                    mentioned_bonus=mentioned_bonus,
                    moderation_prompt=moderation_prompt_block,
                    name_block=name_block,
                )
                return prompt, message_id_list
        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错", []

    def get_necessary_info(self) -> Tuple[bool, Optional[dict], Dict[str, ActionInfo]]:
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


init_prompt()
