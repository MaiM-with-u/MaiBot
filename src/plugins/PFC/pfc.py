from typing import List, Tuple, TYPE_CHECKING
from src.common.logger import get_module_logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from .pfc_utils import get_items_from_json
from src.individuality.individuality import Individuality
from .conversation_info import ConversationInfo
from .observation_info import ObservationInfo
from src.plugins.utils.chat_message_builder import build_readable_messages
import asyncio
import time
import random
from .message_sender import DirectMessageSender
from ..chat.chat_stream import ChatStream
from maim_message import UserInfo
from src.config.config import global_config
from rich.traceback import install

install(extra_lines=3)

if TYPE_CHECKING:
    pass

logger = get_module_logger("pfc")


def _calculate_similarity(goal1: str, goal2: str) -> float:
    """简单计算两个目标之间的相似度

    这里使用一个简单的实现，实际可以使用更复杂的文本相似度算法

    Args:
        goal1: 第一个目标
        goal2: 第二个目标

    Returns:
        float: 相似度得分 (0-1)
    """
    # 简单实现：检查重叠字数比例
    words1 = set(goal1)
    words2 = set(goal2)
    overlap = len(words1.intersection(words2))
    total = len(words1.union(words2))
    return overlap / total if total > 0 else 0


class IdleConversationStarter:
    """长时间无对话主动发起对话的组件"""

    def __init__(self, stream_id: str, private_name: str):
        self.stream_id = stream_id
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.message_sender = DirectMessageSender(private_name)

        # LLM请求对象，用于生成主动对话内容
        self.llm = LLMRequest(
            model=global_config.llm_normal, temperature=0.8, max_tokens=500, request_type="idle_conversation_starter"
        )

        # 个性化信息
        self.personality_info = Individuality.get_instance().get_prompt(x_person=2, level=3)
        self.name = global_config.BOT_NICKNAME
        self.nick_name = global_config.BOT_ALIAS_NAMES

        # 从配置文件读取配置参数，或使用默认值
        self.enabled = getattr(global_config, 'idle_conversation', {}).get('enable_idle_conversation', True)
        self.idle_check_interval = getattr(global_config, 'idle_conversation', {}).get('idle_check_interval', 10)
        self.min_idle_time = getattr(global_config, 'idle_conversation', {}).get('min_idle_time', 60)
        self.max_idle_time = getattr(global_config, 'idle_conversation', {}).get('max_idle_time', 120)

        # 计算实际触发阈值（在min和max之间随机）
        self.actual_idle_threshold = random.randint(self.min_idle_time, self.max_idle_time)

        # 工作状态
        self.last_message_time = time.time()
        self._running = False
        self._task = None

    def start(self):
        """启动空闲对话检测"""
        # 如果功能被禁用，则不启动
        if not self.enabled:
            logger.info(f"[私聊][{self.private_name}]主动发起对话功能已禁用")
            return

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._check_idle_loop())
        logger.info(f"[私聊][{self.private_name}]启动空闲对话检测，阈值设置为{self.actual_idle_threshold}秒")

    def stop(self):
        """停止空闲对话检测"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info(f"[私聊][{self.private_name}]停止空闲对话检测")

    def update_last_message_time(self, message_time=None):
        """更新最后一条消息的时间"""
        self.last_message_time = message_time or time.time()
        # 重新随机化下一次触发的时间阈值
        self.actual_idle_threshold = random.randint(self.min_idle_time, self.max_idle_time)
        logger.debug(
            f"[私聊][{self.private_name}]更新最后消息时间: {self.last_message_time}，新阈值: {self.actual_idle_threshold}秒")

    def reload_config(self):
        """重新加载配置"""
        # 从配置文件重新读取参数
        self.enabled = getattr(global_config, 'idle_conversation', {}).get('enable_idle_conversation', True)
        self.idle_check_interval = getattr(global_config, 'idle_conversation', {}).get('idle_check_interval', 10)
        self.min_idle_time = getattr(global_config, 'idle_conversation', {}).get('min_idle_time', 7200)
        self.max_idle_time = getattr(global_config, 'idle_conversation', {}).get('max_idle_time', 18000)
        logger.debug(
            f"[私聊][{self.private_name}]重新加载主动对话配置: 启用={self.enabled}, 检查间隔={self.idle_check_interval}秒, 最短间隔={self.min_idle_time}秒, 最长间隔={self.max_idle_time}秒")

    async def _check_idle_loop(self):
        """检查空闲状态的循环"""
        try:
            config_reload_counter = 0
            config_reload_interval = 100  # 每100次检查重新加载一次配置

            while self._running:
                # 定期重新加载配置
                config_reload_counter += 1
                if config_reload_counter >= config_reload_interval:
                    self.reload_config()
                    config_reload_counter = 0

                # 检查是否启用了主动对话功能
                if not self.enabled:
                    # 如果禁用了功能，就等待一段时间后再次检查配置
                    await asyncio.sleep(self.idle_check_interval)
                    continue

                current_time = time.time()
                idle_time = current_time - self.last_message_time

                if idle_time >= self.actual_idle_threshold:
                    logger.info(f"[私聊][{self.private_name}]检测到长时间({idle_time:.0f}秒)无对话，尝试主动发起聊天")
                    await self._initiate_conversation()
                    # 更新时间，避免连续触发
                    self.update_last_message_time()

                # 等待下一次检查
                await asyncio.sleep(self.idle_check_interval)

        except asyncio.CancelledError:
            logger.debug(f"[私聊][{self.private_name}]空闲对话检测任务被取消")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]空闲对话检测出错: {str(e)}")

    async def _initiate_conversation(self):
        """生成并发送主动对话内容"""
        try:
            # 获取聊天历史记录，用于生成更合适的开场白
            messages = self.chat_observer.get_cached_messages(limit=12)  # 获取最近12条消息
            chat_history_text = await build_readable_messages(
                messages,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
            )

            # 构建提示词
            prompt = f"""{self.personality_info}。你的名字是{self.name}。
            你正在与用户{self.private_name}进行QQ私聊，
            但已经有一段时间没有对话了。
            你想要主动发起一个友好的对话，可以说说自己在做的事情或者询问对方在做什么。
            请基于以下之前的对话历史，生成一条自然、友好、符合你个性的主动对话消息。
            这条消息应该能够引起用户的兴趣，重新开始对话。
            最近的对话历史（可能已经过去了很久）：
            {chat_history_text}
            请直接输出一条消息，不要有任何额外的解释或引导文字。消息要简短自然，就像是在日常聊天中的开场白。
            消息内容尽量简短，不要超过20个字，不要添加任何表情符号。
            """

            # 生成对话内容
            content, _ = await self.llm.generate_response_async(prompt)

            # 清理和格式化内容
            content = content.strip()
            # 移除可能的引号和多余格式
            content = content.strip('"\'')

            if content:
                # 通过PFCManager创建或获取一个新的对话实例
                # 使用局部导入避免循环依赖
                from .pfc_manager import PFCManager
                from src.plugins.chat.chat_stream import chat_manager

                # 获取当前实例
                pfc_manager = PFCManager.get_instance()

                # 结束当前对话实例（如果存在）
                current_conversation = await pfc_manager.get_conversation(self.stream_id)
                if current_conversation:
                    logger.info(f"[私聊][{self.private_name}]结束当前对话实例，准备创建新实例")
                    await current_conversation.stop()
                    await pfc_manager.remove_conversation(self.stream_id)

                # 创建新的对话实例
                logger.info(f"[私聊][{self.private_name}]创建新的对话实例以发送主动消息")
                new_conversation = await pfc_manager.get_or_create_conversation(self.stream_id, self.private_name)

                # 确保新对话实例已初始化完成
                if new_conversation and hasattr(new_conversation, 'should_continue'):
                    # 等待一小段时间，确保初始化完成
                    retry_count = 0
                    max_retries = 10  # 增加最大重试次数
                    while not new_conversation.should_continue and retry_count < max_retries:
                        await asyncio.sleep(0.5)  # 减少等待时间，0.5秒
                        retry_count += 1
                        logger.debug(
                            f"[私聊][{self.private_name}]等待新对话实例初始化完成: 尝试 {retry_count}/{max_retries}")

                    if not new_conversation.should_continue:
                        logger.warning(f"[私聊][{self.private_name}]新对话实例初始化可能未完成，但仍将尝试发送消息")

                # 首先尝试使用新对话实例的聊天流
                chat_stream = None
                if new_conversation and hasattr(new_conversation, 'chat_stream') and new_conversation.chat_stream:
                    logger.info(f"[私聊][{self.private_name}]使用新对话实例的聊天流发送消息")
                    chat_stream = new_conversation.chat_stream

                # 如果新对话实例没有可用的聊天流，则尝试从chat_manager获取
                if not chat_stream:
                    logger.info(f"[私聊][{self.private_name}]尝试从chat_manager获取聊天流")
                    chat_stream = chat_manager.get_stream(self.stream_id)

                # 如果仍然没有获取到聊天流，则创建一个新的
                if not chat_stream:
                    logger.warning(f"[私聊][{self.private_name}]无法获取聊天流，创建新的聊天流")
                    # 创建用户信息对象
                    user_info = UserInfo(
                        user_id=global_config.BOT_QQ,
                        user_nickname=global_config.BOT_NICKNAME,
                        platform="qq"
                    )
                    # 创建聊天流
                    chat_stream = ChatStream(self.stream_id, "qq", user_info)

                # 发送消息
                await self.message_sender.send_message(
                    chat_stream=chat_stream,
                    content=content,
                    reply_to_message=None
                )

                # 更新空闲会话启动器的最后消息时间
                self.update_last_message_time()

                # 如果新对话实例有一个聊天观察者，请触发更新
                if new_conversation and hasattr(new_conversation, 'chat_observer'):
                    logger.info(f"[私聊][{self.private_name}]触发聊天观察者更新")
                    new_conversation.chat_observer.trigger_update()

                logger.success(f"[私聊][{self.private_name}]成功主动发起对话: {content}")
            else:
                logger.error(f"[私聊][{self.private_name}]生成的主动对话内容为空")

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]主动发起对话失败: {str(e)}")

class GoalAnalyzer:
    """对话目标分析器"""

    def __init__(self, stream_id: str, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_normal, temperature=0.7, max_tokens=1000, request_type="conversation_goal"
        )

        self.personality_info = Individuality.get_instance().get_prompt(x_person=2, level=3)
        self.name = global_config.BOT_NICKNAME
        self.nick_name = global_config.BOT_ALIAS_NAMES
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)

        # 多目标存储结构
        self.goals = []  # 存储多个目标
        self.max_goals = 3  # 同时保持的最大目标数量
        self.current_goal_and_reason = None

    async def analyze_goal(self, conversation_info: ConversationInfo, observation_info: ObservationInfo):
        """分析对话历史并设定目标

        Args:
            conversation_info: 对话信息
            observation_info: 观察信息

        Returns:
            Tuple[str, str, str]: (目标, 方法, 原因)
        """
        # 构建对话目标
        goals_str = ""
        if conversation_info.goal_list:
            for goal_reason in conversation_info.goal_list:
                if isinstance(goal_reason, dict):
                    goal = goal_reason.get("goal", "目标内容缺失")
                    reasoning = goal_reason.get("reasoning", "没有明确原因")
                else:
                    goal = str(goal_reason)
                    reasoning = "没有明确原因"

                goal_str = f"目标：{goal}，产生该对话目标的原因：{reasoning}\n"
                goals_str += goal_str
        else:
            goal = "目前没有明确对话目标"
            reasoning = "目前没有明确对话目标，最好思考一个对话目标"
            goals_str = f"目标：{goal}，产生该对话目标的原因：{reasoning}\n"

        # 获取聊天历史记录
        chat_history_text = observation_info.chat_history_str

        if observation_info.new_messages_count > 0:
            new_messages_list = observation_info.unprocessed_messages
            new_messages_str = await build_readable_messages(
                new_messages_list,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
            )
            chat_history_text += f"\n--- 以下是 {observation_info.new_messages_count} 条新消息 ---\n{new_messages_str}"

            # await observation_info.clear_unprocessed_messages()

        persona_text = f"你的名字是{self.name}，{self.personality_info}。"
        # 构建action历史文本
        action_history_list = conversation_info.done_action
        action_history_text = "你之前做的事情是："
        for action in action_history_list:
            action_history_text += f"{action}\n"

        prompt = f"""{persona_text}。现在你在参与一场QQ聊天，请分析以下聊天记录，并根据你的性格特征确定多个明确的对话目标。
