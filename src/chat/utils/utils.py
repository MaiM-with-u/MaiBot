import random
import re
import time
import jieba
import json
import ast
import numpy as np

from collections import Counter
from typing import Optional, Tuple, Dict, List

from src.common.logger import get_logger
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.message_repository import find_messages, count_messages
from src.config.config import global_config, model_config
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.chat_stream import get_chat_manager
from src.llm_models.utils_model import LLMRequest
from src.person_info.person_info import Person
from .typo_generator import ChineseTypoGenerator

logger = get_logger("chat_utils")


def is_english_letter(char: str) -> bool:
    """检查字符是否为英文字母（忽略大小写）"""
    return "a" <= char.lower() <= "z"


def db_message_to_str(message_dict: dict) -> str:
    logger.debug(f"message_dict: {message_dict}")
    time_str = time.strftime("%m-%d %H:%M:%S", time.localtime(message_dict["time"]))
    try:
        name = f"[({message_dict['user_id']}){message_dict.get('user_nickname', '')}]{message_dict.get('user_cardname', '')}"
    except Exception:
        name = message_dict.get("user_nickname", "") or f"用户{message_dict['user_id']}"
    content = message_dict.get("processed_plain_text", "")
    result = f"[{time_str}] {name}: {content}\n"
    logger.debug(f"result: {result}")
    return result


def is_mentioned_bot_in_message(message: MessageRecv) -> tuple[bool, float]:
    """检查消息是否提到了机器人"""
    keywords = [global_config.bot.nickname]
    nicknames = global_config.bot.alias_names
    reply_probability = 0.0
    is_at = False
    is_mentioned = False
    if message.is_mentioned is not None:
        return bool(message.is_mentioned), message.is_mentioned
    if (
        message.message_info.additional_config is not None
        and message.message_info.additional_config.get("is_mentioned") is not None
    ):
        try:
            reply_probability = float(message.message_info.additional_config.get("is_mentioned"))  # type: ignore
            is_mentioned = True
            return is_mentioned, reply_probability
        except Exception as e:
            logger.warning(str(e))
            logger.warning(
                f"消息中包含不合理的设置 is_mentioned: {message.message_info.additional_config.get('is_mentioned')}"
            )

    if global_config.bot.nickname in message.processed_plain_text:
        is_mentioned = True

    for alias_name in global_config.bot.alias_names:
        if alias_name in message.processed_plain_text:
            is_mentioned = True

    # 判断是否被@
    if re.search(rf"@<(.+?):{global_config.bot.qq_account}>", message.processed_plain_text):
        is_at = True
        is_mentioned = True

    # print(f"message.processed_plain_text: {message.processed_plain_text}")
    # print(f"is_mentioned: {is_mentioned}")
    # print(f"is_at: {is_at}")

    if is_at and global_config.chat.at_bot_inevitable_reply:
        reply_probability = 1.0
        logger.debug("被@，回复概率设置为100%")
    else:
        if not is_mentioned:
            # 判断是否被回复
            if re.match(
                rf"\[回复 (.+?)\({str(global_config.bot.qq_account)}\)：(.+?)\]，说：", message.processed_plain_text
            ) or re.match(
                rf"\[回复<(.+?)(?=:{str(global_config.bot.qq_account)}>)\:{str(global_config.bot.qq_account)}>：(.+?)\]，说：",
                message.processed_plain_text,
            ):
                is_mentioned = True
            else:
                # 判断内容中是否被提及
                message_content = re.sub(r"@(.+?)（(\d+)）", "", message.processed_plain_text)
                message_content = re.sub(r"@<(.+?)(?=:(\d+))\:(\d+)>", "", message_content)
                message_content = re.sub(r"\[回复 (.+?)\(((\d+)|未知id)\)：(.+?)\]，说：", "", message_content)
                message_content = re.sub(r"\[回复<(.+?)(?=:(\d+))\:(\d+)>：(.+?)\]，说：", "", message_content)
                for keyword in keywords:
                    if keyword in message_content:
                        is_mentioned = True
                for nickname in nicknames:
                    if nickname in message_content:
                        is_mentioned = True
        if is_mentioned and global_config.chat.mentioned_bot_inevitable_reply:
            reply_probability = 1.0
            logger.debug("被提及，回复概率设置为100%")
    return is_mentioned, reply_probability


