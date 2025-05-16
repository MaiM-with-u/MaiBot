import traceback
import json
import re
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List, Union
from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.common.database import db
from src.chat.memory_system.Hippocampus import HippocampusManager
from src.chat.focus_chat.heartflow_prompt_builder import prompt_builder
from src.chat.utils.utils import get_embedding
from src.chat.utils.chat_message_builder import build_readable_messages
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.person_info.person_info import person_info_manager
import math
from .observation_info import ObservationInfo


logger = get_logger("pfc_utils")


# ==============================================================================
# 专门用于检索 PFC 私聊历史对话上下文的函数
# ==============================================================================
async def find_most_relevant_historical_message(
    chat_id: str,
    query_text: str,
    similarity_threshold: float = 0.3,  # 相似度阈值，可以根据效果调整
    absolute_search_time_limit: Optional[float] = None,  # 新增参数：排除最近多少秒内的消息（例如5分钟）
) -> Optional[Dict[str, Any]]:
    """
    根据查询文本，在指定 chat_id 的历史消息中查找最相关的消息。
    """
    if not query_text or not query_text.strip():
        logger.debug(f"[{chat_id}] (私聊历史)查询文本为空，跳过检索。")
        return None

    logger.debug(f"[{chat_id}] (私聊历史)开始为查询文本 '{query_text[:50]}...' 检索。")

    # 使用你项目中已有的 get_embedding 函数
    # request_type 参数需要根据 get_embedding 的实际需求调整
    query_embedding = await get_embedding(query_text, request_type="pfc_historical_chat_query")
    if not query_embedding:
        logger.warning(f"[{chat_id}] (私聊历史)未能为查询文本 '{query_text[:50]}...' 生成嵌入向量。")
        return None

    effective_search_upper_limit: float
    log_source_of_limit: str = ""

    if absolute_search_time_limit is not None:
        effective_search_upper_limit = absolute_search_time_limit
        log_source_of_limit = "传入的绝对时间上限"
    else:
        # 如果没有传入绝对时间上限，可以设置一个默认的回退逻辑
        fallback_exclude_seconds = getattr(global_config, "pfc_historical_fallback_exclude_seconds", 7200)  # 默认2小时
        effective_search_upper_limit = time.time() - fallback_exclude_seconds
        log_source_of_limit = f"回退逻辑 (排除最近 {fallback_exclude_seconds} 秒)"

    logger.debug(
        f"[{chat_id}] (私聊历史) find_most_relevant_historical_message: "
        f"将使用时间上限 {effective_search_upper_limit} "
        f"(可读: {datetime.fromtimestamp(effective_search_upper_limit).strftime('%Y-%m-%d %H:%M:%S')}) "
        f"进行历史消息锚点搜索。来源: {log_source_of_limit}"
    )
    # --- [新代码结束] ---

    pipeline = [
        {
            "$match": {
                "chat_id": chat_id,
                "embedding_vector": {"$exists": True, "$ne": None, "$not": {"$size": 0}},
                "time": {"$lt": effective_search_upper_limit},  # <--- 使用新的 effective_search_upper_limit
            }
        },
        {
            "$addFields": {
                "dotProduct": {
                    "$reduce": {
                        "input": {"$range": [0, {"$size": "$embedding_vector"}]},
                        "initialValue": 0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$multiply": [
                                        {"$arrayElemAt": ["$embedding_vector", "$$this"]},
                                        {"$arrayElemAt": [query_embedding, "$$this"]},
                                    ]
                                },
                            ]
                        },
                    }
                },
                "queryVecMagnitude": {
                    "$sqrt": {
                        "$reduce": {
                            "input": query_embedding,
                            "initialValue": 0,
                            "in": {"$add": ["$$value", {"$multiply": ["$$this", "$$this"]}]},
                        }
                    }
                },
                "docVecMagnitude": {
                    "$sqrt": {
                        "$reduce": {
                            "input": "$embedding_vector",
                            "initialValue": 0,
                            "in": {"$add": ["$$value", {"$multiply": ["$$this", "$$this"]}]},
                        }
                    }
                },
            }
        },
        {
            "$addFields": {
                "similarity": {
                    "$cond": [
                        {"$and": [{"$gt": ["$queryVecMagnitude", 0]}, {"$gt": ["$docVecMagnitude", 0]}]},
                        {"$divide": ["$dotProduct", {"$multiply": ["$queryVecMagnitude", "$docVecMagnitude"]}]},
                        0,
                    ]
                }
            }
        },
        {"$match": {"similarity": {"$gte": similarity_threshold}}},
        {"$sort": {"similarity": -1}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "message_id": 1,
                "time": 1,
                "chat_id": 1,
                "user_info": 1,
                "processed_plain_text": 1,
                "similarity": 1,
            }
        },  # 可以不返回 embedding_vector 节省带宽
    ]

    try:
        # --- 确定性修改：同步执行聚合和结果转换 ---
        cursor = db.messages.aggregate(pipeline)  # PyMongo 的 aggregate 返回一个 CommandCursor
        results = list(cursor)  # 直接将 CommandCursor 转换为列表
        if not results:
            logger.info(
                f"[{chat_id}] (私聊历史) find_most_relevant_historical_message: 在时间点 {effective_search_upper_limit} 之前，未能找到任何与 '{query_text[:30]}...' 相关的历史消息。"
            )
        else:
            logger.info(
                f"[{chat_id}] (私聊历史) find_most_relevant_historical_message: 在时间点 {effective_search_upper_limit} 之前，找到了 {len(results)} 条候选历史消息。最相关的一条是："
            )
            for res_msg in results:
                msg_time_readable = datetime.fromtimestamp(res_msg.get("time", 0)).strftime("%Y-%m-%d %H:%M:%S")
                logger.info(
                    f"  - MsgID: {res_msg.get('message_id')}, Time: {msg_time_readable} (原始: {res_msg.get('time')}), Sim: {res_msg.get('similarity'):.4f}, Text: '{res_msg.get('processed_plain_text', '')[:50]}...'"
                )
        # --- [修改结束] ---

        # --- 修改结束 ---
        if results and len(results) > 0:
            most_similar_message = results[0]
            logger.info(
                f"[{chat_id}] (私聊历史)找到最相关消息 ID: {most_similar_message.get('message_id')}, 相似度: {most_similar_message.get('similarity'):.4f}"
            )
            return most_similar_message
        else:
            logger.debug(f"[{chat_id}] (私聊历史)未找到相似度超过 {similarity_threshold} 的相关消息。")
            return None
    except Exception as e:
        logger.error(f"[{chat_id}] (私聊历史)在数据库中检索时出错: {e}", exc_info=True)
        return None


