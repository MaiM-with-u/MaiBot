import random
import time
import re
from collections import Counter
from typing import Dict, List

import jieba
import numpy as np
from src.common.logger import get_module_logger

from ..models.utils_model import LLMRequest
from ..utils.typo_generator import ChineseTypoGenerator
from ...config.config import global_config
from .message import MessageRecv, Message
from ..message.message_base import UserInfo
from .chat_stream import ChatStream
from ..moods.moods import MoodManager
from ...common.database import db


logger = get_module_logger("chat_utils")


def db_message_to_str(message_dict: Dict) -> str:
    logger.debug(f"message_dict: {message_dict}")
    time_str = time.strftime("%m-%d %H:%M:%S", time.localtime(message_dict["time"]))
    try:
        name = "[(%s)%s]%s" % (
            message_dict["user_id"],
            message_dict.get("user_nickname", ""),
            message_dict.get("user_cardname", ""),
        )
    except Exception:
        name = message_dict.get("user_nickname", "") or f"用户{message_dict['user_id']}"
    content = message_dict.get("processed_plain_text", "")
    result = f"[{time_str}] {name}: {content}\n"
    logger.debug(f"result: {result}")
    return result


def is_mentioned_bot_in_message(message: MessageRecv) -> tuple[bool, float]:
    """检查消息是否提到了机器人"""
    keywords = [global_config.BOT_NICKNAME]
    nicknames = global_config.BOT_ALIAS_NAMES
    reply_probability = 0.0
    is_at = False
    is_mentioned = False

    if (
        message.message_info.additional_config is not None
        and message.message_info.additional_config.get("is_mentioned") is not None
    ):
        try:
            reply_probability = float(message.message_info.additional_config.get("is_mentioned"))
            is_mentioned = True
            return is_mentioned, reply_probability
        except Exception as e:
            logger.warning(e)
            logger.warning(
                f"消息中包含不合理的设置 is_mentioned: {message.message_info.additional_config.get('is_mentioned')}"
            )

    # 判断是否被@
    if re.search(f"@[\s\S]*?（id:{global_config.BOT_QQ}）", message.processed_plain_text):
        is_at = True
        is_mentioned = True

    if is_at and global_config.at_bot_inevitable_reply:
        reply_probability = 1.0
        logger.info("被@，回复概率设置为100%")
    else:
        if not is_mentioned:
            # 判断是否被回复
            if re.match(f"回复[\s\S]*?\({global_config.BOT_QQ}\)的消息，说：", message.processed_plain_text):
                is_mentioned = True

            # 判断内容中是否被提及
            message_content = re.sub(r"@[\s\S]*?（(\d+)）", "", message.processed_plain_text)
            message_content = re.sub(r"回复[\s\S]*?\((\d+)\)的消息，说： ", "", message_content)
            for keyword in keywords:
                if keyword in message_content:
                    is_mentioned = True
            for nickname in nicknames:
                if nickname in message_content:
                    is_mentioned = True
        if is_mentioned and global_config.mentioned_bot_inevitable_reply:
            reply_probability = 1.0
            logger.info("被提及，回复概率设置为100%")
    return is_mentioned, reply_probability


async def get_embedding(text, request_type="embedding"):
    """获取文本的embedding向量"""
    llm = LLMRequest(model=global_config.embedding, request_type=request_type)
    # return llm.get_embedding_sync(text)
    try:
        embedding = await llm.get_embedding(text)
    except Exception as e:
        logger.error(f"获取embedding失败: {str(e)}")
        embedding = None
    return embedding