这些目标应该反映出对话的不同方面和意图。

{action_history_text}
当前对话目标：
{goals_str}

聊天记录：
{chat_history_text}

请分析当前对话并确定最适合的对话目标。你可以：
1. 保持现有目标不变
2. 修改现有目标
3. 添加新目标
4. 删除不再相关的目标
5. 如果你想结束对话，请设置一个目标，目标goal为"结束对话"，原因reasoning为你希望结束对话

请以JSON数组格式输出当前的所有对话目标，每个目标包含以下字段：
1. goal: 对话目标（简短的一句话）
2. reasoning: 对话原因，为什么设定这个目标（简要解释）

输出格式示例：
[
{{
    "goal": "回答用户关于Python编程的具体问题",
    "reasoning": "用户提出了关于Python的技术问题，需要专业且准确的解答"
}},
{{
    "goal": "回答用户关于python安装的具体问题",
    "reasoning": "用户提出了关于Python的技术问题，需要专业且准确的解答"
}}
]"""

        logger.debug(f"[私聊][{self.private_name}]发送到LLM的提示词: {prompt}")
        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]LLM原始返回内容: {content}")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]分析对话目标时出错: {str(e)}")
            content = ""

        # 使用改进后的get_items_from_json函数处理JSON数组
        success, result = get_items_from_json(
            content,
            self.private_name,
            "goal",
            "reasoning",
            required_types={"goal": str, "reasoning": str},
            allow_array=True,
        )

        if success:
            # 判断结果是单个字典还是字典列表
            if isinstance(result, list):
                # 清空现有目标列表并添加新目标
                conversation_info.goal_list = []
                for item in result:
                    conversation_info.goal_list.append(item)

                # 返回第一个目标作为当前主要目标（如果有）
                if result:
                    first_goal = result[0]
                    return first_goal.get("goal", ""), "", first_goal.get("reasoning", "")
            else:
                # 单个目标的情况
                conversation_info.goal_list.append(result)
                return goal, "", reasoning

        # 如果解析失败，返回默认值
        return "", "", ""

    async def _update_goals(self, new_goal: str, method: str, reasoning: str):
        """更新目标列表

        Args:
            new_goal: 新的目标
            method: 实现目标的方法
            reasoning: 目标的原因
        """
        # 检查新目标是否与现有目标相似
        for i, (existing_goal, _, _) in enumerate(self.goals):
            if _calculate_similarity(new_goal, existing_goal) > 0.7:  # 相似度阈值
                # 更新现有目标
                self.goals[i] = (new_goal, method, reasoning)
                # 将此目标移到列表前面（最主要的位置）
                self.goals.insert(0, self.goals.pop(i))
                return

        # 添加新目标到列表前面
        self.goals.insert(0, (new_goal, method, reasoning))

        # 限制目标数量
        if len(self.goals) > self.max_goals:
            self.goals.pop()  # 移除最老的目标

    async def get_all_goals(self) -> List[Tuple[str, str, str]]:
        """获取所有当前目标

        Returns:
            List[Tuple[str, str, str]]: 目标列表，每项为(目标, 方法, 原因)
        """
        return self.goals.copy()

    async def get_alternative_goals(self) -> List[Tuple[str, str, str]]:
        """获取除了当前主要目标外的其他备选目标

        Returns:
            List[Tuple[str, str, str]]: 备选目标列表
        """
        if len(self.goals) <= 1:
            return []
        return self.goals[1:].copy()

    async def analyze_conversation(self, goal, reasoning):
        messages = self.chat_observer.get_cached_messages()
        chat_history_text = await build_readable_messages(
            messages,
            replace_bot_name=True,
            merge_messages=False,
            timestamp_mode="relative",
            read_mark=0.0,
        )

        persona_text = f"你的名字是{self.name}，{self.personality_info}。"
        # ===> Persona 文本构建结束 <===

        # --- 修改 Prompt 字符串，使用 persona_text ---
        prompt = f"""{persona_text}。现在你在参与一场QQ聊天，
        当前对话目标：{goal}
        产生该对话目标的原因：{reasoning}
        
        请分析以下聊天记录，并根据你的性格特征评估该目标是否已经达到，或者你是否希望停止该次对话。
        聊天记录：
        {chat_history_text}
        请以JSON格式输出，包含以下字段：
        1. goal_achieved: 对话目标是否已经达到（true/false）
        2. stop_conversation: 是否希望停止该次对话（true/false）
        3. reason: 为什么希望停止该次对话（简要解释）   