async def get_embedding(text, request_type="embedding") -> Optional[List[float]]:
    """获取文本的embedding向量"""
    # 每次都创建新的LLMRequest实例以避免事件循环冲突
    llm = LLMRequest(model_set=model_config.model_task_config.embedding, request_type=request_type)
    try:
        embedding, _ = await llm.get_embedding(text)
    except Exception as e:
        logger.error(f"获取embedding失败: {str(e)}")
        embedding = None
    return embedding


def get_recent_group_speaker(chat_stream_id: str, sender, limit: int = 12) -> list:
    # 获取当前群聊记录内发言的人
    filter_query = {"chat_id": chat_stream_id}
    sort_order = [("time", -1)]
    recent_messages = find_messages(message_filter=filter_query, sort=sort_order, limit=limit)

    if not recent_messages:
        return []

    who_chat_in_group = []
    for db_msg in recent_messages:
        # user_info = UserInfo.from_dict(
        #     {
        #         "platform": msg_db_data["user_platform"],
        #         "user_id": msg_db_data["user_id"],
        #         "user_nickname": msg_db_data["user_nickname"],
        #         "user_cardname": msg_db_data.get("user_cardname", ""),
        #     }
        # )
        # if (
        #     (user_info.platform, user_info.user_id) != sender
        #     and user_info.user_id != global_config.bot.qq_account
        #     and (user_info.platform, user_info.user_id, user_info.user_nickname) not in who_chat_in_group
        #     and len(who_chat_in_group) < 5
        # ):  # 排除重复，排除消息发送者，排除bot，限制加载的关系数目
        #     who_chat_in_group.append((user_info.platform, user_info.user_id, user_info.user_nickname))
        if (
            (db_msg.user_info.platform, db_msg.user_info.user_id) != sender
            and db_msg.user_info.user_id != global_config.bot.qq_account
            and (db_msg.user_info.platform, db_msg.user_info.user_id, db_msg.user_info.user_nickname)
            not in who_chat_in_group
            and len(who_chat_in_group) < 5
        ):  # 排除重复，排除消息发送者，排除bot，限制加载的关系数目
            who_chat_in_group.append(
                (db_msg.user_info.platform, db_msg.user_info.user_id, db_msg.user_info.user_nickname)
            )

    return who_chat_in_group


def split_into_sentences_w_remove_punctuation(text: str) -> list[str]:
    """将文本分割成句子，并根据概率合并
    1. 识别分割点（, ， 。 ; 空格），但如果分割点左右都是英文字母则不分割。
    2. 将文本分割成 (内容, 分隔符) 的元组。
    3. 根据原始文本长度计算合并概率，概率性地合并相邻段落。
    注意：此函数假定颜文字已在上层被保护。
    Args:
        text: 要分割的文本字符串 (假定颜文字已被保护)
    Returns:
        List[str]: 分割和合并后的句子列表
    """
    # 预处理：处理多余的换行符
    # 1. 将连续的换行符替换为单个换行符
    text = re.sub(r"\n\s*\n+", "\n", text)
    # 2. 处理换行符和其他分隔符的组合
    text = re.sub(r"\n\s*([，,。;\s])", r"\1", text)
    text = re.sub(r"([，,。;\s])\s*\n", r"\1", text)

    # 处理两个汉字中间的换行符
    text = re.sub(r"([\u4e00-\u9fff])\n([\u4e00-\u9fff])", r"\1。\2", text)

    len_text = len(text)
    if len_text < 3:
        return list(text) if random.random() < 0.01 else [text]

    # 定义分隔符
    separators = {"，", ",", " ", "。", ";"}
    segments = []
    current_segment = ""

    # 1. 分割成 (内容, 分隔符) 元组
    i = 0
    while i < len(text):
        char = text[i]
        if char in separators:
            # 检查分割条件：如果分隔符左右都是英文字母，则不分割
            can_split = True
            if 0 < i < len(text) - 1:
                prev_char = text[i - 1]
                next_char = text[i + 1]
                # if is_english_letter(prev_char) and is_english_letter(next_char) and char == ' ': # 原计划只对空格应用此规则，现应用于所有分隔符
                if is_english_letter(prev_char) and is_english_letter(next_char):
                    can_split = False

            if can_split:
                # 只有当当前段不为空时才添加
                if current_segment:
                    segments.append((current_segment, char))
                # 如果当前段为空，但分隔符是空格，则也添加一个空段（保留空格）
                elif char == " ":
                    segments.append(("", char))
                current_segment = ""
            else:
                # 不分割，将分隔符加入当前段
                current_segment += char
        else:
            current_segment += char
        i += 1

    # 添加最后一个段（没有后续分隔符）
    if current_segment:
        segments.append((current_segment, ""))

    # 过滤掉完全空的段（内容和分隔符都为空）
    segments = [(content, sep) for content, sep in segments if content or sep]

    # 如果分割后为空（例如，输入全是分隔符且不满足保留条件），恢复颜文字并返回
    if not segments:
        return [text] if text else []  # 如果原始文本非空，则返回原始文本（可能只包含未被分割的字符或颜文字占位符）

    # 2. 概率合并
    if len_text < 12:
        split_strength = 0.2
    elif len_text < 32:
        split_strength = 0.6
    else:
        split_strength = 0.7
    # 合并概率与分割强度相反
    merge_probability = 1.0 - split_strength

    merged_segments = []
    idx = 0
    while idx < len(segments):
        current_content, current_sep = segments[idx]

        # 检查是否可以与下一段合并
        # 条件：不是最后一段，且随机数小于合并概率，且当前段有内容（避免合并空段）
        if idx + 1 < len(segments) and random.random() < merge_probability and current_content:
            next_content, next_sep = segments[idx + 1]
            # 合并: (内容1 + 分隔符1 + 内容2, 分隔符2)
            # 只有当下一段也有内容时才合并文本，否则只传递分隔符
            if next_content:
                merged_content = current_content + current_sep + next_content
                merged_segments.append((merged_content, next_sep))
            else:  # 下一段内容为空，只保留当前内容和下一段的分隔符
                merged_segments.append((current_content, next_sep))

            idx += 2  # 跳过下一段，因为它已被合并
        else:
            # 不合并，直接添加当前段
            merged_segments.append((current_content, current_sep))
            idx += 1

    # 提取最终的句子内容
    final_sentences = [content for content, sep in merged_segments if content]  # 只保留有内容的段

    # 清理可能引入的空字符串和仅包含空白的字符串
    final_sentences = [
        s for s in final_sentences if s.strip()
    ]  # 过滤掉空字符串以及仅包含空白（如换行符、空格）的字符串

    logger.debug(f"分割并合并后的句子: {final_sentences}")
    return final_sentences


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
            if rand < 0.05:  # 5%概率删除逗号
                continue
            elif rand < 0.25:  # 20%概率把逗号变成空格
                result += " "
                continue
        result += char
    return result


