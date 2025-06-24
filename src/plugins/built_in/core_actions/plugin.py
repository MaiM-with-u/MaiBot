"""
核心动作插件

将系统核心动作（reply、no_reply、emoji）转换为新插件系统格式
这是系统的内置插件，提供基础的聊天交互功能
"""

import random
import time
from typing import List, Tuple, Type

# 导入新插件系统
from src.plugin_system import BasePlugin, register_plugin, BaseAction, ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import emoji_api, generator_api, message_api
from src.plugins.built_in.core_actions.no_reply import NoReplyAction

logger = get_logger("core_actions")

# 常量定义
WAITING_TIME_THRESHOLD = 1200  # 等待新消息时间阈值，单位秒


class ReplyAction(BaseAction):
    """回复动作 - 参与聊天回复"""

    # 激活设置
    focus_activation_type = ActionActivationType.ALWAYS
    normal_activation_type = ActionActivationType.NEVER
    mode_enable = ChatMode.FOCUS
    parallel_action = False

    # 动作基本信息
    action_name = "reply"
    action_description = "参与聊天回复，发送文本进行表达"

    # 动作参数定义
    action_parameters = {
        "reply_to": "你要回复的对方的发言内容，格式：（用户名:发言内容），可以为none",
        "reason": "回复的原因",
    }

    # 动作使用场景
    action_require = ["你想要闲聊或者随便附和", "有人提到你", "如果你刚刚进行了回复，不要对同一个话题重复回应"]

    # 关联类型
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行回复动作"""
        logger.info(f"{self.log_prefix} 决定回复: {self.reasoning}")

        start_time = self.action_data.get("loop_start_time", time.time())

        try:
            success, reply_set = await generator_api.generate_reply(
                action_data=self.action_data,
                chat_id=self.chat_id,
            )

            # 检查从start_time以来的新消息数量
            # 获取动作触发时间或使用默认值
            current_time = time.time()
            new_message_count = message_api.count_new_messages(
                chat_id=self.chat_id, start_time=start_time, end_time=current_time
            )

            # 根据新消息数量决定是否使用reply_to
            need_reply = new_message_count >= random.randint(2, 5)
            logger.info(
                f"{self.log_prefix} 从{start_time}到{current_time}共有{new_message_count}条新消息，{'使用' if need_reply else '不使用'}reply_to"
            )

            # 构建回复文本
            reply_text = ""
            first_replyed = False
            for reply_seg in reply_set:
                data = reply_seg[1]
                if not first_replyed:
                    if need_reply:
                        await self.send_text(content=data, reply_to=self.action_data.get("reply_to", ""), typing=False)
                        first_replyed = True
                    else:
                        await self.send_text(content=data, typing=False)
                        first_replyed = True
                else:
                    await self.send_text(content=data, typing=True)
                reply_text += data

            # 存储动作记录
            await self.store_action_info(
                action_build_into_prompt=False,
                action_prompt_display=reply_text,
                action_done=True,
            )

            # 重置NoReplyAction的连续计数器
            NoReplyAction.reset_consecutive_count()

            return success, reply_text

        except Exception as e:
            logger.error(f"{self.log_prefix} 回复动作执行失败: {e}")
            return False, f"回复失败: {str(e)}"


class EmojiAction(BaseAction):
    """表情动作 - 发送表情包"""

    # 激活设置
    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.RANDOM
    mode_enable = ChatMode.ALL
    parallel_action = True
    random_activation_probability = 0.1  # 默认值，可通过配置覆盖

    # 动作基本信息
    action_name = "emoji"
    action_description = "发送表情包辅助表达情绪"

    # LLM判断提示词
    llm_judge_prompt = """
    判定是否需要使用表情动作的条件：
    1. 用户明确要求使用表情包
    2. 这是一个适合表达强烈情绪的场合
    3. 不要发送太多表情包，如果你已经发送过多个表情包则回答"否"
    
    请回答"是"或"否"。
    """

    # 动作参数定义
    action_parameters = {"description": "文字描述你想要发送的表情包内容"}

    # 动作使用场景
    action_require = ["表达情绪时可以选择使用", "重点：不要连续发，如果你已经发过[表情包]，就不要选择此动作"]

    # 关联类型
    associated_types = ["emoji"]

    async def execute(self) -> Tuple[bool, str]:
        """执行表情动作"""
        logger.info(f"{self.log_prefix} 决定发送表情")

        try:
            # 1. 根据描述选择表情包
            description = self.action_data.get("description", "")
            emoji_result = await emoji_api.get_by_description(description)

            if not emoji_result:
                logger.warning(f"{self.log_prefix} 未找到匹配描述 '{description}' 的表情包")
                return False, f"未找到匹配 '{description}' 的表情包"

            emoji_base64, emoji_description, matched_emotion = emoji_result
            logger.info(f"{self.log_prefix} 找到表情包: {emoji_description}, 匹配情感: {matched_emotion}")

            # 使用BaseAction的便捷方法发送表情包
            success = await self.send_emoji(emoji_base64)

            if not success:
                logger.error(f"{self.log_prefix} 表情包发送失败")
                return False, "表情包发送失败"

            # 重置NoReplyAction的连续计数器
            NoReplyAction.reset_consecutive_count()

            return True, f"发送表情包: {emoji_description}"

        except Exception as e:
            logger.error(f"{self.log_prefix} 表情动作执行失败: {e}")
            return False, f"表情发送失败: {str(e)}"


@register_plugin
class CoreActionsPlugin(BasePlugin):
    """核心动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name = "core_actions"  # 内部标识符
    enable_plugin = True
    config_file_name = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "components": "核心组件启用配置",
        "no_reply": "不回复动作配置（智能等待机制）",
        "emoji": "表情动作配置",
    }

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="0.1.0", description="配置文件版本"),
        },
        "components": {
            "enable_reply": ConfigField(type=bool, default=True, description="是否启用'回复'动作"),
            "enable_no_reply": ConfigField(type=bool, default=True, description="是否启用'不回复'动作"),
            "enable_emoji": ConfigField(type=bool, default=True, description="是否启用'表情'动作"),
            "enable_change_to_focus": ConfigField(type=bool, default=True, description="是否启用'切换到专注模式'动作"),
            "enable_exit_focus": ConfigField(type=bool, default=True, description="是否启用'退出专注模式'动作"),
        },
        "no_reply": {
            "max_timeout": ConfigField(type=int, default=1200, description="最大等待超时时间（秒）"),
            "min_judge_interval": ConfigField(
                type=float, default=1.0, description="LLM判断的最小间隔时间（秒），防止过于频繁"
            ),
            "auto_exit_message_count": ConfigField(
                type=int, default=20, description="累计消息数量达到此阈值时自动结束等待"
            ),
            "random_probability": ConfigField(
                type=float, default=0.8, description="Focus模式下，随机选择不回复的概率（0.0到1.0）", example=0.8
            ),
            "skip_judge_when_tired": ConfigField(
                type=bool, default=True, description="当发言过多时是否启用跳过LLM判断机制"
            ),
            "skip_probability_light": ConfigField(
                type=float, default=0.3, description="轻度疲惫时跳过LLM判断的概率", example=0.2
            ),
            "skip_probability_medium": ConfigField(
                type=float, default=0.5, description="中度疲惫时跳过LLM判断的概率", example=0.4
            ),
            "skip_probability_heavy": ConfigField(
                type=float, default=0.7, description="重度疲惫时跳过LLM判断的概率", example=0.6
            ),
        },
        "emoji": {
            "random_probability": ConfigField(
                type=float, default=0.1, description="Normal模式下，随机发送表情的概率（0.0到1.0）", example=0.15
            )
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 从配置动态设置Action/Command ---
        emoji_chance = self.get_config("emoji.random_probability", 0.1)
        EmojiAction.random_activation_probability = emoji_chance

        no_reply_probability = self.get_config("no_reply.random_probability", 0.8)
        NoReplyAction.random_activation_probability = no_reply_probability

        min_judge_interval = self.get_config("no_reply.min_judge_interval", 1.0)
        NoReplyAction._min_judge_interval = min_judge_interval

        auto_exit_message_count = self.get_config("no_reply.auto_exit_message_count", 20)
        NoReplyAction._auto_exit_message_count = auto_exit_message_count

        max_timeout = self.get_config("no_reply.max_timeout", 600)
        NoReplyAction._max_timeout = max_timeout

        skip_judge_when_tired = self.get_config("no_reply.skip_judge_when_tired", True)
        NoReplyAction._skip_judge_when_tired = skip_judge_when_tired

        skip_probability_light = self.get_config("no_reply.skip_probability_light", 0.2)
        NoReplyAction._skip_probability_light = skip_probability_light

        skip_probability_medium = self.get_config("no_reply.skip_probability_medium", 0.4)
        NoReplyAction._skip_probability_medium = skip_probability_medium

        skip_probability_heavy = self.get_config("no_reply.skip_probability_heavy", 0.6)
        NoReplyAction._skip_probability_heavy = skip_probability_heavy

        # 新增：频率检测相关配置
        frequency_check_window = self.get_config("no_reply.frequency_check_window", 600)
        NoReplyAction._frequency_check_window = frequency_check_window

        # --- 根据配置注册组件 ---
        components = []
        if self.get_config("components.enable_reply", True):
            components.append((ReplyAction.get_action_info(), ReplyAction))
        if self.get_config("components.enable_no_reply", True):
            components.append((NoReplyAction.get_action_info(), NoReplyAction))
        if self.get_config("components.enable_emoji", True):
            components.append((EmojiAction.get_action_info(), EmojiAction))

        # components.append((DeepReplyAction.get_action_info(), DeepReplyAction))

        return components


# class DeepReplyAction(BaseAction):
#     """回复动作 - 参与聊天回复"""

#     # 激活设置
#     focus_activation_type = ActionActivationType.ALWAYS
#     normal_activation_type = ActionActivationType.NEVER
#     mode_enable = ChatMode.FOCUS
#     parallel_action = False

#     # 动作基本信息
#     action_name = "deep_reply"
#     action_description = "参与聊天回复，关注某个话题，对聊天内容进行深度思考，给出回复"

#     # 动作参数定义
#     action_parameters = {
#         "topic": "想要思考的话题"
#     }

#     # 动作使用场景
#     action_require = ["有些问题需要深度思考", "某个问题可能涉及多个方面", "某个问题涉及专业领域或者需要专业知识","这个问题讨论的很激烈，需要深度思考"]

#     # 关联类型
#     associated_types = ["text"]

#     async def execute(self) -> Tuple[bool, str]:
#         """执行回复动作"""
#         logger.info(f"{self.log_prefix} 决定深度思考")

#         try:
#             # 获取聊天观察
#             chatting_observation = self._get_chatting_observation()
#             if not chatting_observation:
#                 return False, "未找到聊天观察"

#             talking_message_str = chatting_observation.talking_message_str

#             # 处理回复目标
#             chat_stream = self.api.get_service("chat_stream")
#             anchor_message = await create_empty_anchor_message(chat_stream.platform, chat_stream.group_info, chat_stream)


#             llm_model = self.api.get_available_models().replyer_1

#             prompt = f"""
#             {talking_message_str}

#             在上面的聊天中，你对{self.action_data.get("topic", "")}感兴趣，形成深刻观点，请你思考,总结成一份学术论文,APA标准格式
#             """

#             success, response, reasoning, model_name = await self.api.generate_with_model(prompt, llm_model)

#             print(prompt)
#             print(f"DeepReplyAction: {response}")

#             # prompt = f"""
#             # {talking_message_str}

#             # 在上面的聊天中，你对{self.action_data.get("topic", "")}感兴趣，请你思考
#             # """

#             extra_info_block = self.action_data.get("extra_info_block", "")
#             extra_info_block += response
#             # extra_info_block += f"\n--------------------------------\n注意，这是最重要的内容！！！！！你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以下方聊天记录的回复要求不再适用，请你自由的表达，不论字数长短限制\n\n--------------------------------\n注意，你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以下方聊天记录的回复要求不再适用，请你自由的表达，不论字数长短限制\n"
#             # extra_info_block += f"\n--------------------------------\n注意，优先关注这句！！！！你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以下方聊天记录的回复要求不再适用，请你自由的表达，不论字数长短限制\n\n--------------------------------\n注意，你现在可以用比较长的篇幅来表达你的观点，不要只回复一个字或者几个字\n由于你进入了深度思考模式，所以其他的回复要求不再适用，请你自由的表达，不论字数长短限制\n"
#             self.action_data["extra_info_block"] = extra_info_block


#             # 获取回复器服务
#             # replyer = self.api.get_service("replyer")
#             # if not replyer:
#                 # logger.error(f"{self.log_prefix} 未找到回复器服务")
#                 # return False, "回复器服务不可用"

#             # await self.send_message_by_expressor(extra_info_block)
#             await self.send_text(extra_info_block)
#             # 执行回复
#             # success, reply_set = await replyer.deal_reply(
#             #     cycle_timers=self.cycle_timers,
#             #     action_data=self.action_data,
#             #     anchor_message=anchor_message,
#             #     reasoning=self.reasoning,
#             #     thinking_id=self.thinking_id,
#             # )

#             # 构建回复文本
#             reply_text = "self._build_reply_text(reply_set)"

#             # 存储动作记录
#             await self.api.store_action_info(
#                 action_build_into_prompt=False,
#                 action_prompt_display=reply_text,
#                 action_done=True,
#                 thinking_id=self.thinking_id,
#                 action_data=self.action_data,
#             )

#             # 重置NoReplyAction的连续计数器
#             NoReplyAction.reset_consecutive_count()

#             return success, reply_text

#         except Exception as e:
#             logger.error(f"{self.log_prefix} 回复动作执行失败: {e}")
#             return False, f"回复失败: {str(e)}"

#     def _get_chatting_observation(self) -> Optional[ChattingObservation]:
#         """获取聊天观察对象"""
#         observations = self.api.get_service("observations") or []
#         for obs in observations:
#             if isinstance(obs, ChattingObservation):
#                 return obs
#         return None


#     def _build_reply_text(self, reply_set) -> str:
#         """构建回复文本"""
#         reply_text = ""
#         if reply_set:
#             for reply in reply_set:
#                 data = reply[1]
#                 reply_text += data
#         return reply_text