输出格式示例：
{{
    "goal_achieved": true,
    "stop_conversation": false,
    "reason": "虽然目标已达成，但对话仍然有继续的价值"
}}"""

        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]LLM原始返回内容: {content}")

            # 尝试解析JSON
            success, result = get_items_from_json(
                content,
                self.private_name,
                "goal_achieved",
                "stop_conversation",
                "reason",
                required_types={"goal_achieved": bool, "stop_conversation": bool, "reason": str},
            )

            if not success:
                logger.error(f"[私聊][{self.private_name}]无法解析对话分析结果JSON")
                return False, False, "解析结果失败"

            goal_achieved = result["goal_achieved"]
            stop_conversation = result["stop_conversation"]
            reason = result["reason"]

            return goal_achieved, stop_conversation, reason

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]分析对话状态时出错: {str(e)}")
            return False, False, f"分析出错: {str(e)}"


# 先注释掉，万一以后出问题了还能开回来（（（
# class DirectMessageSender:
#     """直接发送消息到平台的发送器"""

#     def __init__(self, private_name: str):
#         self.logger = get_module_logger("direct_sender")
#         self.storage = MessageStorage()
#         self.private_name = private_name

#     async def send_via_ws(self, message: MessageSending) -> None:
#         try:
#             await global_api.send_message(message)
#         except Exception as e:
#             raise ValueError(f"未找到平台：{message.message_info.platform} 的url配置，请检查配置文件") from e

#     async def send_message(
#         self,
#         chat_stream: ChatStream,
#         content: str,
#         reply_to_message: Optional[Message] = None,
#     ) -> None:
#         """直接发送消息到平台

#         Args:
#             chat_stream: 聊天流
#             content: 消息内容
#             reply_to_message: 要回复的消息
#         """
#         # 构建消息对象
#         message_segment = Seg(type="text", data=content)
#         bot_user_info = UserInfo(
#             user_id=global_config.BOT_QQ,
#             user_nickname=global_config.BOT_NICKNAME,
#             platform=chat_stream.platform,
#         )

#         message = MessageSending(
#             message_id=f"dm{round(time.time(), 2)}",
#             chat_stream=chat_stream,
#             bot_user_info=bot_user_info,
#             sender_info=reply_to_message.message_info.user_info if reply_to_message else None,
#             message_segment=message_segment,
#             reply=reply_to_message,
#             is_head=True,
#             is_emoji=False,
#             thinking_start_time=time.time(),
#         )

#         # 处理消息
#         await message.process()

#         _message_json = message.to_dict()

#         # 发送消息
#         try:
#             await self.send_via_ws(message)
#             await self.storage.store_message(message, chat_stream)
#             logger.success(f"[私聊][{self.private_name}]PFC消息已发送: {content}")
#         except Exception as e:
#             logger.error(f"[私聊][{self.private_name}]PFC消息发送失败: {str(e)}")