def process_llm_response(text: str, enable_splitter: bool = True, enable_chinese_typo: bool = True) -> list[str]:
    if not global_config.response_post_process.enable_response_post_process:
        return [text]

    # 先保护颜文字
    if global_config.response_splitter.enable_kaomoji_protection:
        protected_text, kaomoji_mapping = protect_kaomoji(text)
        logger.debug(f"保护颜文字后的文本: {protected_text}")
    else:
        protected_text = text
        kaomoji_mapping = {}
    # 提取被 () 或 [] 或 （）包裹且包含中文的内容
    pattern = re.compile(r"[(\[（](?=.*[一-鿿]).*?[)\]）]")
    _extracted_contents = pattern.findall(protected_text)  # 在保护后的文本上查找
    # 去除 () 和 [] 及其包裹的内容
    cleaned_text = pattern.sub("", protected_text)

    if cleaned_text == "":
        return ["呃呃"]

    logger.debug(f"{text}去除括号处理后的文本: {cleaned_text}")

    # 对清理后的文本进行进一步处理
    max_length = global_config.response_splitter.max_length * 2
    max_sentence_num = global_config.response_splitter.max_sentence_num
    # 如果基本上是中文，则进行长度过滤
    if get_western_ratio(cleaned_text) < 0.1 and len(cleaned_text) > max_length:
        logger.warning(f"回复过长 ({len(cleaned_text)} 字符)，返回默认回复")
        return ["懒得说"]

    typo_generator = ChineseTypoGenerator(
        error_rate=global_config.chinese_typo.error_rate,
        min_freq=global_config.chinese_typo.min_freq,
        tone_error_rate=global_config.chinese_typo.tone_error_rate,
        word_replace_rate=global_config.chinese_typo.word_replace_rate,
    )

    if global_config.response_splitter.enable and enable_splitter:
        split_sentences = split_into_sentences_w_remove_punctuation(cleaned_text)
    else:
        split_sentences = [cleaned_text]

    sentences = []
    for sentence in split_sentences:
        if global_config.chinese_typo.enable and enable_chinese_typo:
            typoed_text, typo_corrections = typo_generator.create_typo_sentence(sentence)
            sentences.append(typoed_text)
            if typo_corrections:
                sentences.append(typo_corrections)
        else:
            sentences.append(sentence)

    if len(sentences) > max_sentence_num:
        logger.warning(f"分割后消息数量过多 ({len(sentences)} 条)，返回默认回复")
        return [f"{global_config.bot.nickname}不知道哦"]

    # if extracted_contents:
    #     for content in extracted_contents:
    #         sentences.append(content)

    # 在所有句子处理完毕后，对包含占位符的列表进行恢复
    if global_config.response_splitter.enable_kaomoji_protection:
        sentences = recover_kaomoji(sentences, kaomoji_mapping)

    return sentences


