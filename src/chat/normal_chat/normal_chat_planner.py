import json
from typing import Dict, Any
from rich.traceback import install
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.common.logger_manager import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.individuality.individuality import individuality
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.chat.actions.base_action import ChatMode
from src.chat.message_receive.message import MessageThinking
from json_repair import repair_json
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
import time
import traceback

logger = get_logger("normal_chat_planner")

install(extra_lines=3)


def init_prompt():
    Prompt(
        """
你的自我认知是：
{self_info_block}
请记住你的性格，身份和特点。

你是群内的一员，你现在正在参与群内的闲聊，以下是群内的聊天内容：
{chat_context}

基于以上聊天上下文和用户的最新消息，选择最合适的action。

注意，除了下面动作选项之外，你在聊天中不能做其他任何事情，这是你能力的边界，现在请你选择合适的action:

{action_options_text}

重要说明：
- "no_action" 表示只进行普通聊天回复，不执行任何额外动作
- "change_to_focus_chat" 表示当聊天变得热烈、自己回复条数很多或需要深入交流时，正常回复消息并切换到focus_chat模式进行更深入的对话
- 其他action表示在普通回复的基础上，执行相应的额外动作

你必须从上面列出的可用action中选择一个，并说明原因。
{moderation_prompt}

请以动作的输出要求，以严格的 JSON 格式输出，且仅包含 JSON 内容。不要有任何其他文字或解释：
""",
        "normal_chat_planner_prompt",
    )

    Prompt(
        """
动作：{action_name}
该动作的描述：{action_description}
使用该动作的场景：
{action_require}
输出要求：
{{
    "action": "{action_name}",{action_parameters}
}}
""",
        "normal_chat_action_prompt",
    )


