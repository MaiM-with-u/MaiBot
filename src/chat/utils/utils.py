import random
import re
import regex
import time
from collections import Counter

import jieba
import numpy as np
from maim_message import UserInfo
from pymongo.errors import PyMongoError

from src.common.logger import get_module_logger
from src.manager.mood_manager import mood_manager
from ..message_receive.message import MessageRecv
from ..models.utils_model import LLMRequest
from .typo_generator import ChineseTypoGenerator
from ...common.database import db
from ...config.config import global_config

logger = get_module_logger("chat_utils")

# 预编译正则表达式以提高性能
_L_REGEX = regex.compile(r"\p{L}")  # 匹配任何Unicode字母
_HAN_CHAR_REGEX = regex.compile(r"\p{Han}")  # 匹配汉字 (Unicode属性)
_Nd_REGEX = regex.compile(r"\p{Nd}")  # 新增：匹配Unicode数字 (Nd = Number, decimal digit)

BOOK_TITLE_PLACEHOLDER_PREFIX = "__BOOKTITLE_"
SEPARATORS = {"。", "，", ",", " ", ";", "\xa0", "\n", ".", "—", "！", "？"}
KNOWN_ABBREVIATIONS_ENDING_WITH_DOT = {
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "St.",
    "Messrs.",
    "Mmes.",
    "Capt.",
    "Gov.",
    "Inc.",
    "Ltd.",
    "Corp.",
    "Co.",
    "PLC",  # PLC通常不带点，但有些可能
    "vs.",
    "etc.",
    "i.e.",
    "e.g.",
    "viz.",
    "al.",
    "et al.",
    "ca.",
    "cf.",
    "No.",
    "Vol.",
    "pp.",
    "fig.",
    "figs.",
    "ed.",
    "Ph.D.",
    "M.D.",
    "B.A.",
    "M.A.",
    "Jan.",
    "Feb.",
    "Mar.",
    "Apr.",
    "Jun.",
    "Jul.",
    "Aug.",
    "Sep.",
    "Oct.",
    "Nov.",
    "Dec.",  # May. 通常不用点
    "Mon.",
    "Tue.",
    "Wed.",
    "Thu.",
    "Fri.",
    "Sat.",
    "Sun.",
    "U.S.",
    "U.K.",
    "E.U.",
    "U.S.A.",
    "U.S.S.R.",
    "Ave.",
    "Blvd.",
    "Rd.",
    "Ln.",  # Street suffixes
    "approx.",
    "dept.",
    "appt.",
    "श्री.",  # Hindi Shri.
}


def is_letter_not_han(char_str: str) -> bool:
    """
    检查字符是否为“字母”且“非汉字”。
    例如拉丁字母、西里尔字母、韩文等返回True。
    汉字、数字、标点、空格等返回False。
    """
    if not isinstance(char_str, str) or len(char_str) != 1:
        return False

    is_letter = _L_REGEX.fullmatch(char_str) is not None
    if not is_letter:
        return False

    # 使用 \p{Han} 属性进行汉字判断，更为准确
    is_han = _HAN_CHAR_REGEX.fullmatch(char_str) is not None
    return not is_han


def is_han_character(char_str: str) -> bool:
    """检查字符是否为汉字 (使用 \p{Han} Unicode 属性)"""
    if not isinstance(char_str, str) or len(char_str) != 1:
        return False
    return _HAN_CHAR_REGEX.fullmatch(char_str) is not None


def is_digit(char_str: str) -> bool:
    """检查字符是否为Unicode数字"""
    if not isinstance(char_str, str) or len(char_str) != 1:
        return False
    return _Nd_REGEX.fullmatch(char_str) is not None


def is_relevant_word_char(char_str: str) -> bool:  # 新增辅助函数
    """
    检查字符是否为“相关词语字符”（非汉字字母 或 数字）。
    用于判断在非中文语境下，空格两侧是否应被视为一个词内部的部分。
    例如拉丁字母、西里尔字母、数字等返回True。
    汉字、标点、纯空格等返回False。
    """
    if not isinstance(char_str, str) or len(char_str) != 1:
        return False

    # 检查是否为Unicode字母
    if _L_REGEX.fullmatch(char_str):
        # 如果是字母，则检查是否非汉字
        return not _HAN_CHAR_REGEX.fullmatch(char_str)

    # 检查是否为Unicode数字
    if _Nd_REGEX.fullmatch(char_str):
        return True  # 数字本身被视为相关词语字符

    return False


def is_english_letter(char: str) -> bool:
    """检查字符是否为英文字母（忽略大小写）"""
    return "a" <= char.lower() <= "z"