async def get_recent_group_messages(chat_id: str, limit: int = 12) -> list:
    """从数据库获取群组最近的消息记录

    Args:
        chat_id: 群组ID
        limit: 获取消息数量，默认12条

    Returns:
        list: Message对象列表，按时间正序排列
    """

    # 从数据库获取最近消息
    recent_messages = list(
        db.messages.find(
            {"chat_id": chat_id},
        )
        .sort("time", -1)
        .limit(limit)
    )

    if not recent_messages:
        return []

    # 转换为 Message对象列表
    message_objects = []
    for msg_data in recent_messages:
        try:
            chat_info = msg_data.get("chat_info", {})
            chat_stream = ChatStream.from_dict(chat_info)
            user_info = msg_data.get("user_info", {})
            user_info = UserInfo.from_dict(user_info)
            msg = Message(
                message_id=msg_data["message_id"],
                chat_stream=chat_stream,
                timestamp=msg_data["time"],
                user_info=user_info,
                processed_plain_text=msg_data.get("processed_text", ""),
                detailed_plain_text=msg_data.get("detailed_plain_text", ""),
            )
            message_objects.append(msg)
        except KeyError:
            logger.warning("数据库中存在无效的消息")
            continue

    # 按时间正序排列
    message_objects.reverse()
    return message_objects


def get_recent_group_detailed_plain_text(chat_stream_id: int, limit: int = 12, combine=False):
    recent_messages = list(
        db.messages.find(
            {"chat_id": chat_stream_id},
            {
                "time": 1,  # 返回时间字段
                "chat_id": 1,
                "chat_info": 1,
                "user_info": 1,
                "message_id": 1,  # 返回消息ID字段
                "detailed_plain_text": 1,  # 返回处理后的文本字段
            },
        )
        .sort("time", -1)
        .limit(limit)
    )

    if not recent_messages:
        return []

    message_detailed_plain_text = ""
    message_detailed_plain_text_list = []

    # 反转消息列表，使最新的消息在最后
    recent_messages.reverse()

    if combine:
        for msg_db_data in recent_messages:
            message_detailed_plain_text += str(msg_db_data["detailed_plain_text"])
        return message_detailed_plain_text
    else:
        for msg_db_data in recent_messages:
            message_detailed_plain_text_list.append(msg_db_data["detailed_plain_text"])
        return message_detailed_plain_text_list


def get_recent_group_speaker(chat_stream_id: int, sender, limit: int = 12) -> list:
    # 获取当前群聊记录内发言的人
    recent_messages = list(
        db.messages.find(
            {"chat_id": chat_stream_id},
            {
                "user_info": 1,
            },
        )
        .sort("time", -1)
        .limit(limit)
    )

    if not recent_messages:
        return []

    who_chat_in_group = []
    for msg_db_data in recent_messages:
        user_info = UserInfo.from_dict(msg_db_data["user_info"])
        if (
            (user_info.platform, user_info.user_id) != sender
            and user_info.user_id != global_config.BOT_QQ
            and (user_info.platform, user_info.user_id, user_info.user_nickname) not in who_chat_in_group
            and len(who_chat_in_group) < 5
        ):  # 排除重复，排除消息发送者，排除bot，限制加载的关系数目
            who_chat_in_group.append((user_info.platform, user_info.user_id, user_info.user_nickname))

    return who_chat_in_group