def calculate_typing_time(
    input_string: str,
    thinking_start_time: float,
    chinese_time: float = 0.3,
    english_time: float = 0.15,
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
    # # 将0-1的唤醒度映射到-1到1
    # mood_arousal = mood_manager.current_mood.arousal
    # # 映射到0.5到2倍的速度系数
    # typing_speed_multiplier = 1.5**mood_arousal  # 唤醒度为1时速度翻倍,为-1时速度减半
    # chinese_time *= 1 / typing_speed_multiplier
    # english_time *= 1 / typing_speed_multiplier
    # 计算中文字符数
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in input_string)

    # 如果只有一个中文字符，使用3倍时间
    if chinese_chars == 1 and len(input_string.strip()) == 1:
        return chinese_time * 3 + 0.3  # 加上回车时间

    # 正常计算所有字符的输入时间
    total_time = 0.0
    for char in input_string:
        total_time += chinese_time if "\u4e00" <= char <= "\u9fff" else english_time
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
    return 0 if norm1 == 0 or norm2 == 0 else dot_product / (norm1 * norm2)


def text_to_vector(text):
    """将文本转换为词频向量"""
    # 分词
    words = jieba.lcut(text)
    return Counter(words)


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
    return f"{message[:max_length]}..." if len(message) > max_length else message


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
        r"[)\]）】"  # 右括号
        r"]"
        r")"
        r"|"
        r"([▼▽・ᴥω･﹏^><≧≦￣｀´∀ヮДд︿﹀へ｡ﾟ╥╯╰︶︹•⁄]{2,15})"
    )

    kaomoji_matches = kaomoji_pattern.findall(sentence)
    placeholder_to_kaomoji = {}

    for idx, match in enumerate(kaomoji_matches):
        kaomoji = match[0] or match[1]
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


def get_western_ratio(paragraph):
    """计算段落中字母数字字符的西文比例
    原理：检查段落中字母数字字符的西文比例
    通过is_english_letter函数判断每个字符是否为西文
    只检查字母数字字符，忽略标点符号和空格等非字母数字字符

    Args:
        paragraph: 要检查的文本段落

    Returns:
        float: 西文字符比例(0.0-1.0)，如果没有字母数字字符则返回0.0
    """
    alnum_chars = [char for char in paragraph if char.isalnum()]
    if not alnum_chars:
        return 0.0

    western_count = sum(bool(is_english_letter(char)) for char in alnum_chars)
    return western_count / len(alnum_chars)


def count_messages_between(start_time: float, end_time: float, stream_id: str) -> tuple[int, int]:
    """计算两个时间点之间的消息数量和文本总长度

    Args:
        start_time (float): 起始时间戳 (不包含)
        end_time (float): 结束时间戳 (包含)
        stream_id (str): 聊天流ID

    Returns:
        tuple[int, int]: (消息数量, 文本总长度)
    """
    count = 0
    total_length = 0

    # 参数校验 (可选但推荐)
    if start_time >= end_time:
        # logger.debug(f"开始时间 {start_time} 大于或等于结束时间 {end_time}，返回 0, 0")
        return 0, 0
    if not stream_id:
        logger.error("stream_id 不能为空")
        return 0, 0

    # 使用message_repository中的count_messages和find_messages函数

    # 构建查询条件
    filter_query = {"chat_id": stream_id, "time": {"$gt": start_time, "$lte": end_time}}

    try:
        # 先获取消息数量
        count = count_messages(filter_query)

        # 获取消息内容计算总长度
        messages = find_messages(message_filter=filter_query)
        total_length = sum(len(msg.processed_plain_text or "") for msg in messages)

        return count, total_length

    except Exception as e:
        logger.error(f"计算消息数量时发生意外错误: {e}")
        return 0, 0