def db_message_to_str(message_dict: dict) -> str:
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
            if re.match(
                f"\[回复 [\s\S]*?\({str(global_config.BOT_QQ)}\)：[\s\S]*?\]，说：",
                message.processed_plain_text,
            ):
                is_mentioned = True
            else:
                # 判断内容中是否被提及
                message_content = re.sub(r"@[\s\S]*?（(\d+)）", "", message.processed_plain_text)
                message_content = re.sub(r"\[回复 [\s\S]*?\(((\d+)|未知id)\)：[\s\S]*?]，说：", "", message_content)
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


def get_recent_group_detailed_plain_text(chat_stream_id: str, limit: int = 12, combine=False):
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


def split_into_sentences_w_remove_punctuation(original_text: str) -> list[str]:
    """将文本分割成句子，并根据概率合并"""
    # print(f"DEBUG: 输入文本 (repr): {repr(text)}")
    text, local_book_title_mapping = protect_book_titles(original_text)
    perform_book_title_recovery_here = True
    # 预处理
    text = regex.sub(r"\n\s*\n+", "\n", text)  # 合并多个换行符
    text = regex.sub(r"\n\s*([—。.,，;\s\xa0！？])", r"\1", text)
    text = regex.sub(r"([—。.,，;\s\xa0！？])\s*\n", r"\1", text)

    def replace_han_newline(match):
        char1 = match.group(1)
        char2 = match.group(2)
        if is_han_character(char1) and is_han_character(char2):
            return char1 + "，" + char2  # 汉字间的换行符替换为逗号
        return match.group(0)

    text = regex.sub(r"(.)\n(.)", replace_han_newline, text)

    len_text = len(text)
    if len_text < 3:
        stripped_text = text.strip()
        if not stripped_text:
            return []
        if len(stripped_text) == 1 and stripped_text in SEPARATORS:
            return []
        return [stripped_text]

    segments = []
    current_segment = ""
    i = 0
    while i < len(text):
        char = text[i]
        if char in SEPARATORS:
            can_split_current_char = True

            if char == ".":
                can_split_this_dot = True
                # 规则1: 小数点 (数字.数字)
                if 0 < i < len_text - 1 and is_digit(text[i - 1]) and is_digit(text[i + 1]):
                    can_split_this_dot = False
                # 规则2: 西文缩写/域名内部的点 (西文字母.西文字母)
                elif 0 < i < len_text - 1 and is_letter_not_han(text[i - 1]) and is_letter_not_han(text[i + 1]):
                    can_split_this_dot = False
                # 规则3: 已知缩写词的末尾点 (例如 "e.g. ", "U.S.A. ")
                else:
                    potential_abbreviation_word = current_segment + char
                    is_followed_by_space = i + 1 < len_text and text[i + 1] == " "
                    is_at_end_of_text = i + 1 == len_text

                    if potential_abbreviation_word in KNOWN_ABBREVIATIONS_ENDING_WITH_DOT and (
                        is_followed_by_space or is_at_end_of_text
                    ):
                        can_split_this_dot = False
                can_split_current_char = can_split_this_dot
            elif char == " " or char == "\xa0":  # 处理空格/NBSP
                if 0 < i < len_text - 1:
                    prev_char = text[i - 1]
                    next_char = text[i + 1]
                    # 非中文单词内部的空格不分割 (例如 "hello world", "слово1 слово2")
                    if is_relevant_word_char(prev_char) and is_relevant_word_char(next_char):
                        can_split_current_char = False

            if can_split_current_char:
                if current_segment:  # 如果当前段落有内容，则添加 (内容, 分隔符)
                    segments.append((current_segment, char))
                # 如果当前段落为空，但分隔符不是简单的排版空格 (除非是换行符这种有意义的空行分隔)
                elif char not in [" ", "\xa0"] or char == "\n":
                    segments.append(("", char))  # 添加 ("", 分隔符)
                current_segment = ""  # 重置当前段落
            else:
                current_segment += char  # 不分割，将当前分隔符加入到当前段落
        else:
            current_segment += char  # 非分隔符，加入当前段落
        i += 1

    if current_segment:  # 处理末尾剩余的段落
        segments.append((current_segment, ""))

    # 过滤掉仅由空格组成的segment，但保留其后的有效分隔符
    filtered_segments = []
    for content, sep in segments:
        stripped_content = content.strip()
        if stripped_content:
            filtered_segments.append((stripped_content, sep))
        elif sep and (sep not in [" ", "\xa0"] or sep == "\n"):
            filtered_segments.append(("", sep))
    segments = filtered_segments

    if not segments:
        return [text.strip()] if text.strip() else []

    preliminary_final_sentences = []
    current_sentence_build = ""
    for k, (content, sep) in enumerate(segments):
        current_sentence_build += content  # 先添加内容部分

        # 判断分隔符类型
        is_strong_terminator = sep in {"。", ".", "！", "？", "\n", "—"}
        is_space_separator = sep in [" ", "\xa0"]

        if is_strong_terminator:
            current_sentence_build += sep  # 将强终止符加入
            if current_sentence_build.strip():
                preliminary_final_sentences.append(current_sentence_build.strip())
            current_sentence_build = ""  # 开始新的句子构建
        elif is_space_separator:
            # 如果是空格，并且当前构建的句子不以空格结尾，则添加空格并继续构建
            if not current_sentence_build.endswith(sep):
                current_sentence_build += sep
        elif sep:  # 其他分隔符 (如 ',', ';')
            current_sentence_build += sep  # 加入并继续构建，这些通常不独立成句
            # 如果这些弱分隔符后紧跟的就是文本末尾，则它们可能结束一个句子
            if k == len(segments) - 1 and current_sentence_build.strip():
                preliminary_final_sentences.append(current_sentence_build.strip())
                current_sentence_build = ""

    if current_sentence_build.strip():  # 处理最后一个构建中的句子
        preliminary_final_sentences.append(current_sentence_build.strip())

    preliminary_final_sentences = [s for s in preliminary_final_sentences if s.strip()]  # 清理空字符串
    # print(f"DEBUG: 初步分割（优化组装后）的句子: {preliminary_final_sentences}")

    if not preliminary_final_sentences:
        return []

    if len_text < 12:
        split_strength = 0.2
    elif len_text < 32:
        split_strength = 0.5
    else:
        split_strength = 0.7
    merge_probability = 1.0 - split_strength

    if merge_probability == 1.0 and len(preliminary_final_sentences) > 1:
        merged_text = " ".join(preliminary_final_sentences).strip()
        if merged_text.endswith(",") or merged_text.endswith("，"):
            merged_text = merged_text[:-1].strip()
        return [merged_text] if merged_text else []
    elif len(preliminary_final_sentences) == 1:
        s = preliminary_final_sentences[0].strip()
        if s.endswith(",") or s.endswith("，"):
            s = s[:-1].strip()
        return [s] if s else []

    final_sentences_merged = []
    temp_sentence = ""
    if preliminary_final_sentences:
        temp_sentence = preliminary_final_sentences[0]
        for i_merge in range(1, len(preliminary_final_sentences)):
            should_merge_based_on_punctuation = True
            if temp_sentence and temp_sentence[-1] in {"。", ".", "！", "？"}:
                should_merge_based_on_punctuation = False

            if random.random() < merge_probability and temp_sentence and should_merge_based_on_punctuation:
                temp_sentence += " " + preliminary_final_sentences[i_merge]
            else:
                if temp_sentence:
                    final_sentences_merged.append(temp_sentence)
                temp_sentence = preliminary_final_sentences[i_merge]
        if temp_sentence:
            final_sentences_merged.append(temp_sentence)

    processed_sentences_after_merge = []
    for sentence in final_sentences_merged:
        s = sentence.strip()
        if s.endswith(",") or s.endswith("，"):
            s = s[:-1].strip()
        if s:
            s = random_remove_punctuation(s)
            processed_sentences_after_merge.append(s)

    if perform_book_title_recovery_here and local_book_title_mapping:
        # 假设 processed_sentences_after_merge 是最终的句子列表
        processed_sentences_after_merge = recover_book_titles(processed_sentences_after_merge, local_book_title_mapping)
    return processed_sentences_after_merge


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
            if rand < 0.25:  # 25%概率删除逗号
                continue
            elif rand < 0.2:  # 20%概率把逗号变成空格
                result += " "
                continue
        result += char
    return result