def split_into_sentences_w_remove_punctuation(text: str) -> List[str]:
    """将文本分割成句子，但保持书名号中的内容完整
    Args:
        text: 要分割的文本字符串
    Returns:
        List[str]: 分割后的句子列表
    """
    len_text = len(text)
    if len_text < 4:
        if random.random() < 0.01:
            return list(text)  # 如果文本很短且触发随机条件,直接按字符分割
        else:
            return [text]
    if len_text < 12:
        split_strength = 0.2
    elif len_text < 32:
        split_strength = 0.6
    else:
        split_strength = 0.7

    # 检查是否为西文字符段落
    if not is_western_paragraph(text):
        # 当语言为中文时，统一将英文逗号转换为中文逗号
        text = text.replace(",", "，")
        text = text.replace("\n", " ")
    else:
        # 用"|seg|"作为分割符分开
        text = re.sub(r"([.!?]) +", r"\1\|seg\|", text)
        text = text.replace("\n", "|seg|")
    text, mapping = protect_kaomoji(text)
    # print(f"处理前的文本: {text}")

    text_no_1 = ""
    for letter in text:
        # print(f"当前字符: {letter}")
        if letter in ["!", "！", "?", "？"]:
            # print(f"当前字符: {letter}, 随机数: {random.random()}")
            if random.random() < split_strength:
                letter = ""
        if letter in ["。", "…"]:
            # print(f"当前字符: {letter}, 随机数: {random.random()}")
            if random.random() < 1 - split_strength:
                letter = ""
        text_no_1 += letter

    # 对每个逗号单独判断是否分割
    sentences = [text_no_1]
    new_sentences = []
    for sentence in sentences:
        parts = sentence.split("，")
        current_sentence = parts[0]
        if not is_western_paragraph(current_sentence):
            for part in parts[1:]:
                if random.random() < split_strength:
                    new_sentences.append(current_sentence.strip())
                    current_sentence = part
                else:
                    current_sentence += "，" + part
            # 处理空格分割
            space_parts = current_sentence.split(" ")
            current_sentence = space_parts[0]
            for part in space_parts[1:]:
                if random.random() < split_strength:
                    new_sentences.append(current_sentence.strip())
                    current_sentence = part
                else:
                    current_sentence += " " + part
        else:
            # 处理分割符
            space_parts = current_sentence.split("|seg|")
            current_sentence = space_parts[0]
            for part in space_parts[1:]:
                new_sentences.append(current_sentence.strip())
                current_sentence = part
        new_sentences.append(current_sentence.strip())
    sentences = [s for s in new_sentences if s]  # 移除空字符串
    sentences = recover_kaomoji(sentences, mapping)

    # print(f"分割后的句子: {sentences}")
    sentences_done = []
    for sentence in sentences:
        sentence = sentence.rstrip("，,")
        # 西文字符句子不进行随机合并
        if not is_western_paragraph(current_sentence):
            if random.random() < split_strength * 0.5:
                sentence = sentence.replace("，", "").replace(",", "")
            elif random.random() < split_strength:
                sentence = sentence.replace("，", " ").replace(",", " ")
        sentences_done.append(sentence)

    logger.debug(f"处理后的句子: {sentences_done}")
    return sentences_done


def random_remove_punctuation(text: str) -> str:
    """随机处理标点符号，模拟人类打字习惯

    Args:
        text: 要处理的文本

    Returns:
        str: 处理后的文本
    """
    result = ""
    text_len = len(text)

    for i, char in enumerate(text):
        if char == "。" and i == text_len - 1:  # 结尾的句号
            if random.random() > 0.1:  # 90%概率删除结尾句号
                continue
        elif char == "，":
            rand = random.random()
            if rand < 0.25:  # 5%概率删除逗号
                continue
            elif rand < 0.25:  # 20%概率把逗号变成空格
                result += " "
                continue
        result += char
    return result


def process_llm_response(text: str) -> List[str]:
    # 先保护颜文字
    protected_text, kaomoji_mapping = protect_kaomoji(text)
    logger.debug(f"保护颜文字后的文本: {protected_text}")
    # 提取被 () 或 [] 包裹的内容
    pattern = re.compile(r"[\(\[\（].*?[\)\]\）]")
    # _extracted_contents = pattern.findall(text)
    _extracted_contents = pattern.findall(protected_text)  # 在保护后的文本上查找

    # 去除 () 和 [] 及其包裹的内容
    # cleaned_text = pattern.sub("", text)
    cleaned_text = pattern.sub("", protected_text)

    if cleaned_text == "":
        return ["呃呃"]

    logger.debug(f"{text}去除括号处理后的文本: {cleaned_text}")

    # 对清理后的文本进行进一步处理
    max_length = global_config.response_max_length * 2
    max_sentence_num = global_config.response_max_sentence_num
    if len(cleaned_text) > max_length and not is_western_paragraph(cleaned_text):
        logger.warning(f"回复过长 ({len(cleaned_text)} 字符)，返回默认回复")
        return ["懒得说"]
    elif len(cleaned_text) > 200:
        logger.warning(f"回复过长 ({len(cleaned_text)} 字符)，返回默认回复")
        return ["懒得说"]

    typo_generator = ChineseTypoGenerator(
        error_rate=global_config.chinese_typo_error_rate,
        min_freq=global_config.chinese_typo_min_freq,
        tone_error_rate=global_config.chinese_typo_tone_error_rate,
        word_replace_rate=global_config.chinese_typo_word_replace_rate,
    )

    if global_config.enable_response_splitter:
        split_sentences = split_into_sentences_w_remove_punctuation(cleaned_text)
    else:
        split_sentences = [cleaned_text]

    sentences = []
    for sentence in split_sentences:
        if global_config.chinese_typo_enable:
            typoed_text, typo_corrections = typo_generator.create_typo_sentence(sentence)
            sentences.append(typoed_text)
            if typo_corrections:
                sentences.append(typo_corrections)
        else:
            sentences.append(sentence)

    if len(sentences) > max_sentence_num:
        logger.warning(f"分割后消息数量过多 ({len(sentences)} 条)，返回默认回复")
        return [f"{global_config.BOT_NICKNAME}不知道哦"]

    # sentences.extend(extracted_contents)
    # 在所有句子处理完毕后，对包含占位符的列表进行恢复
    sentences = recover_kaomoji(sentences, kaomoji_mapping)

    return sentences


