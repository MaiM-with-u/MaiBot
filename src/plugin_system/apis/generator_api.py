"""
回复器API模块

提供回复器相关功能，采用标准Python包设计模式
使用方式：
    from src.plugin_system.apis import generator_api
    replyer = generator_api.get_replyer(chat_stream)
    success, reply_set, _ = await generator_api.generate_reply(chat_stream, action_data, reasoning)
"""

import traceback
from typing import Tuple, Any, Dict, List, Optional
from rich.traceback import install
from src.common.logger import get_logger
from src.chat.replyer.default_generator import DefaultReplyer
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.utils.utils import process_llm_response
from src.chat.replyer.replyer_manager import replyer_manager
from src.plugin_system.base.component_types import ActionInfo

install(extra_lines=3)

logger = get_logger("generator_api")


# =============================================================================
# 回复器获取API函数
# =============================================================================


def get_replyer(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    model_configs: Optional[List[Dict[str, Any]]] = None,
    request_type: str = "replyer",
) -> Optional[DefaultReplyer]:
    """获取回复器对象

    优先使用chat_stream，如果没有则使用chat_id直接查找。
    使用 ReplyerManager 来管理实例，避免重复创建。

    Args:
        chat_stream: 聊天流对象（优先）
        chat_id: 聊天ID（实际上就是stream_id）
        model_configs: 模型配置列表
        request_type: 请求类型

    Returns:
        Optional[DefaultReplyer]: 回复器对象，如果获取失败则返回None

    Raises:
        ValueError: chat_stream 和 chat_id 均为空
    """
    if not chat_id and not chat_stream:
        raise ValueError("chat_stream 和 chat_id 不可均为空")
    try:
        logger.debug(f"[GeneratorAPI] 正在获取回复器，chat_id: {chat_id}, chat_stream: {'有' if chat_stream else '无'}")
        return replyer_manager.get_replyer(
            chat_stream=chat_stream,
            chat_id=chat_id,
            model_configs=model_configs,
            request_type=request_type,
        )
    except Exception as e:
        logger.error(f"[GeneratorAPI] 获取回复器时发生意外错误: {e}", exc_info=True)
        traceback.print_exc()
        return None


# =============================================================================
# 回复生成API函数
# =============================================================================


async def generate_reply(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    action_data: Optional[Dict[str, Any]] = None,
    reply_to: str = "",
    extra_info: str = "",
    available_actions: Optional[Dict[str, ActionInfo]] = None,
    enable_tool: bool = False,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    return_prompt: bool = False,
    model_configs: Optional[List[Dict[str, Any]]] = None,
    request_type: str = "",
    enable_timeout: bool = False,
) -> Tuple[bool, List[Tuple[str, Any]], Optional[str]]:
    """生成回复

    Args:
        chat_stream: 聊天流对象（优先）
        chat_id: 聊天ID（备用）
        action_data: 动作数据
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
        return_prompt: 是否返回提示词
    Returns:
        Tuple[bool, List[Tuple[str, Any]], Optional[str]]: (是否成功, 回复集合, 提示词)
    """
    try:
        # 获取回复器
        replyer = get_replyer(chat_stream, chat_id, model_configs=model_configs, request_type=request_type)
        if not replyer:
            logger.error("[GeneratorAPI] 无法获取回复器")
            return False, [], None

        logger.debug("[GeneratorAPI] 开始生成回复")
        
        if not reply_to and action_data:
            reply_to = action_data.get("reply_to", "")
        if not extra_info and action_data:
            extra_info = action_data.get("extra_info", "")

        # 调用回复器生成回复
        success, content, prompt = await replyer.generate_reply_with_context(
            reply_to=reply_to,
            extra_info=extra_info,
            available_actions=available_actions,
            enable_timeout=enable_timeout,
            enable_tool=enable_tool,
        )
        reply_set = []
        if content:
            reply_set = await process_human_text(content, enable_splitter, enable_chinese_typo)

        if success:
            logger.debug(f"[GeneratorAPI] 回复生成成功，生成了 {len(reply_set)} 个回复项")
        else:
            logger.warning("[GeneratorAPI] 回复生成失败")

        if return_prompt:
            return success, reply_set, prompt
        else:
            return success, reply_set, None

    except ValueError as ve:
        raise ve

    except Exception as e:
        logger.error(f"[GeneratorAPI] 生成回复时出错: {e}")
        logger.error(traceback.format_exc())
        return False, [], None


async def rewrite_reply(
    chat_stream: Optional[ChatStream] = None,
    reply_data: Optional[Dict[str, Any]] = None,
    chat_id: Optional[str] = None,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    model_configs: Optional[List[Dict[str, Any]]] = None,
    raw_reply: str = "",
    reason: str = "",
    reply_to: str = "",
) -> Tuple[bool, List[Tuple[str, Any]]]:
    """重写回复

    Args:
        chat_stream: 聊天流对象（优先）
        reply_data: 回复数据字典（备用，当其他参数缺失时从此获取）
        chat_id: 聊天ID（备用）
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
        model_configs: 模型配置列表
        raw_reply: 原始回复内容
        reason: 回复原因
        reply_to: 回复对象

    Returns:
        Tuple[bool, List[Tuple[str, Any]]]: (是否成功, 回复集合)
    """
    try:
        # 获取回复器
        replyer = get_replyer(chat_stream, chat_id, model_configs=model_configs)
        if not replyer:
            logger.error("[GeneratorAPI] 无法获取回复器")
            return False, []

        logger.info("[GeneratorAPI] 开始重写回复")

        # 如果参数缺失，从reply_data中获取
        if reply_data:
            raw_reply = raw_reply or reply_data.get("raw_reply", "")
            reason = reason or reply_data.get("reason", "")
            reply_to = reply_to or reply_data.get("reply_to", "")

        # 调用回复器重写回复
        success, content = await replyer.rewrite_reply_with_context(
            raw_reply=raw_reply,
            reason=reason,
            reply_to=reply_to,
        )
        reply_set = []
        if content:
            reply_set = await process_human_text(content, enable_splitter, enable_chinese_typo)

        if success:
            logger.info(f"[GeneratorAPI] 重写回复成功，生成了 {len(reply_set)} 个回复项")
        else:
            logger.warning("[GeneratorAPI] 重写回复失败")

        return success, reply_set

    except ValueError as ve:
        raise ve

    except Exception as e:
        logger.error(f"[GeneratorAPI] 重写回复时出错: {e}")
        return False, []


async def process_human_text(content: str, enable_splitter: bool, enable_chinese_typo: bool) -> List[Tuple[str, Any]]:
    """将文本处理为更拟人化的文本

    Args:
        content: 文本内容
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
    """
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串类型")
    try:
        processed_response = process_llm_response(content, enable_splitter, enable_chinese_typo)

        reply_set = []
        for text in processed_response:
            reply_seg = ("text", text)
            reply_set.append(reply_seg)

        return reply_set

    except Exception as e:
        logger.error(f"[GeneratorAPI] 处理人形文本时出错: {e}")
        return []