async def retrieve_chat_context_window(
    chat_id: str,
    anchor_message_id: str,
    anchor_message_time: float,
    excluded_time_threshold_for_window: float,
    window_size_before: int = 7,
    window_size_after: int = 7,
) -> List[Dict[str, Any]]:
    """
    以某条消息为锚点，获取其前后的聊天记录形成一个上下文窗口。
    """
    if not anchor_message_id or anchor_message_time is None:
        return []

    context_messages: List[Dict[str, Any]] = []  # 明确类型
    logger.debug(
        f"[{chat_id}] (私聊历史)准备以消息 ID '{anchor_message_id}' (时间: {anchor_message_time}) 为锚点，获取上下文窗口..."
    )

    try:
        # --- 同步执行 find_one 和 find ---
        anchor_message = db.messages.find_one({"message_id": anchor_message_id, "chat_id": chat_id})

        messages_before_cursor = (
            db.messages.find({"chat_id": chat_id, "time": {"$lt": anchor_message_time}})
            .sort("time", -1)
            .limit(window_size_before)
        )
        messages_before = list(messages_before_cursor)
        messages_before.reverse()
        # --- 新增日志 ---
        logger.debug(
            f"[{chat_id}] (私聊历史) retrieve_chat_context_window: Anchor Time: {anchor_message_time}, Excluded Window End Time: {excluded_time_threshold_for_window}"
        )
        logger.debug(
            f"[{chat_id}] (私聊历史) retrieve_chat_context_window: Messages BEFORE anchor ({len(messages_before)}):"
        )
        for msg_b in messages_before:
            logger.debug(
                f"  - Time: {datetime.fromtimestamp(msg_b.get('time', 0)).strftime('%Y-%m-%d %H:%M:%S')}, Text: '{msg_b.get('processed_plain_text', '')[:30]}...'"
            )

        messages_after_cursor = (
            db.messages.find(
                {"chat_id": chat_id, "time": {"$gt": anchor_message_time, "$lt": excluded_time_threshold_for_window}}
            )
            .sort("time", 1)
            .limit(window_size_after)
        )
        messages_after = list(messages_after_cursor)
        # --- 新增日志 ---
        logger.debug(
            f"[{chat_id}] (私聊历史) retrieve_chat_context_window: Messages AFTER anchor ({len(messages_after)}):"
        )
        for msg_a in messages_after:
            logger.debug(
                f"  - Time: {datetime.fromtimestamp(msg_a.get('time', 0)).strftime('%Y-%m-%d %H:%M:%S')}, Text: '{msg_a.get('processed_plain_text', '')[:30]}...'"
            )

        if messages_before:
            context_messages.extend(messages_before)
        if anchor_message:
            anchor_message.pop("_id", None)
            context_messages.append(anchor_message)
        if messages_after:
            context_messages.extend(messages_after)

        final_window: List[Dict[str, Any]] = []  # 明确类型
        seen_ids: set[str] = set()  # 明确类型
        for msg in context_messages:
            msg_id = msg.get("message_id")
            if msg_id and msg_id not in seen_ids:  # 确保 msg_id 存在
                final_window.append(msg)
                seen_ids.add(msg_id)

        final_window.sort(key=lambda m: m.get("time", 0))
        logger.info(
            f"[{chat_id}] (私聊历史)为锚点 '{anchor_message_id}' 构建了包含 {len(final_window)} 条消息的上下文窗口。"
        )
        return final_window
    except Exception as e:
        logger.error(f"[{chat_id}] (私聊历史)获取消息 ID '{anchor_message_id}' 的上下文窗口时出错: {e}", exc_info=True)
        return []


