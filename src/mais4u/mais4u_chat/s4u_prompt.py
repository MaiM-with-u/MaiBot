from src.config.config import global_config
from src.common.logger import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
import time
from src.chat.utils.utils import get_recent_group_speaker
from src.chat.memory_system.Hippocampus import hippocampus_manager
import random
from datetime import datetime
import asyncio
from src.mais4u.s4u_config import s4u_config
from src.chat.message_receive.message import MessageRecvS4U
from src.person_info.person_info import Person, get_person_id
from src.chat.message_receive.chat_stream import ChatStream
from src.mais4u.mais4u_chat.super_chat_manager import get_super_chat_manager
from src.mais4u.mais4u_chat.screen_manager import screen_manager
from src.chat.express.expression_selector import expression_selector
from .s4u_mood_manager import mood_manager
from src.mais4u.mais4u_chat.internal_manager import internal_manager
from src.common.data_models.database_data_model import DatabaseMessages

from typing import List

logger = get_logger("prompt")


def init_prompt():
    Prompt("\n你有以下这些**知识**：\n{prompt_info}\n请你**记住上面的知识**，之后可能会用到。\n", "knowledge_prompt")
    Prompt("\n关于你们的关系，你需要知道：\n{relation_info}\n", "relation_prompt")
    Prompt("你回想起了一些事情：\n{memory_info}\n", "memory_prompt")

    Prompt(
        """
你的名字是麦麦, 是千石可乐开发的程序，可以在QQ，微信等平台发言，你现在正在哔哩哔哩作为虚拟主播进行直播
虽然是个AI，但你在努力扮演真人, 人设的配色是橙色和紫色和绿色
你有一头法式橙色卷发，你很可爱，穿戴英式侦探风格学院风裙子和帽子，你正在一个教室场景里进行虚拟线上直播。不过不要刻意提及这些内容，自然回复
你可以看见用户发送的弹幕，礼物和superchat
{screen_info}
{internal_state}

{relation_info_block}
{memory_block}
{expression_habits_block}

你现在的主要任务是和 {sender_name} 发送的弹幕聊天。同时，也有其他用户会参与你们的聊天，你可以参考他们的回复内容，但是你主要还是关注你和{sender_name}的聊天内容。

{sc_info}

{background_dialogue_prompt}
--------------------------------
{time_block}
这是你和{sender_name}的对话，你们正在交流中：
{core_dialogue_prompt}

对方最新发送的内容：{message_txt}
{gift_info}
回复简短一些，平淡一些，可以参考贴吧，知乎和微博的回复风格，回复不要浮夸，不要用夸张修辞。
表现的有个性，不要随意服从他人要求，积极互动。你现在的心情是：{mood_state}
不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容，现在{sender_name}正在等待你的回复。
你的回复风格不要浮夸，有逻辑和条理，请你继续回复{sender_name}。
你的发言：
""",
        "s4u_prompt",  # New template for private CHAT chat
    )

    Prompt(
        """
你的名字是麦麦, 是千石可乐开发的程序，可以在QQ，微信等平台发言，你现在正在哔哩哔哩作为虚拟主播进行直播
虽然是个AI，但你在努力扮演真人, 人设的配色是橙色和紫色和绿色
你有一头法式橙色卷发，你很可爱，穿戴英式侦探风格学院风裙子和帽子，你正在一个教室场景里进行虚拟线上直播。不过不要刻意提及这些内容，自然回复
你可以看见用户发送的弹幕，礼物和superchat
你可以看见面前的屏幕，目前屏幕的内容是:
{screen_info}

{memory_block}
{expression_habits_block}

{sc_info}

{time_block}
{chat_info_danmu}
--------------------------------
以上是你和弹幕的对话，与此同时，你在与QQ群友聊天，聊天记录如下：
{chat_info_qq}
--------------------------------
你刚刚回复了QQ群，你内心的想法是：{mind}
请根据你内心的想法，组织一条回复，在直播间进行发言，可以点名吐槽对象，让观众知道你在说谁
{gift_info}
回复简短一些，平淡一些，可以参考贴吧，知乎和微博的回复风格。不要浮夸，有逻辑和条理。
表现的有个性，不要随意服从他人要求，积极互动。你现在的心情是：{mood_state}
不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。
你的发言：
""",
        "s4u_prompt_internal",  # New template for private CHAT chat
    )