def calculate_typing_time(
    input_string: str,
    thinking_start_time: float,
    chinese_time: float = 0.2,
    english_time: float = 0.1,
    is_emoji: bool = False,
) -> float:
    """
    计算输入字符串所需的时间，中文和英文字符有不同的输入时间
        input_string (str): 输入的字符串
        chinese_time (float): 中文字符的输入时间，默认为0.2秒
        english_time (float): 英文字符的输入时间，默认为0.1秒
        is_emoji (bool): 是否为emoji，默认为False

    特殊情况：
    - 如果只有一个中文字符，将使用3倍的中文输入时间
    - 在所有输入结束后，额外加上回车时间0.3秒
    - 如果is_emoji为True，将使用固定1秒的输入时间
    """
    mood_manager = MoodManager.get_instance()
    # 将0-1的唤醒度映射到-1到1
    mood_arousal = mood_manager.current_mood.arousal
    # 映射到0.5到2倍的速度系数
    typing_speed_multiplier = 1.5**mood_arousal  # 唤醒度为1时速度翻倍,为-1时速度减半
    chinese_time *= 1 / typing_speed_multiplier
    english_time *= 1 / typing_speed_multiplier
    # 计算中文字符数
    chinese_chars = sum(1 for char in input_string if "\u4e00" <= char <= "\u9fff")

    # 如果只有一个中文字符，使用3倍时间
    if chinese_chars == 1 and len(input_string.strip()) == 1:
        return chinese_time * 3 + 0.3  # 加上回车时间

    # 正常计算所有字符的输入时间
    total_time = 0.0
    for char in input_string:
        if "\u4e00" <= char <= "\u9fff":  # 判断是否为中文字符
            total_time += chinese_time
        else:  # 其他字符（如英文）
            total_time += english_time

    if is_emoji:
        total_time = 1

    if time.time() - thinking_start_time > 10:
        total_time = 1

    # print(f"thinking_start_time:{thinking_start_time}")
    # print(f"nowtime:{time.time()}")
    # print(f"nowtime - thinking_start_time:{time.time() - thinking_start_time}")
    # print(f"{total_time}")

    return total_time  # 加上回车时间


def cosine_similarity(v1, v2):
    """计算余弦相似度"""
    dot_product = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0
    return dot_product / (norm1 * norm2)


def text_to_vector(text):
    """将文本转换为词频向量"""
    # 分词
    words = jieba.lcut(text)
    # 统计词频
    word_freq = Counter(words)
    return word_freq