class NormalChatPlanner:
    def __init__(self, log_prefix: str, action_manager: ActionManager):
        self.log_prefix = log_prefix
        # LLM规划器配置
        self.planner_llm = LLMRequest(
            model=global_config.model.planner,
            request_type="normal_chat.planner",  # 用于normal_chat动作规划
        )

        self.action_manager = action_manager

    async def plan(self, message: MessageThinking, sender_name: str = "某人") -> Dict[str, Any]:
        """
        Normal Chat 规划器: 使用LLM根据上下文决定做出什么动作。

        参数:
            message: 思考消息对象
            sender_name: 发送者名称
        """

        action = "no_action"  # 默认动作改为no_action
        reasoning = "规划器初始化默认"
        action_data = {}

        try:
            # 设置默认值
            nickname_str = ""
            for nicknames in global_config.bot.alias_names:
                nickname_str += f"{nicknames},"
            name_block = f"你的名字是{global_config.bot.nickname},你的昵称有{nickname_str}，有人也会用这些昵称称呼你。"

            personality_block = individuality.get_personality_prompt(x_person=2, level=2)
            identity_block = individuality.get_identity_prompt(x_person=2, level=2)

            self_info = name_block + personality_block + identity_block

            # 获取当前可用的动作，使用Normal模式过滤
            current_available_actions = self.action_manager.get_using_actions_for_mode(ChatMode.NORMAL)
            
            # 注意：动作的激活判定现在在 normal_chat_action_modifier 中完成
            # 这里直接使用经过 action_modifier 处理后的最终动作集
            # 符合职责分离原则：ActionModifier负责动作管理，Planner专注于决策

            # 如果没有可用动作，直接返回no_action
            if not current_available_actions:
                logger.debug(f"{self.log_prefix}规划器: 没有可用动作，返回no_action")
                return {
                    "action_result": {"action_type": action, "action_data": action_data, "reasoning": reasoning, "is_parallel": True},
                    "chat_context": "",
                    "action_prompt": "",
                }

            # 构建normal_chat的上下文 (使用与normal_chat相同的prompt构建方法)
            message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                chat_id=message.chat_stream.stream_id,
                timestamp=time.time(),
                limit=global_config.focus_chat.observation_context_size,
            )
            
            chat_context = build_readable_messages(
                message_list_before_now,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
                show_actions=True,
            )
            
            # 构建planner的prompt
            prompt = await self.build_planner_prompt(
                self_info_block=self_info,
                chat_context=chat_context,
                current_available_actions=current_available_actions,
            )

            if not prompt:
                logger.warning(f"{self.log_prefix}规划器: 构建提示词失败")
                return {
                    "action_result": {"action_type": action, "action_data": action_data, "reasoning": reasoning, "is_parallel": False},
                    "chat_context": chat_context,
                    "action_prompt": "",
                }

            # 使用LLM生成动作决策
            try:
                content, (reasoning_content, model_name) = await self.planner_llm.generate_response_async(prompt)
                
                # logger.info(f"{self.log_prefix}规划器原始提示词: {prompt}")
                logger.info(f"{self.log_prefix}规划器原始响应: {content}")
                logger.info(f"{self.log_prefix}规划器推理: {reasoning_content}")
                logger.info(f"{self.log_prefix}规划器模型: {model_name}")

                # 解析JSON响应
                try:
                    # 尝试修复JSON
                    fixed_json = repair_json(content)
                    action_result = json.loads(fixed_json)

                    action = action_result.get("action", "no_action")
                    reasoning = action_result.get("reasoning", "未提供原因")

                    # 提取其他参数作为action_data
                    action_data = {k: v for k, v in action_result.items() if k not in ["action", "reasoning"]}

                    # 验证动作是否在可用动作列表中，或者是特殊动作
                    if action not in current_available_actions and action != "change_to_focus_chat":
                        logger.warning(f"{self.log_prefix}规划器选择了不可用的动作: {action}, 回退到no_action")
                        action = "no_action"
                        reasoning = f"选择的动作{action}不在可用列表中，回退到no_action"
                        action_data = {}

                except json.JSONDecodeError as e:
                    logger.warning(f"{self.log_prefix}规划器JSON解析失败: {e}, 内容: {content}")
                    action = "no_action"
                    reasoning = "JSON解析失败，使用默认动作"
                    action_data = {}

            except Exception as e:
                logger.error(f"{self.log_prefix}规划器LLM调用失败: {e}")
                action = "no_action"
                reasoning = "LLM调用失败，使用默认动作"
                action_data = {}

        except Exception as outer_e:
            logger.error(f"{self.log_prefix}规划器异常: {outer_e}")
            # 设置异常时的默认值
            current_available_actions = {}
            chat_context = "无法获取聊天上下文"
            prompt = ""
            action = "no_action"
            reasoning = "规划器出现异常，使用默认动作"
            action_data = {}

        # 检查动作是否支持并行执行
        is_parallel = False
        if action in current_available_actions:
            action_info = current_available_actions[action]
            is_parallel = action_info.get("parallel_action", False)
        
        logger.debug(f"{self.log_prefix}规划器决策动作:{action}, 动作信息: '{action_data}', 理由: {reasoning}, 并行执行: {is_parallel}")

        # 恢复到默认动作集
        self.action_manager.restore_actions()
        logger.debug(
            f"{self.log_prefix}规划后恢复到默认动作集, 当前可用: {list(self.action_manager.get_using_actions().keys())}"
        )

        # 构建 action 记录
        action_record = {
            "action_type": action,
            "action_data": action_data,
            "reasoning": reasoning,
            "timestamp": time.time(),
            "model_name": model_name if 'model_name' in locals() else None
        }

        action_result = {
            "action_type": action, 
            "action_data": action_data, 
            "reasoning": reasoning,
            "is_parallel": is_parallel,
            "action_record": json.dumps(action_record, ensure_ascii=False)
        }

        plan_result = {
            "action_result": action_result,
            "chat_context": chat_context,
            "action_prompt": prompt,
        }

        return plan_result

    async def build_planner_prompt(
        self,
        self_info_block: str,
        chat_context: str,
        current_available_actions: Dict[str, Any],
    ) -> str:
        """构建 Normal Chat Planner LLM 的提示词"""
        try:
            # 构建动作选项文本
            action_options_text = ""

            # 添加特殊的change_to_focus_chat动作
            action_options_text += "动作：change_to_focus_chat\n"
            action_options_text += (
                "该动作的描述：当聊天变得热烈、自己回复条数很多或需要深入交流时使用，正常回复消息并切换到focus_chat模式\n"
            )

            action_options_text += "使用该动作的场景：\n"
            action_options_text += "- 聊天上下文中自己的回复条数较多（超过3-4条）\n"
            action_options_text += "- 对话进行得非常热烈活跃\n"
            action_options_text += "- 用户表现出深入交流的意图\n"
            action_options_text += "- 话题需要更专注和深入的讨论\n\n"
            
            action_options_text += "输出要求：\n"
            action_options_text += "{{"
            action_options_text += "    \"action\": \"change_to_focus_chat\""
            action_options_text += "}}\n\n"
            
            
            
            
            for action_name, action_info in current_available_actions.items():
                action_description = action_info.get("description", "")
                action_parameters = action_info.get("parameters", {})
                action_require = action_info.get("require", [])

                if action_parameters:
                    param_text = "\n"
                    print(action_parameters)
                    for param_name, param_description in action_parameters.items():
                        param_text += f'    "{param_name}":"{param_description}"\n'
                    param_text = param_text.rstrip('\n')
                else:
                    param_text = ""


                require_text = ""
                for require_item in action_require:
                    require_text += f"- {require_item}\n"
                require_text = require_text.rstrip('\n')

                # 构建单个动作的提示
                action_prompt = await global_prompt_manager.format_prompt(
                    "normal_chat_action_prompt",
                    action_name=action_name,
                    action_description=action_description,
                    action_parameters=param_text,
                    action_require=require_text,
                )
                action_options_text += action_prompt + "\n\n"

            # 审核提示
            moderation_prompt = "请确保你的回复符合平台规则，避免不当内容。"

            # 使用模板构建最终提示词
            prompt = await global_prompt_manager.format_prompt(
                "normal_chat_planner_prompt",
                self_info_block=self_info_block,
                action_options_text=action_options_text,
                moderation_prompt=moderation_prompt,
                chat_context=chat_context,
            )

            return prompt

        except Exception as e:
            logger.error(f"{self.log_prefix}构建Planner提示词失败: {e}")
            traceback.print_exc()
            return ""




init_prompt()
