from typing import Tuple
from src.common.logger import get_module_logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from .reply_checker import ReplyChecker
from src.individuality.individuality import Individuality
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo

logger = get_module_logger("reply_generator")


class ReplyGenerator:
    """回复生成器"""

    def __init__(self, stream_id: str):
        self.llm = LLMRequest(
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=300,
            request_type="reply_generation",
        )
        self.personality_info = Individuality.get_instance().get_prompt(type="personality", x_person=2, level=2)
        self.name = global_config.BOT_NICKNAME
        self.chat_observer = ChatObserver.get_instance(stream_id)
        self.reply_checker = ReplyChecker(stream_id)

    async def generate(self, observation_info: ObservationInfo, conversation_info: ConversationInfo) -> str:
        """生成回复

        Args:
            observation_info: 观察信息
            conversation_info: 对话信息

        Returns:
            str: 生成的回复
        """
        # 构建提示词
        logger.debug(f"开始生成回复：当前目标: {conversation_info.goal_list}")

        # 构建对话目标
        goals_str = ""
        if conversation_info.goal_list:
            for goal_reason in conversation_info.goal_list:
                # 处理字典或元组格式
                if isinstance(goal_reason, tuple):
                    # 假设元组的第一个元素是目标，第二个元素是原因
                    goal = goal_reason[0]
                    reasoning = goal_reason[1] if len(goal_reason) > 1 else "没有明确原因"
                elif isinstance(goal_reason, dict):
                    goal = goal_reason.get("goal")
                    reasoning = goal_reason.get("reasoning", "没有明确原因")
                else:
                    # 如果是其他类型，尝试转为字符串
                    goal = str(goal_reason)
                    reasoning = "没有明确原因"

                goal_str = f"目标：{goal}，产生该对话目标的原因：{reasoning}\n"
                goals_str += goal_str
        else:
            goal = "目前没有明确对话目标"
            reasoning = "目前没有明确对话目标，最好思考一个对话目标"
            goals_str = f"目标：{goal}，产生该对话目标的原因：{reasoning}\n"

        # 获取聊天历史记录
        chat_history_list = (
            observation_info.chat_history[-20:]
            if len(observation_info.chat_history) >= 20
            else observation_info.chat_history
        )
        chat_history_text = ""
        for msg in chat_history_list:
            chat_history_text += f"{msg.get('detailed_plain_text', '')}\n"

        if observation_info.new_messages_count > 0:
            new_messages_list = observation_info.unprocessed_messages

            chat_history_text += f"有{observation_info.new_messages_count}条新消息：\n"
            for msg in new_messages_list:
                chat_history_text += f"{msg.get('detailed_plain_text', '')}\n"

            observation_info.clear_unprocessed_messages()

        personality_text = f"你的名字是{self.name}，{self.personality_info}"

        # 构建action历史文本
        action_history_list = (
            conversation_info.done_action[-10:]
            if len(conversation_info.done_action) >= 10
            else conversation_info.done_action
        )
        action_history_text = "你之前做的事情是："
        for action in action_history_list:
            if isinstance(action, dict):
                action_type = action.get("action")
                action_reason = action.get("reason")
                action_status = action.get("status")
                if action_status == "recall":
                    action_history_text += (
                        f"原本打算：{action_type}，但是因为有新消息，你发现这个行动不合适，所以你没做\n"
                    )
                elif action_status == "done":
                    action_history_text += f"你之前做了：{action_type}，原因：{action_reason}\n"
            elif isinstance(action, tuple):
                # 假设元组的格式是(action_type, action_reason, action_status)
                action_type = action[0] if len(action) > 0 else "未知行动"
                action_reason = action[1] if len(action) > 1 else "未知原因"
                action_status = action[2] if len(action) > 2 else "done"
                if action_status == "recall":
                    action_history_text += (
                        f"原本打算：{action_type}，但是因为有新消息，你发现这个行动不合适，所以你没做\n"
                    )
                elif action_status == "done":
                    action_history_text += f"你之前做了：{action_type}，原因：{action_reason}\n"

        prompt = f"""{personality_text}。现在你在参与一场QQ聊天，请根据以下信息生成回复：

当前对话目标：{goals_str}
最近的聊天记录：
{chat_history_text}


请根据上述信息，以你的性格特征生成一个自然、得体的回复。回复应该：
1. 符合对话目标，以"你"的角度发言
2. 体现你的性格特征
3. 自然流畅，像正常聊天一样，简短
4. 适当利用相关知识，但不要生硬引用

请注意把握聊天内容，不要回复的太有条理，可以有个性。请分清"你"和对方说的话，不要把"你"说的话当做对方说的话，这是你自己说的话。
请你回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，尽量不要说你说过的话 
请你注意不要输出多余内容(包括前后缀，冒号和引号，括号，表情等)，只输出回复内容。
不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )。

请直接输出回复内容，不需要任何额外格式。"""

        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.info(f"生成的回复: {content}")
            # is_new = self.chat_observer.check()
            # logger.debug(f"再看一眼聊天记录，{'有' if is_new else '没有'}新消息")

            # 如果有新消息,重新生成回复
            # if is_new:
            #     logger.info("检测到新消息,重新生成回复")
            #     return await self.generate(
            #         goal, chat_history, knowledge_cache,
            #         None, retry_count
            #     )

            return content

        except Exception as e:
            logger.error(f"生成回复时出错: {str(e)}")
            return "抱歉，我现在有点混乱，让我重新思考一下..."

    async def check_reply(self, reply: str, goal: str, retry_count: int = 0) -> Tuple[bool, str, bool]:
        """检查回复是否合适

        Args:
            reply: 生成的回复
            goal: 对话目标
            retry_count: 当前重试次数

        Returns:
            Tuple[bool, str, bool]: (是否合适, 原因, 是否需要重新规划)
        """
        return await self.reply_checker.check(reply, goal, retry_count)
