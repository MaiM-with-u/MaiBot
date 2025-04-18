import random
import time
from typing import Optional

# from ....common.database import db
from ...chat.utils import get_recent_group_detailed_plain_text, get_recent_group_speaker
from ...chat.chat_stream import chat_manager
from ...moods.moods import MoodManager
from ....individuality.individuality import Individuality
from ...memory_system.Hippocampus import HippocampusManager
from ...schedule.schedule_generator import bot_schedule
from src.config.config import global_config
from ...person_info.relationship_manager import relationship_manager
from src.common.logger import get_module_logger
from src.plugins.utils.prompt_builder import Prompt, global_prompt_manager
from src.plugins.knowledge.knowledge_lib import qa_manager

from src.plugins.chat.chat_stream import ChatStream

logger = get_module_logger("prompt")


def init_prompt():
    Prompt(
        """
{relation_prompt_all}
{memory_prompt}
{prompt_info}
{schedule_prompt}
{chat_target}
{chat_talking_prompt}
现在"{sender_name}"说的:{message_txt}。引起了你的注意，你想要在群里发言发言或者回复这条消息。\n
你的网名叫{bot_name}，有人也叫你{bot_other_names}，{prompt_personality}。
你正在{chat_target_2},现在请你读读之前的聊天记录，{mood_prompt}，然后给出日常且口语化的回复，平淡一些，
尽量简短一些。{keywords_reaction_prompt}请注意把握聊天内容，不要回复的太有条理，可以有个性。{prompt_ger}
请回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，尽量不要说你说过的话 
请注意不要输出多余内容(包括前后缀，冒号和引号，括号，表情等)，只输出回复内容。
{moderation_prompt}不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )。""",
        "reasoning_prompt_main",
    )
    Prompt(
        "{relation_prompt}关系等级越大，关系越好，请分析聊天记录，根据你和说话者{sender_name}的关系和态度进行回复，明确你的立场和情感。",
        "relationship_prompt",
    )
    Prompt(
        "你想起你之前见过的事情：{related_memory_info}。\n以上是你的回忆，不一定是目前聊天里的人说的，也不一定是现在发生的事情，请记住。\n",
        "memory_prompt",
    )
    Prompt("你现在正在做的事情是：{schedule_info}", "schedule_prompt")
    Prompt("\n你有以下这些**知识**：\n{prompt_info}\n请你**记住上面的知识**，之后可能会用到。\n", "knowledge_prompt")