def find_similar_topics_simple(text: str, topics: list, top_k: int = 5) -> list:
    """使用简单的余弦相似度计算文本相似度"""
    # 将输入文本转换为词频向量
    text_vector = text_to_vector(text)

    # 计算每个主题的相似度
    similarities = []
    for topic in topics:
        topic_vector = text_to_vector(topic)
        # 获取所有唯一词
        all_words = set(text_vector.keys()) | set(topic_vector.keys())
        # 构建向量
        v1 = [text_vector.get(word, 0) for word in all_words]
        v2 = [topic_vector.get(word, 0) for word in all_words]
        # 计算相似度
        similarity = cosine_similarity(v1, v2)
        similarities.append((topic, similarity))

    # 按相似度降序排序并返回前k个
    return sorted(similarities, key=lambda x: x[1], reverse=True)[:top_k]


def truncate_message(message: str, max_length=20) -> str:
    """截断消息，使其不超过指定长度"""
    if len(message) > max_length:
        return message[:max_length] + "..."
    return message


def protect_kaomoji(sentence):
    """ "
    识别并保护句子中的颜文字（含括号与无括号），将其替换为占位符，
    并返回替换后的句子和占位符到颜文字的映射表。
    Args:
        sentence (str): 输入的原始句子
    Returns:
        tuple: (处理后的句子, {占位符: 颜文字})
    """
    kaomoji_pattern = re.compile(
        r"("
        r"[(\[（【]"  # 左括号
        r"[^()\[\]（）【】]*?"  # 非括号字符（惰性匹配）
        r"[^一-龥a-zA-Z0-9\s]"  # 非中文、非英文、非数字、非空格字符（必须包含至少一个）
        r"[^()\[\]（）【】]*?"  # 非括号字符（惰性匹配）
        r"[\)\]）】"  # 右括号
        r"]"
        r")"
        r"|"
        r"([▼▽・ᴥω･﹏^><≧≦￣｀´∀ヮДд︿﹀へ｡ﾟ╥╯╰︶︹•⁄]{2,15})"
    )

    kaomoji_matches = kaomoji_pattern.findall(sentence)
    placeholder_to_kaomoji = {}

    for idx, match in enumerate(kaomoji_matches):
        kaomoji = match[0] if match[0] else match[1]
        placeholder = f"__KAOMOJI_{idx}__"
        sentence = sentence.replace(kaomoji, placeholder, 1)
        placeholder_to_kaomoji[placeholder] = kaomoji

    return sentence, placeholder_to_kaomoji


def recover_kaomoji(sentences, placeholder_to_kaomoji):
    """
    根据映射表恢复句子中的颜文字。
    Args:
        sentences (list): 含有占位符的句子列表
        placeholder_to_kaomoji (dict): 占位符到颜文字的映射表
    Returns:
        list: 恢复颜文字后的句子列表
    """
    recovered_sentences = []
    for sentence in sentences:
        for placeholder, kaomoji in placeholder_to_kaomoji.items():
            sentence = sentence.replace(placeholder, kaomoji)
        recovered_sentences.append(sentence)
    return recovered_sentences


def is_western_char(char):
    """检测是否为西文字符"""
    return len(char.encode("utf-8")) <= 2


def is_western_paragraph(paragraph):
    """检测是否为西文字符段落"""
    return all(is_western_char(char) for char in paragraph if char.isalnum())


