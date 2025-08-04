from src.chat.message_receive.chat_stream import get_chat_manager
import time
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config
from src.chat.message_receive.message import MessageRecvS4U
from src.mais4u.mais4u_chat.s4u_msg_processor import S4UMessageProcessor
from src.mais4u.mais4u_chat.internal_manager import internal_manager
from src.common.logger import get_logger

logger = get_logger(__name__)


def init_prompt():
    Prompt(
        """
你之前的内心想法是：{mind}

{memory_block}
{relation_info_block}

{chat_target}
{time_block}
{chat_info}
{identity}

你刚刚在{chat_target_2},你你刚刚的心情是：{mood_state}
---------------------
在这样的情况下，你对上面的内容，你对 {sender} 发送的 消息 “{target}” 进行了回复
你刚刚选择回复的内容是：{reponse}
现在，根据你之前的想法和回复的内容，推测你现在的想法，思考你现在的想法是什么，为什么做出上面的回复内容
请不要浮夸和夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出想法：""",
        "after_response_think_prompt",
    )


class MaiThinking:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.chat_stream = get_chat_manager().get_stream(chat_id)
        self.platform = self.chat_stream.platform

        if self.chat_stream.group_info:
            self.is_group = True
        else:
            self.is_group = False

        self.s4u_message_processor = S4UMessageProcessor()

        self.mind = ""

        self.memory_block = ""
        self.relation_info_block = ""
        self.time_block = ""
        self.chat_target = ""
        self.chat_target_2 = ""
        self.chat_info = ""
        self.mood_state = ""
        self.identity = ""
        self.sender = ""
        self.target = ""

        self.thinking_model = LLMRequest(model_set=model_config.model_task_config.replyer_1, request_type="thinking")

    async def do_think_before_response(self):
        pass

    async def do_think_after_response(self, reponse: str):
        prompt = await global_prompt_manager.format_prompt(
            "after_response_think_prompt",
            mind=self.mind,
            reponse=reponse,
            memory_block=self.memory_block,
            relation_info_block=self.relation_info_block,
            time_block=self.time_block,
            chat_target=self.chat_target,
            chat_target_2=self.chat_target_2,
            chat_info=self.chat_info,
            mood_state=self.mood_state,
            identity=self.identity,
            sender=self.sender,
            target=self.target,
        )

        result, _ = await self.thinking_model.generate_response_async(prompt)
        self.mind = result

        logger.info(f"[{self.chat_id}] 思考前想法：{self.mind}")
        # logger.info(f"[{self.chat_id}] 思考前prompt：{prompt}")
        logger.info(f"[{self.chat_id}] 思考后想法：{self.mind}")

        msg_recv = await self.build_internal_message_recv(self.mind)
        await self.s4u_message_processor.process_message(msg_recv)
        internal_manager.set_internal_state(self.mind)

    async def do_think_when_receive_message(self):
        pass

    async def build_internal_message_recv(self, message_text: str):
        msg_id = f"internal_{time.time()}"

        message_dict = {
            "message_info": {
                "message_id": msg_id,
                "time": time.time(),
                "user_info": {
                    "user_id": "internal",  # 内部用户ID
                    "user_nickname": "内心",  # 内部昵称
                    "platform": self.platform,  # 平台标记为 internal
                    # 其他 user_info 字段按需补充
                },
                "platform": self.platform,  # 平台
                # 其他 message_info 字段按需补充
            },
            "message_segment": {
                "type": "text",  # 消息类型
                "data": message_text,  # 消息内容
                # 其他 segment 字段按需补充
            },
            "raw_message": message_text,  # 原始消息内容
            "processed_plain_text": message_text,  # 处理后的纯文本
            # 下面这些字段可选，根据 MessageRecv 需要
            "is_emoji": False,
            "has_emoji": False,
            "is_picid": False,
            "has_picid": False,
            "is_voice": False,
            "is_mentioned": False,
            "is_command": False,
            "is_internal": True,
            "priority_mode": "interest",
            "priority_info": {"message_priority": 10.0},  # 内部消息可设高优先级
            "interest_value": 1.0,
        }

        if self.is_group:
            message_dict["message_info"]["group_info"] = {
                "platform": self.platform,
                "group_id": self.chat_stream.group_info.group_id,
                "group_name": self.chat_stream.group_info.group_name,
            }

        msg_recv = MessageRecvS4U(message_dict)
        msg_recv.chat_info = self.chat_info
        msg_recv.chat_stream = self.chat_stream
        msg_recv.is_internal = True

        return msg_recv


class MaiThinkingManager:
    def __init__(self):
        self.mai_think_list = []

    def get_mai_think(self, chat_id):
        for mai_think in self.mai_think_list:
            if mai_think.chat_id == chat_id:
                return mai_think
        mai_think = MaiThinking(chat_id)
        self.mai_think_list.append(mai_think)
        return mai_think


mai_thinking_manager = MaiThinkingManager()


init_prompt()