# ==============================================================================
# 修改后的 retrieve_contextual_info 函数
# ==============================================================================
async def retrieve_contextual_info(
    text: str,  # 用于全局记忆和知识检索的主查询文本 (通常是短期聊天记录)
    private_name: str,  # 用于日志
    chat_id: str,  # 用于特定私聊历史的检索
    historical_chat_query_text: Optional[str] = None,
    current_short_term_history_earliest_time: Optional[float] = None,  # <--- 新增参数
) -> Tuple[str, str, str]:  # 返回: 全局记忆, 知识, 私聊历史回忆
    """
    检索三种类型的上下文信息：全局压缩记忆、知识库知识、当前私聊的特定历史对话。

    Args:
        text: 用于检索的上下文文本 (例如聊天记录)。
        private_name: 私聊对象的名称，用于日志记录。

    Returns:
        Tuple[str, str]: (检索到的记忆字符串, 检索到的知识字符串)
    """
    # 初始化返回值
    retrieved_global_memory_str = "无相关全局记忆。"
    retrieved_knowledge_str = "无相关知识。"
    retrieved_historical_chat_str = "无相关私聊历史回忆。"

    # --- 1. 全局压缩记忆检索 (来自 HippocampusManager) ---
    global_memory_log_msg = f"开始全局压缩记忆检索 (基于文本: '{text[:30]}...')"
    if text and text.strip() and text != "还没有聊天记录。" and text != "[构建聊天记录出错]":
        try:
            related_memory = await HippocampusManager.get_instance().get_memory_from_text(
                text=text,
                max_memory_num=2,
                max_memory_length=2,
                max_depth=3,
                fast_retrieval=False,
            )
            if related_memory:
                temp_global_memory_info = ""
                for memory_item in related_memory:
                    if isinstance(memory_item, (list, tuple)) and len(memory_item) > 1:
                        temp_global_memory_info += str(memory_item[1]) + "\n"
                    elif isinstance(memory_item, str):
                        temp_global_memory_info += memory_item + "\n"

                if temp_global_memory_info.strip():
                    retrieved_global_memory_str = f"你回忆起一些相关的记忆：\n{temp_global_memory_info.strip()}\n(以上是你的一些回忆，不一定是跟对方有关的，回忆里的人说的也不一定是事实，供参考)\n"
                    global_memory_log_msg = f"自动检索到全局压缩记忆: {temp_global_memory_info.strip()[:100]}..."
                else:
                    global_memory_log_msg = "全局压缩记忆检索返回为空或格式不符。"
            else:
                global_memory_log_msg = "全局压缩记忆检索返回为空列表。"
            logger.debug(f"[私聊][{private_name}] (retrieve_contextual_info) 全局压缩记忆检索: {global_memory_log_msg}")
        except Exception as e:
            logger.error(
                f"[私聊][{private_name}] (retrieve_contextual_info) 检索全局压缩记忆时出错: {e}\n{traceback.format_exc()}"
            )
            retrieved_global_memory_str = "[检索全局压缩记忆时出错]\n"
    else:
        logger.debug(f"[私聊][{private_name}] (retrieve_contextual_info) 无有效主查询文本，跳过全局压缩记忆检索。")

    # --- 2. 相关知识检索 (来自 prompt_builder) ---
    knowledge_log_msg = f"开始知识检索 (基于文本: '{text[:30]}...')"
    if text and text.strip() and text != "还没有聊天记录。" and text != "[构建聊天记录出错]":
        try:
            knowledge_result = await prompt_builder.get_prompt_info(
                message=text,
                threshold=0.38,
            )
            if knowledge_result and knowledge_result.strip():  # 确保结果不为空
                retrieved_knowledge_str = knowledge_result  # 直接使用返回结果，如果需要也可以包装
                knowledge_log_msg = f"自动检索到相关知识: {knowledge_result[:100]}..."
            else:
                knowledge_log_msg = "知识检索返回为空。"
            logger.debug(f"[私聊][{private_name}] (retrieve_contextual_info) 知识检索: {knowledge_log_msg}")
        except Exception as e:
            logger.error(
                f"[私聊][{private_name}] (retrieve_contextual_info) 自动检索知识时出错: {e}\n{traceback.format_exc()}"
            )
            retrieved_knowledge_str = "[检索知识时出错]\n"
    else:
        logger.debug(f"[私聊][{private_name}] (retrieve_contextual_info) 无有效主查询文本，跳过知识检索。")

    # --- 3. 当前私聊的特定历史对话上下文检索 ---
    query_for_historical_chat = (
        historical_chat_query_text if historical_chat_query_text and historical_chat_query_text.strip() else None
    )
    # historical_chat_log_msg 的初始化可以移到 try 块之后，根据实际情况赋值

    if query_for_historical_chat:
        try:
            # ---- 计算最终的、严格的搜索时间上限 ----
            # 1. 设置一个基础的、较大的时间回溯窗口，例如2小时 (7200秒)
            #    这个值可以从全局配置读取，如果没配置则使用默认值
            default_search_exclude_seconds = getattr(
                global_config, "pfc_historical_search_default_exclude_seconds", 7200
            )  # 默认2小时
            base_excluded_time_limit = time.time() - default_search_exclude_seconds

            final_search_upper_limit_time = base_excluded_time_limit
            if current_short_term_history_earliest_time is not None:
                # 我们希望找到的消息严格早于 short_term_history 的开始，减去一个小量确保不包含边界
                limit_from_short_term = current_short_term_history_earliest_time - 0.001
                final_search_upper_limit_time = min(base_excluded_time_limit, limit_from_short_term)
            log_earliest_time_str = "未提供"
            if current_short_term_history_earliest_time is not None:
                try:
                    log_earliest_time_str = f"{current_short_term_history_earliest_time} (即 {datetime.fromtimestamp(current_short_term_history_earliest_time).strftime('%Y-%m-%d %H:%M:%S')})"
                except Exception:
                    log_earliest_time_str = str(current_short_term_history_earliest_time)

            logger.debug(
                f"[{private_name}] (私聊历史) retrieve_contextual_info: "
                f"最终用于历史搜索的时间上限: {final_search_upper_limit_time} "
                f"(可读: {datetime.fromtimestamp(final_search_upper_limit_time).strftime('%Y-%m-%d %H:%M:%S')}). "
                f"基于默认排除 {default_search_exclude_seconds}s 和 '最近记录'片段开始时间: {log_earliest_time_str}"
            )

            most_relevant_message_doc = await find_most_relevant_historical_message(
                chat_id=chat_id,
                query_text=query_for_historical_chat,
                similarity_threshold=0.5,  # 您可以调整这个
                # exclude_recent_seconds 不再直接使用，而是传递计算好的绝对时间上限
                absolute_search_time_limit=final_search_upper_limit_time,
            )

            if most_relevant_message_doc:
                anchor_id = most_relevant_message_doc.get("message_id")
                anchor_time = most_relevant_message_doc.get("time")

                # 校验锚点时间是否真的符合我们的硬性上限 (理论上 find_most_relevant_historical_message 内部已保证)
                if anchor_time is not None and anchor_time >= final_search_upper_limit_time:
                    logger.warning(
                        f"[{private_name}] (私聊历史) find_most_relevant_historical_message 返回的锚点时间 {anchor_time} "
                        f"并未严格小于最终搜索上限 {final_search_upper_limit_time}。可能导致重叠。跳过构建上下文。"
                    )
                    historical_chat_log_msg = "检索到的锚点不符合最终时间要求，可能导致重叠。"
                    # 直接进入下一个分支 (else)，使得 retrieved_historical_chat_str 保持默认值
                elif anchor_id and anchor_time is not None:
                    # 构建上下文窗口时，其“未来”消息的上限也应该是 final_search_upper_limit_time
                    # 因为我们不希望历史回忆的上下文窗口延伸到“最近聊天记录”的范围内或更近
                    time_limit_for_context_window_after = final_search_upper_limit_time

                    logger.debug(
                        f"[{private_name}] (私聊历史) 调用 retrieve_chat_context_window "
                        f"with anchor_time: {anchor_time}, "
                        f"excluded_time_threshold_for_window: {time_limit_for_context_window_after}"
                    )

                    context_window_messages = await retrieve_chat_context_window(
                        chat_id=chat_id,
                        anchor_message_id=anchor_id,
                        anchor_message_time=anchor_time,
                        excluded_time_threshold_for_window=time_limit_for_context_window_after,
                        window_size_before=7,
                        window_size_after=7,
                    )
                    if context_window_messages:
                        formatted_window_str = await build_readable_messages(
                            context_window_messages,
                            replace_bot_name=False,  # 在回忆中，保留原始发送者名称
                            merge_messages=False,
                            timestamp_mode="relative",  # 可以选择 'absolute' 或 'none'
                            read_mark=0.0,
                        )
                        if formatted_window_str and formatted_window_str.strip():
                            retrieved_historical_chat_str = f"你还想到了一些你们之前的聊天记录：\n------\n{formatted_window_str.strip()}\n------\n(以上是你们之前的聊天记录，供参考)\n"
                            historical_chat_log_msg = f"自动检索到相关私聊历史片段 (锚点ID: {anchor_id}, 相似度: {most_relevant_message_doc.get('similarity'):.3f})"
                            return retrieved_global_memory_str, retrieved_knowledge_str, retrieved_historical_chat_str
                        else:
                            historical_chat_log_msg = "检索到的私聊历史对话窗口格式化后为空。"
                    else:
                        historical_chat_log_msg = f"找到了相关锚点消息 (ID: {anchor_id})，但未能构建其上下文窗口。"
                else:
                    historical_chat_log_msg = "检索到的最相关私聊历史消息文档缺少 message_id 或 time。"
            else:
                historical_chat_log_msg = "未找到足够相关的私聊历史对话消息。"
            logger.debug(
                f"[私聊][{private_name}] (retrieve_contextual_info) 私聊历史对话检索: {historical_chat_log_msg}"
            )
        except Exception as e:
            logger.error(
                f"[私聊][{private_name}] (retrieve_contextual_info) 检索私聊历史对话时出错: {e}\n{traceback.format_exc()}"
            )
            retrieved_historical_chat_str = "[检索私聊历史对话时出错]\n"
    else:
        logger.debug(
            f"[私聊][{private_name}] (retrieve_contextual_info) 无专门的私聊历史查询文本，跳过私聊历史对话检索。"
        )

    return retrieved_global_memory_str, retrieved_knowledge_str, retrieved_historical_chat_str