def count_messages_between(start_time: float, end_time: float, stream_id: str) -> tuple[int, int]:
    """计算两个时间点之间的消息数量和文本总长度

    Args:
        start_time (float): 起始时间戳
        end_time (float): 结束时间戳
        stream_id (str): 聊天流ID

    Returns:
        tuple[int, int]: (消息数量, 文本总长度)
        - 消息数量：包含起始时间的消息，不包含结束时间的消息
        - 文本总长度：所有消息的processed_plain_text长度之和
    """
    try:
        # 获取开始时间之前最新的一条消息
        start_message = db.messages.find_one(
            {"chat_id": stream_id, "time": {"$lte": start_time}},
            sort=[("time", -1), ("_id", -1)],  # 按时间倒序，_id倒序（最后插入的在前）
        )

        # 获取结束时间最近的一条消息
        # 先找到结束时间点的所有消息
        end_time_messages = list(
            db.messages.find(
                {"chat_id": stream_id, "time": {"$lte": end_time}},
                sort=[("time", -1)],  # 先按时间倒序
            ).limit(10)
        )  # 限制查询数量，避免性能问题

        if not end_time_messages:
            logger.warning(f"未找到结束时间 {end_time} 之前的消息")
            return 0, 0

        # 找到最大时间
        max_time = end_time_messages[0]["time"]
        # 在最大时间的消息中找最后插入的（_id最大的）
        end_message = max([msg for msg in end_time_messages if msg["time"] == max_time], key=lambda x: x["_id"])

        if not start_message:
            logger.warning(f"未找到开始时间 {start_time} 之前的消息")
            return 0, 0

        # 调试输出
        # print("\n=== 消息范围信息 ===")
        # print("Start message:", {
        #     "message_id": start_message.get("message_id"),
        #     "time": start_message.get("time"),
        #     "text": start_message.get("processed_plain_text", ""),
        #     "_id": str(start_message.get("_id"))
        # })
        # print("End message:", {
        #     "message_id": end_message.get("message_id"),
        #     "time": end_message.get("time"),
        #     "text": end_message.get("processed_plain_text", ""),
        #     "_id": str(end_message.get("_id"))
        # })
        # print("Stream ID:", stream_id)

        # 如果结束消息的时间等于开始时间，返回0
        if end_message["time"] == start_message["time"]:
            return 0, 0

        # 获取并打印这个时间范围内的所有消息
        # print("\n=== 时间范围内的所有消息 ===")
        all_messages = list(
            db.messages.find(
                {"chat_id": stream_id, "time": {"$gte": start_message["time"], "$lte": end_message["time"]}},
                sort=[("time", 1), ("_id", 1)],  # 按时间正序，_id正序
            )
        )

        count = 0
        total_length = 0
        for msg in all_messages:
            count += 1
            text_length = len(msg.get("processed_plain_text", ""))
            total_length += text_length
            # print(f"\n消息 {count}:")
            # print({
            #     "message_id": msg.get("message_id"),
            #     "time": msg.get("time"),
            #     "text": msg.get("processed_plain_text", ""),
            #     "text_length": text_length,
            #     "_id": str(msg.get("_id"))
            # })

        # 如果时间不同，需要把end_message本身也计入
        return count - 1, total_length

    except Exception as e:
        logger.error(f"计算消息数量时出错: {str(e)}")
        return 0, 0


def translate_timestamp_to_human_readable(timestamp: float, mode: str = "normal") -> str:
    """将时间戳转换为人类可读的时间格式

    Args:
        timestamp: 时间戳
        mode: 转换模式，"normal"为标准格式，"relative"为相对时间格式

    Returns:
        str: 格式化后的时间字符串
    """
    if mode == "normal":
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    elif mode == "relative":
        now = time.time()
        diff = now - timestamp

        if diff < 20:
            return "刚刚:"
        elif diff < 60:
            return f"{int(diff)}秒前:"
        elif diff < 1800:
            return f"{int(diff / 60)}分钟前:"
        elif diff < 3600:
            return f"{int(diff / 60)}分钟前:\n"
        elif diff < 86400:
            return f"{int(diff / 3600)}小时前:\n"
        elif diff < 604800:
            return f"{int(diff / 86400)}天前:\n"
        else:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) + ":"