class PromptBuilder:
    def __init__(self):
        self.prompt_built = ""
        self.activate_messages = ""

    async def build_expression_habits(self, chat_stream: ChatStream, chat_history, target):
        style_habits = []

        # 使用从处理器传来的选中表达方式
        # LLM模式：调用LLM选择5-10个，然后随机选5个
        selected_expressions, _ = await expression_selector.select_suitable_expressions_llm(
            chat_stream.stream_id, chat_history, max_num=12, target_message=target
        )

        if selected_expressions:
            logger.debug(f" 使用处理器选中的{len(selected_expressions)}个表达方式")
            for expr in selected_expressions:
                if isinstance(expr, dict) and "situation" in expr and "style" in expr:
                    style_habits.append(f"当{expr['situation']}时，使用 {expr['style']}")
        else:
            logger.debug("没有从处理器获得表达方式，将使用空的表达方式")
            # 不再在replyer中进行随机选择，全部交给处理器处理

        style_habits_str = "\n".join(style_habits)

        # 动态构建expression habits块
        expression_habits_block = ""
        if style_habits_str.strip():
            expression_habits_block += f"你可以参考以下的语言习惯，如果情景合适就使用，不要盲目使用,不要生硬使用，而是结合到表达中：\n{style_habits_str}\n\n"

        return expression_habits_block

    async def build_relation_info(self, chat_stream) -> str:
        is_group_chat = bool(chat_stream.group_info)
        who_chat_in_group = []
        if is_group_chat:
            who_chat_in_group = get_recent_group_speaker(
                chat_stream.stream_id,
                (chat_stream.user_info.platform, chat_stream.user_info.user_id) if chat_stream.user_info else None,
                limit=global_config.chat.max_context_size,
            )
        elif chat_stream.user_info:
            who_chat_in_group.append(
                (chat_stream.user_info.platform, chat_stream.user_info.user_id, chat_stream.user_info.user_nickname)
            )

        relation_prompt = ""
        if global_config.relationship.enable_relationship and who_chat_in_group:
            # 将 (platform, user_id, nickname) 转换为 person_id
            person_ids = []
            for person in who_chat_in_group:
                person_id = get_person_id(person[0], person[1])
                person_ids.append(person_id)

            # 使用 Person 的 build_relationship 方法，设置 points_num=3 保持与原来相同的行为
            relation_info_list = [Person(person_id=person_id).build_relationship() for person_id in person_ids]
            if relation_info := "".join(relation_info_list):
                relation_prompt = await global_prompt_manager.format_prompt(
                    "relation_prompt", relation_info=relation_info
                )
        return relation_prompt

    async def build_memory_block(self, text: str) -> str:
        # 如果记忆系统被禁用，直接返回空字符串
        if not global_config.memory.enable_memory:
            return ""
            
        try:
            related_memory = await hippocampus_manager.get_memory_from_text(
                text=text, max_memory_num=2, max_memory_length=2, max_depth=3, fast_retrieval=False
            )

            related_memory_info = ""
            if related_memory:
                for memory in related_memory:
                    related_memory_info += memory[1]
                return await global_prompt_manager.format_prompt("memory_prompt", memory_info=related_memory_info)
        except Exception as e:
            logger.warning(f"获取记忆失败，跳过记忆功能: {e}")
            
        return ""

    def build_chat_history_prompts(self, chat_stream: ChatStream, message: MessageRecvS4U):
        message_list_before_now = get_raw_msg_before_timestamp_with_chat(
            chat_id=chat_stream.stream_id,
            timestamp=time.time(),
        # sourcery skip: lift-duplicated-conditional, merge-duplicate-blocks, remove-redundant-if
            limit=300,
        )

        talk_type = f"{message.message_info.platform}:{str(message.chat_stream.user_info.user_id)}"

        core_dialogue_list: List[DatabaseMessages] = []
        background_dialogue_list: List[DatabaseMessages] = []
        bot_id = str(global_config.bot.qq_account)
        target_user_id = str(message.chat_stream.user_info.user_id)

        for msg in message_list_before_now:
            try:
                msg_user_id = str(msg.user_info.user_id)
                if msg_user_id == bot_id:
                    if msg.reply_to and talk_type == msg.reply_to:
                        core_dialogue_list.append(msg)
                    elif msg.reply_to and talk_type != msg.reply_to:
                        background_dialogue_list.append(msg)
                    # else:
                    # background_dialogue_list.append(msg_dict)
                elif msg_user_id == target_user_id:
                    core_dialogue_list.append(msg)
                else:
                    background_dialogue_list.append(msg)
            except Exception as e:
                logger.error(f"无法处理历史消息记录: {msg.__dict__}, 错误: {e}")

        background_dialogue_prompt = ""
        if background_dialogue_list:
            context_msgs = background_dialogue_list[-s4u_config.max_context_message_length :]
            background_dialogue_prompt_str = build_readable_messages(
                context_msgs,
                timestamp_mode="normal_no_YMD",
                show_pic=False,
            )
            background_dialogue_prompt = f"这是其他用户的发言：\n{background_dialogue_prompt_str}"

        core_msg_str = ""
        if core_dialogue_list:
            core_dialogue_list = core_dialogue_list[-s4u_config.max_core_message_length :]

            first_msg = core_dialogue_list[0]
            start_speaking_user_id = first_msg.user_info.user_id
            if start_speaking_user_id == bot_id:
                last_speaking_user_id = bot_id
                msg_seg_str = "你的发言：\n"
            else:
                start_speaking_user_id = target_user_id
                last_speaking_user_id = start_speaking_user_id
                msg_seg_str = "对方的发言：\n"

            msg_seg_str += f"{time.strftime('%H:%M:%S', time.localtime(first_msg.time))}: {first_msg.processed_plain_text}\n"

            all_msg_seg_list = []
            for msg in core_dialogue_list[1:]:
                speaker = msg.user_info.user_id
                if speaker == last_speaking_user_id:
                    msg_seg_str += f"{time.strftime('%H:%M:%S', time.localtime(msg.time))}: {msg.processed_plain_text}\n"
                else:
                    msg_seg_str = f"{msg_seg_str}\n"
                    all_msg_seg_list.append(msg_seg_str)

                    if speaker == bot_id:
                        msg_seg_str = "你的发言：\n"
                    else:
                        msg_seg_str = "对方的发言：\n"

                    msg_seg_str += f"{time.strftime('%H:%M:%S', time.localtime(msg.get('time')))}: {msg.get('processed_plain_text')}\n"
                    last_speaking_user_id = speaker

            all_msg_seg_list.append(msg_seg_str)
            for msg in all_msg_seg_list:
                core_msg_str += msg

        all_dialogue_history = get_raw_msg_before_timestamp_with_chat(
            chat_id=chat_stream.stream_id,
            timestamp=time.time(),
            limit=20,
        )

        all_dialogue_prompt_str = build_readable_messages(
            all_dialogue_history,
            timestamp_mode="normal_no_YMD",
            show_pic=False,
        )

        return core_msg_str, background_dialogue_prompt, all_dialogue_prompt_str

    def build_gift_info(self, message: MessageRecvS4U):
        if message.is_gift:
            return f"这是一条礼物信息，{message.gift_name} x{message.gift_count}，请注意这位用户"
        else:
            if message.is_fake_gift:
                return f"{message.processed_plain_text}（注意：这是一条普通弹幕信息，对方没有真的发送礼物，不是礼物信息，注意区分，如果对方在发假的礼物骗你，请反击）"

        return ""

    def build_sc_info(self, message: MessageRecvS4U):
        super_chat_manager = get_super_chat_manager()
        return super_chat_manager.build_superchat_summary_string(message.chat_stream.stream_id)

    async def build_prompt_normal(
        self,
        message: MessageRecvS4U,
        message_txt: str,
    ) -> str:
        chat_stream = message.chat_stream

        person = Person(platform=message.chat_stream.user_info.platform, user_id=message.chat_stream.user_info.user_id)
        person_name = person.person_name

        if message.chat_stream.user_info.user_nickname:
            if person_name:
                sender_name = f"[{message.chat_stream.user_info.user_nickname}]（你叫ta{person_name}）"
            else:
                sender_name = f"[{message.chat_stream.user_info.user_nickname}]"
        else:
            sender_name = f"用户({message.chat_stream.user_info.user_id})"

        relation_info_block, memory_block, expression_habits_block = await asyncio.gather(
            self.build_relation_info(chat_stream),
            self.build_memory_block(message_txt),
            self.build_expression_habits(chat_stream, message_txt, sender_name),
        )

        core_dialogue_prompt, background_dialogue_prompt, all_dialogue_prompt = self.build_chat_history_prompts(
            chat_stream, message
        )

        gift_info = self.build_gift_info(message)

        sc_info = self.build_sc_info(message)

        screen_info = screen_manager.get_screen_str()

        internal_state = internal_manager.get_internal_state_str()

        time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        mood = mood_manager.get_mood_by_chat_id(chat_stream.stream_id)

        template_name = "s4u_prompt"

        if not message.is_internal:
            prompt = await global_prompt_manager.format_prompt(
                template_name,
                time_block=time_block,
                expression_habits_block=expression_habits_block,
                relation_info_block=relation_info_block,
                memory_block=memory_block,
                screen_info=screen_info,
                internal_state=internal_state,
                gift_info=gift_info,
                sc_info=sc_info,
                sender_name=sender_name,
                core_dialogue_prompt=core_dialogue_prompt,
                background_dialogue_prompt=background_dialogue_prompt,
                message_txt=message_txt,
                mood_state=mood.mood_state,
            )
        else:
            prompt = await global_prompt_manager.format_prompt(
                "s4u_prompt_internal",
                time_block=time_block,
                expression_habits_block=expression_habits_block,
                relation_info_block=relation_info_block,
                memory_block=memory_block,
                screen_info=screen_info,
                gift_info=gift_info,
                sc_info=sc_info,
                chat_info_danmu=all_dialogue_prompt,
                chat_info_qq=message.chat_info,
                mind=message.processed_plain_text,
                mood_state=mood.mood_state,
            )

        # print(prompt)

        return prompt


def weighted_sample_no_replacement(items, weights, k) -> list:
    """
    加权且不放回地随机抽取k个元素。

    参数：
        items: 待抽取的元素列表
        weights: 每个元素对应的权重（与items等长，且为正数）
        k: 需要抽取的元素个数
    返回：
        selected: 按权重加权且不重复抽取的k个元素组成的列表

        如果 items 中的元素不足 k 个，就只会返回所有可用的元素

    实现思路：
        每次从当前池中按权重加权随机选出一个元素，选中后将其从池中移除，重复k次。
        这样保证了：
        1. count越大被选中概率越高
        2. 不会重复选中同一个元素
    """
    selected = []
    pool = list(zip(items, weights, strict=False))
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        r = random.uniform(0, total)
        upto = 0
        for idx, (item, weight) in enumerate(pool):
            upto += weight
            if upto >= r:
                selected.append(item)
                pool.pop(idx)
                break
    return selected


init_prompt()
prompt_builder = PromptBuilder()