def get_items_from_json(
    content: str,
    private_name: str,
    *items: str,
    default_values: Optional[Dict[str, Any]] = None,
    required_types: Optional[Dict[str, type]] = None,
    allow_array: bool = True,
    allow_empty_string_fields: Optional[List[str]] = None,
) -> Tuple[bool, Union[Dict[str, Any], List[Dict[str, Any]]]]:
    """从文本中提取JSON内容并获取指定字段

    Args:
        content: 包含JSON的文本
        private_name: 私聊名称
        *items: 要提取的字段名
        default_values: 字段的默认值，格式为 {字段名: 默认值}
        required_types: 字段的必需类型，格式为 {字段名: 类型}
        allow_array: 是否允许解析JSON数组

    Returns:
        Tuple[bool, Union[Dict[str, Any], List[Dict[str, Any]]]]: (是否成功, 提取的字段字典或字典列表)
    """
    cleaned_content = content.strip()
    _result: Union[Dict[str, Any], List[Dict[str, Any]]] = {}
    markdown_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_content, re.IGNORECASE)
    if markdown_match:
        cleaned_content = markdown_match.group(1).strip()
        logger.debug(f"[私聊][{private_name}] 已去除 Markdown 标记，剩余内容: {cleaned_content[:100]}...")
    default_result: Dict[str, Any] = {}

    if default_values:
        default_result.update(default_values)
        # result = default_result.copy()
    _allow_empty_string_fields = allow_empty_string_fields if allow_empty_string_fields is not None else []

    if allow_array:
        try:
            json_array = json.loads(cleaned_content)
            if isinstance(json_array, list):
                valid_items_list: List[Dict[str, Any]] = []
                for item_json in json_array:
                    if not isinstance(item_json, dict):
                        logger.warning(f"[私聊][{private_name}] JSON数组中的元素不是字典: {item_json}")
                        continue

                    current_item_result = default_result.copy()
                    valid_item = True
                    for field in items:
                        if field in item_json:
                            current_item_result[field] = item_json[field]
                        elif field not in default_result:
                            logger.warning(f"[私聊][{private_name}] JSON数组元素缺少必要字段 '{field}': {item_json}")
                            valid_item = False
                            break

                    if not valid_item:
                        continue

                    if required_types:
                        for field, expected_type in required_types.items():
                            if field in current_item_result and not isinstance(
                                current_item_result[field], expected_type
                            ):
                                logger.warning(
                                    f"[私聊][{private_name}] JSON数组元素字段 '{field}' 类型错误 (应为 {expected_type.__name__}, 实际为 {type(current_item_result[field]).__name__}): {item_json}"
                                )
                                valid_item = False
                                break
                    if not valid_item:
                        continue

                    for field in items:
                        if (
                            field in current_item_result
                            and isinstance(current_item_result[field], str)
                            and not current_item_result[field].strip()
                            and field not in _allow_empty_string_fields
                        ):
                            logger.warning(
                                f"[私聊][{private_name}] JSON数组元素字段 '{field}' 不能为空字符串: {item_json}"
                            )
                            valid_item = False
                            break

                    if valid_item:
                        valid_items_list.append(current_item_result)

                if valid_items_list:
                    logger.debug(f"[私聊][{private_name}] 成功解析JSON数组，包含 {len(valid_items_list)} 个有效项目。")
                    return True, valid_items_list
                else:
                    logger.debug(f"[私聊][{private_name}] 解析为JSON数组，但未找到有效项目，尝试解析单个JSON对象。")
                    # result = default_result.copy()
        except json.JSONDecodeError:
            logger.debug(f"[私聊][{private_name}] JSON数组直接解析失败，尝试解析单个JSON对象")
            # result = default_result.copy()
        except Exception as e:
            logger.error(f"[私聊][{private_name}] 尝试解析JSON数组时发生未知错误: {str(e)}")
            # result = default_result.copy()

    json_data = None
    valid_single_object = True  # <--- 将初始化提前到这里

    try:
        json_data = json.loads(cleaned_content)
        if not isinstance(json_data, dict):
            logger.error(f"[私聊][{private_name}] 解析为单个对象，但结果不是字典类型: {type(json_data)}")
            # 如果不是字典，即使 allow_array 为 False，这里也应该认为单个对象解析失败
            valid_single_object = False  # 标记为无效
            # return False, default_result.copy() # 不立即返回，让后续逻辑统一处理 valid_single_object
    except json.JSONDecodeError:
        json_pattern = r"\{[\s\S]*?\}"
        json_match = re.search(json_pattern, cleaned_content)
        if json_match:
            try:
                potential_json_str = json_match.group(0)
                json_data = json.loads(potential_json_str)
                if not isinstance(json_data, dict):
                    logger.error(f"[私聊][{private_name}] 正则提取后解析，但结果不是字典类型: {type(json_data)}")
                    valid_single_object = False  # 标记为无效
                    # return False, default_result.copy()
                else:
                    logger.debug(f"[私聊][{private_name}] 通过正则提取并成功解析JSON对象。")
                    # valid_single_object 保持 True
            except json.JSONDecodeError:
                logger.error(f"[私聊][{private_name}] 正则提取的部分 '{potential_json_str[:100]}...' 无法解析为JSON。")
                valid_single_object = False  # 标记为无效
                # return False, default_result.copy()
        else:
            logger.error(
                f"[私聊][{private_name}] 无法在返回内容中找到有效的JSON对象部分。原始内容: {cleaned_content[:100]}..."
            )
            valid_single_object = False  # 标记为无效
            # return False, default_result.copy()

    # 如果前面的步骤未能成功解析出一个 dict 类型的 json_data，则 valid_single_object 会是 False
    if not isinstance(json_data, dict) or not valid_single_object:  # 增加对 json_data 类型的检查
        # 如果 allow_array 为 True 且数组解析成功过，这里不应该执行 (因为之前会 return True, valid_items_list)
        # 如果 allow_array 为 False，或者数组解析也失败了，那么到这里就意味着整体解析失败
        if not (allow_array and isinstance(json_array, list) and valid_items_list):  # 修正：检查之前数组解析是否成功
            logger.debug(f"[私聊][{private_name}] 未能成功解析为有效的JSON对象或数组。")
        return False, default_result.copy()

    # 如果成功解析了单个 JSON 对象 (json_data 是 dict 且 valid_single_object 仍为 True)
    # current_single_result 的初始化和填充逻辑可以保持
    current_single_result = default_result.copy()
    # valid_single_object = True # 这一行现在是多余的，因为在上面已经初始化并可能被修改

    for item_field in items:
        if item_field in json_data:
            current_single_result[item_field] = json_data[item_field]
        elif item_field not in default_result:
            logger.error(f"[私聊][{private_name}] JSON对象缺少必要字段 '{item_field}'。JSON内容: {json_data}")
            valid_single_object = False
            break

    if not valid_single_object:
        return False, default_result.copy()  # 如果字段缺失，则校验失败

    if required_types:
        for field, expected_type in required_types.items():
            if field in current_single_result and not isinstance(current_single_result[field], expected_type):
                logger.error(
                    f"[私聊][{private_name}] JSON对象字段 '{field}' 类型错误 (应为 {expected_type.__name__}, 实际为 {type(current_single_result[field]).__name__})"
                )
                valid_single_object = False
                break

    if not valid_single_object:
        return False, default_result.copy()  # 如果类型错误，则校验失败

    for field in items:
        if (
            field in current_single_result
            and isinstance(current_single_result[field], str)
            and not current_single_result[field].strip()
            and field not in _allow_empty_string_fields
        ):
            logger.error(f"[私聊][{private_name}] JSON对象字段 '{field}' 不能为空字符串 (除非特别允许)")
            valid_single_object = False
            break

    if valid_single_object:
        logger.debug(f"[私聊][{private_name}] 成功解析并验证了单个JSON对象。")
        return True, current_single_result
    else:
        return False, default_result.copy()