def translate_timestamp_to_human_readable(timestamp: float, mode: str = "normal") -> str:
    # sourcery skip: merge-comparisons, merge-duplicate-blocks, switch
    """将时间戳转换为人类可读的时间格式

    Args:
        timestamp: 时间戳
        mode: 转换模式，"normal"为标准格式，"relative"为相对时间格式

    Returns:
        str: 格式化后的时间字符串
    """
    if mode == "normal":
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    elif mode == "normal_no_YMD":
        return time.strftime("%H:%M:%S", time.localtime(timestamp))
    elif mode == "relative":
        now = time.time()
        diff = now - timestamp

        if diff < 20:
            return "刚刚"
        elif diff < 60:
            return f"{int(diff)}秒前"
        elif diff < 3600:
            return f"{int(diff / 60)}分钟前"
        elif diff < 86400:
            return f"{int(diff / 3600)}小时前"
        elif diff < 86400 * 2:
            return f"{int(diff / 86400)}天前"
        else:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) + ":"
    else:  # mode = "lite" or unknown
        # 只返回时分秒格式
        return time.strftime("%H:%M:%S", time.localtime(timestamp))


def get_chat_type_and_target_info(chat_id: str) -> Tuple[bool, Optional[Dict]]:
    """
    获取聊天类型（是否群聊）和私聊对象信息。

    Args:
        chat_id: 聊天流ID

    Returns:
        Tuple[bool, Optional[Dict]]:
            - bool: 是否为群聊 (True 是群聊, False 是私聊或未知)
            - Optional[Dict]: 如果是私聊，包含对方信息的字典；否则为 None。
            字典包含: platform, user_id, user_nickname, person_id, person_name
    """
    is_group_chat = False  # Default to private/unknown
    chat_target_info = None

    try:
        if chat_stream := get_chat_manager().get_stream(chat_id):
            if chat_stream.group_info:
                is_group_chat = True
                chat_target_info = None  # Explicitly None for group chat
            elif chat_stream.user_info:  # It's a private chat
                is_group_chat = False
                user_info = chat_stream.user_info
                platform: str = chat_stream.platform
                user_id: str = user_info.user_id  # type: ignore

                from src.common.data_models.info_data_model import TargetPersonInfo  # 解决循环导入问题

                # Initialize target_info with basic info
                target_info = TargetPersonInfo(
                    platform=platform,
                    user_id=user_id,
                    user_nickname=user_info.user_nickname,  # type: ignore
                    person_id=None,
                    person_name=None,
                )

                # Try to fetch person info
                try:
                    person = Person(platform=platform, user_id=user_id)
                    if not person.is_known:
                        logger.warning(f"用户 {user_info.user_nickname} 尚未认识")
                        # 如果用户尚未认识，则返回False和None
                        return False, None
                    if person.person_id:
                        target_info.person_id = person.person_id
                        target_info.person_name = person.person_name
                except Exception as person_e:
                    logger.warning(
                        f"获取 person_id 或 person_name 时出错 for {platform}:{user_id} in utils: {person_e}"
                    )

                chat_target_info = target_info.__dict__
        else:
            logger.warning(f"无法获取 chat_stream for {chat_id} in utils")
    except Exception as e:
        logger.error(f"获取聊天类型和目标信息时出错 for {chat_id}: {e}", exc_info=True)

    return is_group_chat, chat_target_info


def assign_message_ids(messages: List[DatabaseMessages]) -> List[Tuple[str, DatabaseMessages]]:
    """
    为消息列表中的每个消息分配唯一的简短随机ID

    Args:
        messages: 消息列表

    Returns:
        List[DatabaseMessages]: 分配了唯一ID的消息列表(写入message_id属性)
    """
    result: List[Tuple[str, DatabaseMessages]] = []  # 复制原始消息列表
    used_ids = set()
    len_i = len(messages)
    if len_i > 100:
        a = 10
        b = 99
    else:
        a = 1
        b = 9

    for i, message in enumerate(messages):
        # 生成唯一的简短ID
        while True:
            # 使用索引+随机数生成简短ID
            random_suffix = random.randint(a, b)
            message_id = f"m{i + 1}{random_suffix}"

            if message_id not in used_ids:
                used_ids.add(message_id)
                break
        result.append((message_id, message))

    return result


# def assign_message_ids_flexible(
#     messages: list, prefix: str = "msg", id_length: int = 6, use_timestamp: bool = False
# ) -> list:
#     """
#     为消息列表中的每个消息分配唯一的简短随机ID（增强版）