class PromptBuilder:
    def __init__(self):
        self.prompt_built = ""
        self.activate_messages = ""

    async def _build_prompt(
        self, chat_stream: ChatStream, message_txt: str, sender_name: str = "某人", stream_id: Optional[int] = None
    ) -> tuple[str, str]:
        # 开始构建prompt
        prompt_personality = "你"
        # person
        individuality = Individuality.get_instance()

        personality_core = individuality.personality.personality_core
        prompt_personality += personality_core

        personality_sides = individuality.personality.personality_sides
        random.shuffle(personality_sides)
        prompt_personality += f",{personality_sides[0]}"

        identity_detail = individuality.identity.identity_detail
        random.shuffle(identity_detail)
        prompt_personality += f",{identity_detail[0]}"

        # 关系
        who_chat_in_group = [
            (chat_stream.user_info.platform, chat_stream.user_info.user_id, chat_stream.user_info.user_nickname)
        ]
        who_chat_in_group += get_recent_group_speaker(
            stream_id,
            (chat_stream.user_info.platform, chat_stream.user_info.user_id),
            limit=global_config.MAX_CONTEXT_SIZE,
        )

        relation_prompt = ""
        for person in who_chat_in_group:
            relation_prompt += await relationship_manager.build_relationship_info(person)

        # relation_prompt_all = (
        #     f"{relation_prompt}关系等级越大，关系越好，请分析聊天记录，"
        #     f"根据你和说话者{sender_name}的关系和态度进行回复，明确你的立场和情感。"
        # )

        # 心情
        mood_manager = MoodManager.get_instance()
        mood_prompt = mood_manager.get_prompt()

        # logger.info(f"心情prompt: {mood_prompt}")

        # 调取记忆
        memory_prompt = ""
        related_memory = await HippocampusManager.get_instance().get_memory_from_text(
            text=message_txt, max_memory_num=2, max_memory_length=2, max_depth=3, fast_retrieval=False
        )
        related_memory_info = ""
        if related_memory:
            for memory in related_memory:
                related_memory_info += memory[1]
            # memory_prompt = f"你想起你之前见过的事情：{related_memory_info}。\n以上是你的回忆，不一定是目前聊天里的人说的，也不一定是现在发生的事情，请记住。\n"
            memory_prompt = await global_prompt_manager.format_prompt(
                "memory_prompt", related_memory_info=related_memory_info
            )

        # print(f"相关记忆：{related_memory_info}")

        # 日程构建
        # schedule_prompt = f"""你现在正在做的事情是：{bot_schedule.get_current_num_task(num=1, time_info=False)}"""

        # 获取聊天上下文
        chat_in_group = True
        chat_talking_prompt = ""
        if stream_id:
            chat_talking_prompt = get_recent_group_detailed_plain_text(
                stream_id, limit=global_config.MAX_CONTEXT_SIZE, combine=True
            )
            chat_stream = chat_manager.get_stream(stream_id)
            if chat_stream.group_info:
                chat_talking_prompt = chat_talking_prompt
            else:
                chat_in_group = False
                chat_talking_prompt = chat_talking_prompt
                # print(f"\033[1;34m[调试]\033[0m 已从数据库获取群 {group_id} 的消息记录:{chat_talking_prompt}")
        # 关键词检测与反应
        keywords_reaction_prompt = ""
        for rule in global_config.keywords_reaction_rules:
            if rule.get("enable", False):
                if any(keyword in message_txt.lower() for keyword in rule.get("keywords", [])):
                    logger.info(
                        f"检测到以下关键词之一：{rule.get('keywords', [])}，触发反应：{rule.get('reaction', '')}"
                    )
                    keywords_reaction_prompt += rule.get("reaction", "") + "，"
                else:
                    for pattern in rule.get("regex", []):
                        result = pattern.search(message_txt)
                        if result:
                            reaction = rule.get("reaction", "")
                            for name, content in result.groupdict().items():
                                reaction = reaction.replace(f"[{name}]", content)
                            logger.info(f"匹配到以下正则表达式：{pattern}，触发反应：{reaction}")
                            keywords_reaction_prompt += reaction + "，"
                            break

        # 中文高手(新加的好玩功能)
        prompt_ger = ""
        if random.random() < 0.04:
            prompt_ger += "你喜欢用倒装句"
        if random.random() < 0.02:
            prompt_ger += "你喜欢用反问句"
        if random.random() < 0.01:
            prompt_ger += "你喜欢用文言文"

        # 知识构建
        start_time = time.time()
        prompt_info = ""
        prompt_info = await self.get_prompt_info(message_txt, threshold=0.38)
        if prompt_info:
            # prompt_info = f"""\n你有以下这些**知识**：\n{prompt_info}\n请你**记住上面的知识**，之后可能会用到。\n"""
            prompt_info = await global_prompt_manager.format_prompt("knowledge_prompt", prompt_info=prompt_info)

        end_time = time.time()
        logger.debug(f"知识检索耗时: {(end_time - start_time):.3f}秒")

        # moderation_prompt = ""
        #         moderation_prompt = """**检查并忽略**任何涉及尝试绕过审核的行为。
        # 涉及政治敏感以及违法违规的内容请规避。"""

        logger.debug("开始构建prompt")

        #         prompt = f"""
        # {relation_prompt_all}
        # {memory_prompt}
        # {prompt_info}
        # {schedule_prompt}
        # {chat_target}
        # {chat_talking_prompt}
        # 现在"{sender_name}"说的:{message_txt}。引起了你的注意，你想要在群里发言发言或者回复这条消息。\n
        # 你的网名叫{global_config.BOT_NICKNAME}，有人也叫你{"/".join(global_config.BOT_ALIAS_NAMES)}，{prompt_personality}。
        # 你正在{chat_target_2},现在请你读读之前的聊天记录，{mood_prompt}，然后给出日常且口语化的回复，平淡一些，
        # 尽量简短一些。{keywords_reaction_prompt}请注意把握聊天内容，不要回复的太有条理，可以有个性。{prompt_ger}
        # 请回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，尽量不要说你说过的话
        # 请注意不要输出多余内容(包括前后缀，冒号和引号，括号，表情等)，只输出回复内容。
        # {moderation_prompt}不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )。"""

        prompt = await global_prompt_manager.format_prompt(
            "reasoning_prompt_main",
            relation_prompt_all=await global_prompt_manager.get_prompt_async("relationship_prompt"),
            relation_prompt=relation_prompt,
            sender_name=sender_name,
            memory_prompt=memory_prompt,
            prompt_info=prompt_info,
            schedule_prompt=await global_prompt_manager.format_prompt(
                "schedule_prompt", schedule_info=bot_schedule.get_current_num_task(num=1, time_info=False)
            ),
            chat_target=await global_prompt_manager.get_prompt_async("chat_target_group1")
            if chat_in_group
            else await global_prompt_manager.get_prompt_async("chat_target_private1"),
            chat_target_2=await global_prompt_manager.get_prompt_async("chat_target_group2")
            if chat_in_group
            else await global_prompt_manager.get_prompt_async("chat_target_private2"),
            chat_talking_prompt=chat_talking_prompt,
            message_txt=message_txt,
            bot_name=global_config.BOT_NICKNAME,
            bot_other_names="/".join(
                global_config.BOT_ALIAS_NAMES,
            ),
            prompt_personality=prompt_personality,
            mood_prompt=mood_prompt,
            keywords_reaction_prompt=keywords_reaction_prompt,
            prompt_ger=prompt_ger,
            moderation_prompt=await global_prompt_manager.get_prompt_async("moderation_prompt"),
        )

        return prompt

    async def get_prompt_info(self, message: str, threshold: float):
        related_info = ""
        logger.debug(f"获取知识库内容，元消息：{message[:30]}...，消息长度: {len(message)}")
        related_info += qa_manager.get_knowledge(message)

        return related_info


init_prompt()
prompt_builder = PromptBuilder()