async def get_person_id(private_name: str, chat_stream: ChatStream):
    """(保持你原始 pfc_utils.py 中的此函数代码不变)"""
    private_user_id_str: Optional[str] = None
    private_platform_str: Optional[str] = None

    if chat_stream.user_info:
        private_user_id_str = str(chat_stream.user_info.user_id)
        private_platform_str = chat_stream.user_info.platform
        logger.debug(
            f"[私聊][{private_name}] 从 ChatStream 获取到私聊对象信息: ID={private_user_id_str}, Platform={private_platform_str}, Name={private_name}"
        )

    if private_user_id_str and private_platform_str:
        try:
            private_user_id_int = int(private_user_id_str)
            person_id = await person_info_manager.get_or_create_person(
                platform=private_platform_str,
                user_id=private_user_id_int,
                nickname=private_name,
            )
            if person_id is None:
                logger.error(f"[私聊][{private_name}] get_or_create_person 未能获取或创建 person_id。")
                return None
            return person_id, private_platform_str, private_user_id_str
        except ValueError:
            logger.error(f"[私聊][{private_name}] 无法将 private_user_id_str ('{private_user_id_str}') 转换为整数。")
            return None
        except Exception as e_pid:
            logger.error(f"[私聊][{private_name}] 获取或创建 person_id 时出错: {e_pid}")
            return None
    else:
        logger.warning(
            f"[私聊][{private_name}] 未能确定私聊对象的 user_id 或 platform，无法获取 person_id。将在收到消息后尝试。"
        )
        return None