#     Args:
#         messages: 消息列表
#         prefix: ID前缀，默认为"msg"
#         id_length: ID的总长度（不包括前缀），默认为6
#         use_timestamp: 是否在ID中包含时间戳，默认为False

#     Returns:
#         包含 {'id': str, 'message': any} 格式的字典列表
#     """
#     result = []
#     used_ids = set()

#     for i, message in enumerate(messages):
#         # 生成唯一的ID
#         while True:
#             if use_timestamp:
#                 # 使用时间戳的后几位 + 随机字符
#                 timestamp_suffix = str(int(time.time() * 1000))[-3:]
#                 remaining_length = id_length - 3
#                 random_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=remaining_length))
#                 message_id = f"{prefix}{timestamp_suffix}{random_chars}"
#             else:
#                 # 使用索引 + 随机字符
#                 index_str = str(i + 1)
#                 remaining_length = max(1, id_length - len(index_str))
#                 random_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=remaining_length))
#                 message_id = f"{prefix}{index_str}{random_chars}"

#             if message_id not in used_ids:
#                 used_ids.add(message_id)
#                 break

#         result.append({"id": message_id, "message": message})

#     return result


# 使用示例:
# messages = ["Hello", "World", "Test message"]
#
# # 基础版本
# result1 = assign_message_ids(messages)
# # 结果: [{'id': 'm1123', 'message': 'Hello'}, {'id': 'm2456', 'message': 'World'}, {'id': 'm3789', 'message': 'Test message'}]
#
# # 增强版本 - 自定义前缀和长度
# result2 = assign_message_ids_flexible(messages, prefix="chat", id_length=8)
# # 结果: [{'id': 'chat1abc2', 'message': 'Hello'}, {'id': 'chat2def3', 'message': 'World'}, {'id': 'chat3ghi4', 'message': 'Test message'}]
#
# # 增强版本 - 使用时间戳
# result3 = assign_message_ids_flexible(messages, prefix="ts", use_timestamp=True)
# # 结果: [{'id': 'ts123a1b', 'message': 'Hello'}, {'id': 'ts123c2d', 'message': 'World'}, {'id': 'ts123e3f', 'message': 'Test message'}]


def parse_keywords_string(keywords_input) -> list[str]:
    # sourcery skip: use-contextlib-suppress
    """
    统一的关键词解析函数，支持多种格式的关键词字符串解析

    支持的格式：
    1. 字符串列表格式：'["utils.py", "修改", "代码", "动作"]'
    2. 斜杠分隔格式：'utils.py/修改/代码/动作'
    3. 逗号分隔格式：'utils.py,修改,代码,动作'
    4. 空格分隔格式：'utils.py 修改 代码 动作'
    5. 已经是列表的情况：["utils.py", "修改", "代码", "动作"]
    6. JSON格式字符串：'{"keywords": ["utils.py", "修改", "代码", "动作"]}'

    Args:
        keywords_input: 关键词输入，可以是字符串或列表

    Returns:
        list[str]: 解析后的关键词列表，去除空白项
    """
    if not keywords_input:
        return []

    # 如果已经是列表，直接处理
    if isinstance(keywords_input, list):
        return [str(k).strip() for k in keywords_input if str(k).strip()]

    # 转换为字符串处理
    keywords_str = str(keywords_input).strip()
    if not keywords_str:
        return []

    try:
        # 尝试作为JSON对象解析（支持 {"keywords": [...]} 格式）
        json_data = json.loads(keywords_str)
        if isinstance(json_data, dict) and "keywords" in json_data:
            keywords_list = json_data["keywords"]
            if isinstance(keywords_list, list):
                return [str(k).strip() for k in keywords_list if str(k).strip()]
        elif isinstance(json_data, list):
            # 直接是JSON数组格式
            return [str(k).strip() for k in json_data if str(k).strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        # 尝试使用 ast.literal_eval 解析（支持Python字面量格式）
        parsed = ast.literal_eval(keywords_str)
        if isinstance(parsed, list):
            return [str(k).strip() for k in parsed if str(k).strip()]
    except (ValueError, SyntaxError):
        pass

    # 尝试不同的分隔符
    separators = ["/", ",", " ", "|", ";"]

    for separator in separators:
        if separator in keywords_str:
            keywords_list = [k.strip() for k in keywords_str.split(separator) if k.strip()]
            if len(keywords_list) > 1:  # 确保分割有效
                return keywords_list

    # 如果没有分隔符，返回单个关键词
    return [keywords_str] if keywords_str else []