def process_llm_response(text: str) -> list[str]:
    # 先保护颜文字
    if global_config.enable_kaomoji_protection:
        protected_text, kaomoji_mapping = protect_kaomoji(text)
        logger.trace(f"保护颜文字后的文本: {protected_text}")
    else:
        protected_text = text
        kaomoji_mapping = {}
    # 提取被 () 或 [] 包裹且包含中文的内容
    pattern = re.compile(r"[(\[（](?=.*[一-鿿]).*?[)\]）]")
    # _extracted_contents = pattern.findall(text)
    _extracted_contents = pattern.findall(protected_text)  # 在保护后的文本上查找
    # 去除 () 和 [] 及其包裹的内容
    cleaned_text = pattern.sub("", protected_text)

    if cleaned_text == "":
        return ["呃呃"]

    logger.debug(f"{text}去除括号处理后的文本: {cleaned_text}")
    # 对清理后的文本进行进一步处理
    max_length = global_config.response_max_length * 2
    max_sentence_num = global_config.response_max_sentence_num
    # 如果基本上是中文，则进行长度过滤
    if get_western_ratio(cleaned_text) < 0.1:
        if len(cleaned_text) > max_length:
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

    # if extracted_contents:
    #     for content in extracted_contents:
    #         sentences.append(content)

    # 在所有句子处理完毕后，对包含占位符的列表进行恢复
    if global_config.enable_kaomoji_protection:
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
    mood_arousal = mood_manager.current_mood.arousal
    typing_speed_multiplier = 1.5**mood_arousal
    chinese_time *= 1 / typing_speed_multiplier
    english_time *= 1 / typing_speed_multiplier

    # 使用 is_han_character 进行判断
    chinese_chars = sum(1 for char in input_string if is_han_character(char))

    if chinese_chars == 1 and len(input_string.strip()) == 1:
        return chinese_time * 3 + 0.3

    total_time = 0
    for char in input_string:
        if is_han_character(char):  # 使用 is_han_character 进行判断
            total_time += chinese_time
        else:
            total_time += english_time

    if is_emoji:
        total_time = 1

    if time.time() - thinking_start_time > 10:
        total_time = 1

    return total_time


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
        r"[)\]）】"  # 右括号
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

    western_count = sum(1 for char in alnum_chars if is_english_letter(char))  # 保持使用 is_english_letter
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

    # 直接查询时间范围内的消息
    # time > start_time AND time <= end_time
    query = {"chat_id": stream_id, "time": {"$gt": start_time, "$lte": end_time}}

    try:
        # 执行查询
        messages_cursor = db.messages.find(query)

        # 遍历结果计算数量和长度
        for msg in messages_cursor:
            count += 1
            total_length += len(msg.get("processed_plain_text", ""))

        # logger.debug(f"查询范围 ({start_time}, {end_time}] 内找到 {count} 条消息，总长度 {total_length}")
        return count, total_length

    except PyMongoError as e:
        logger.error(f"查询 stream_id={stream_id} 在 ({start_time}, {end_time}] 范围内的消息时出错: {e}")
        return 0, 0
    except Exception as e:  # 保留一个通用异常捕获以防万一
        logger.error(f"计算消息数量时发生意外错误: {e}")
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
        return time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime(timestamp))
    elif mode == "relative":
        now = time.time()
        diff = now - timestamp

        if diff < 20:
            return "刚刚:\n"
        elif diff < 60:
            return f"{int(diff)}秒前:\n"
        elif diff < 3600:
            return f"{int(diff / 60)}分钟前:\n"
        elif diff < 86400:
            return f"{int(diff / 3600)}小时前:\n"
        elif diff < 86400 * 2:
            return f"{int(diff / 86400)}天前:\n"
        else:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) + ":\n"
    else:  # mode = "lite" or unknown
        # 只返回时分秒格式，喵~
        return time.strftime("%H:%M:%S", time.localtime(timestamp))


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
        for match in matches:
            timestamp = float(match.group(1))
            readable_time = translate_timestamp_to_human_readable(timestamp, "normal")
            # 由于替换会改变文本长度，需要使用正则替换而非直接替换
            pattern_instance = re.escape(match.group(0))
            result_text = re.sub(pattern_instance, readable_time, result_text, count=1)
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

        # 按照时间戳原始顺序排序，避免替换时位置错误
        to_convert.sort(key=lambda x: x[1].start())

        # 执行替换
        # 由于替换会改变文本长度，从后向前替换
        to_convert.reverse()
        for ts, match in to_convert:
            readable_time = translate_timestamp_to_human_readable(ts, "relative")
            pattern_instance = re.escape(match.group(0))
            result_text = re.sub(pattern_instance, readable_time, result_text, count=1)

        return result_text

def protect_book_titles(text):
    book_title_mapping = {}
    book_title_pattern = re.compile(r"《(.*?)》") # 非贪婪匹配

    def replace_func(match):
        # 生成唯一占位符
        placeholder = f"{BOOK_TITLE_PLACEHOLDER_PREFIX}{len(book_title_mapping)}__"
        # 存储映射关系
        book_title_mapping[placeholder] = match.group(0) # 存储包含书名号的完整匹配
        return placeholder

    protected_text = book_title_pattern.sub(replace_func, text)
    return protected_text, book_title_mapping

def recover_book_titles(sentences, book_title_mapping):
    recovered_sentences = []
    for sentence in sentences:
        for placeholder, original_content in book_title_mapping.items():
            sentence = sentence.replace(placeholder, original_content)
        recovered_sentences.append(sentence)
    return recovered_sentences