async def adjust_relationship_value_nonlinear(old_value: float, raw_adjustment: float) -> float:
    """(保持你原始 pfc_utils.py 中的此函数代码不变)"""
    old_value = max(-1000, min(1000, old_value))
    value = raw_adjustment
    if old_value >= 0:
        if value >= 0:
            value = value * math.cos(math.pi * old_value / 2000)
            if old_value > 500:
                # 确保 person_info_manager.get_specific_value_list 是异步的，如果是同步则需要调整
                rdict = await person_info_manager.get_specific_value_list(
                    "relationship_value", lambda x: x > 700 if isinstance(x, (int, float)) else False
                )
                high_value_count = len(rdict)
                if old_value > 700:
                    value *= 3 / (high_value_count + 2)
                else:
                    value *= 3 / (high_value_count + 3)
        elif value < 0:
            value = value * math.exp(old_value / 2000)
        # else: value = 0 # 你原始代码中没有这句，如果value为0，保持为0
    else:  # old_value < 0
        if value >= 0:
            value = value * math.exp(old_value / 2000)
        elif value < 0:
            value = value * math.cos(math.pi * old_value / 2000)
        # else: value = 0 # 你原始代码中没有这句
    return value


async def build_chat_history_text(observation_info: ObservationInfo, private_name: str) -> str:
    """构建聊天历史记录文本 (包含未处理消息)"""
    chat_history_text = ""
    try:
        if hasattr(observation_info, "chat_history_str") and observation_info.chat_history_str:
            chat_history_text = observation_info.chat_history_str
        elif hasattr(observation_info, "chat_history") and observation_info.chat_history:
            history_slice = observation_info.chat_history[-global_config.pfc_recent_history_display_count :]
            chat_history_text = await build_readable_messages(
                history_slice, replace_bot_name=True, merge_messages=False, timestamp_mode="relative", read_mark=0.0
            )
        else:
            chat_history_text = "还没有聊天记录。\n"

        unread_count = getattr(observation_info, "new_messages_count", 0)
        unread_messages = getattr(observation_info, "unprocessed_messages", [])
        if unread_count > 0 and unread_messages:
            bot_qq_str = str(global_config.BOT_QQ) if global_config.BOT_QQ else None  # 安全获取
            if bot_qq_str:
                other_unread_messages = [
                    msg for msg in unread_messages if msg.get("user_info", {}).get("user_id") != bot_qq_str
                ]
                other_unread_count = len(other_unread_messages)
                if other_unread_count > 0:
                    new_messages_str = await build_readable_messages(
                        other_unread_messages,
                        replace_bot_name=True,
                        merge_messages=False,
                        timestamp_mode="relative",
                        read_mark=0.0,
                    )
                    chat_history_text += f"\n{new_messages_str}\n------\n"
            else:
                logger.warning(f"[私聊][{private_name}] BOT_QQ 未配置，无法准确过滤未读消息中的机器人自身消息。")

    except AttributeError as e:
        logger.warning(f"[私聊][{private_name}] 构建聊天记录文本时属性错误: {e}")
        chat_history_text = "[获取聊天记录时出错]\n"
    except Exception as e:
        logger.error(f"[私聊][{private_name}] 处理聊天记录时发生未知错误: {e}")
        chat_history_text = "[处理聊天记录时出错]\n"
    return chat_history_text