def parse_text_timestamps(text: str, mode: str = "normal") -> str:
    """解析文本中的时间戳并转换为可读时间格式

    Args:
        text: 包含时间戳的文本，时间戳应以[]包裹
        mode: 转换模式，传递给translate_timestamp_to_human_readable，"normal"或"relative"

    Returns:
        str: 替换后的文本

    转换规则:
    - normal模式: 将文本中所有时间戳转换为可读格式
    - lite模式:
        - 第一个和最后一个时间戳必须转换
        - 以5秒为间隔划分时间段，每段最多转换一个时间戳
        - 不转换的时间戳替换为空字符串
    """
    # 匹配[数字]或[数字.数字]格式的时间戳
    pattern = r"\[(\d+(?:\.\d+)?)\]"

    # 找出所有匹配的时间戳
    matches = list(re.finditer(pattern, text))

    if not matches:
        return text

    # normal模式: 直接转换所有时间戳
    if mode == "normal":
        result_text = text

        # 将时间戳转换为可读格式并记录相同格式的时间戳
        timestamp_readable_map = {}
        readable_time_used = set()

        for match in matches:
            timestamp = float(match.group(1))
            readable_time = translate_timestamp_to_human_readable(timestamp, "normal")
            timestamp_readable_map[match.group(0)] = (timestamp, readable_time)

        # 按时间戳排序
        sorted_timestamps = sorted(timestamp_readable_map.items(), key=lambda x: x[1][0])

        # 执行替换，相同格式的只保留最早的
        for ts_str, (_, readable) in sorted_timestamps:
            pattern_instance = re.escape(ts_str)
            if readable in readable_time_used:
                # 如果这个可读时间已经使用过，替换为空字符串
                result_text = re.sub(pattern_instance, "", result_text, count=1)
            else:
                # 否则替换为可读时间并记录
                result_text = re.sub(pattern_instance, readable, result_text, count=1)
                readable_time_used.add(readable)

        return result_text
    else:
        # lite模式: 按5秒间隔划分并选择性转换
        result_text = text

        # 提取所有时间戳及其位置
        timestamps = [(float(m.group(1)), m) for m in matches]
        timestamps.sort(key=lambda x: x[0])  # 按时间戳升序排序

        if not timestamps:
            return text

        # 获取第一个和最后一个时间戳
        first_timestamp, first_match = timestamps[0]
        last_timestamp, last_match = timestamps[-1]

        # 将时间范围划分成5秒间隔的时间段
        time_segments = {}

        # 对所有时间戳按15秒间隔分组
        for ts, match in timestamps:
            segment_key = int(ts // 15)  # 将时间戳除以15取整，作为时间段的键
            if segment_key not in time_segments:
                time_segments[segment_key] = []
            time_segments[segment_key].append((ts, match))

        # 记录需要转换的时间戳
        to_convert = []

        # 从每个时间段中选择一个时间戳进行转换
        for _, segment_timestamps in time_segments.items():
            # 选择这个时间段中的第一个时间戳
            to_convert.append(segment_timestamps[0])

        # 确保第一个和最后一个时间戳在转换列表中
        first_in_list = False
        last_in_list = False

        for ts, _ in to_convert:
            if ts == first_timestamp:
                first_in_list = True
            if ts == last_timestamp:
                last_in_list = True

        if not first_in_list:
            to_convert.append((first_timestamp, first_match))
        if not last_in_list:
            to_convert.append((last_timestamp, last_match))

        # 创建需要转换的时间戳集合，用于快速查找
        to_convert_set = {match.group(0) for _, match in to_convert}

        # 首先替换所有不需要转换的时间戳为空字符串
        for _, match in timestamps:
            if match.group(0) not in to_convert_set:
                pattern_instance = re.escape(match.group(0))
                result_text = re.sub(pattern_instance, "", result_text, count=1)

        # 按照时间戳升序排序
        to_convert.sort(key=lambda x: x[0])

        # 将时间戳转换为可读时间并记录哪些可读时间已经使用过
        converted_timestamps = []
        readable_time_used = set()

        for ts, match in to_convert:
            readable_time = translate_timestamp_to_human_readable(ts, "relative")
            converted_timestamps.append((ts, match, readable_time))

        # 按照时间戳原始顺序排序，避免替换时位置错误
        converted_timestamps.sort(key=lambda x: x[1].start())

        # 从后向前替换，避免位置改变
        converted_timestamps.reverse()
        for timestamps, match, readable_time in converted_timestamps:
            pattern_instance = re.escape(match.group(0))
            if readable_time in readable_time_used:
                # 如果相同格式的时间已存在，替换为空字符串
                result_text = re.sub(pattern_instance, "", result_text, count=1)
            else:
                # 否则替换为可读时间并记录
                result_text = re.sub(pattern_instance, readable_time, result_text, count=1)
                readable_time_used.add(readable_time)

        return result_text
