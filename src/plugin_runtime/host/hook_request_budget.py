"""Maisaka 请求 Hook 的完整帧预算，不改变 SDK 载荷结构。"""

from typing import Any, Dict, List, Sequence, Tuple

from src.common.logger import get_logger
from src.maisaka.visual.image_budget import IMAGE_BUDGET_PLACEHOLDER, MAX_REQUEST_IMAGE_BYTES
from src.plugin_runtime.protocol.codec import FrameTooLargeError, MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.transport.base import MAX_FRAME_SIZE

logger = get_logger("plugin_runtime.host.hook_request_budget")
REQUEST_IMAGE_HOOKS = {"maisaka.planner.before_request", "maisaka.replyer.before_model_request"}


def fit_request_hook_kwargs(
    hook_name: str,
    kwargs: Dict[str, Any],
    *,
    targets: Sequence[Tuple[str, str, int]],
) -> Dict[str, Any]:
    """为所有接收者预留完整信封，优先保留新图片；纯文字超限直接报错。

    request_id / timestamp 使用协议整数的最大编码宽度，实际编码器再校验一次。
    返回独立的可修改容器，图片字符串仍共享；不复制 base64 数据。
    """
    if hook_name not in REQUEST_IMAGE_HOOKS or not targets:
        return kwargs
    codec = MsgPackCodec()
    result = dict(kwargs)
    images: List[Tuple[List[Any], int, Dict[str, Any]]] = []
    placeholder = {"type": "text", "text": IMAGE_BUDGET_PLACEHOLDER}
    raw_items = kwargs.get("items")
    if isinstance(raw_items, list):
        items = list(raw_items)
        result["items"] = items
        for index, item in enumerate(raw_items):
            if (
                not isinstance(item, dict)
                or item.get("item_type")
                not in {
                    "SystemMessageItem",
                    "UserMessageItem",
                    "AssistantMessageItem",
                }
                or not isinstance(item.get("parts"), list)
            ):
                continue
            parts = list(item["parts"])
            items[index] = {**item, "parts": parts}
            for part_index, part in enumerate(parts):
                if isinstance(part, dict) and part.get("type") == "image":
                    images.append((parts, part_index, part))
                    parts[part_index] = placeholder

    def frame_size(arguments: Dict[str, Any]) -> int:
        largest = 0
        for plugin_id, component_name, timeout_ms in targets:
            envelope = Envelope(
                request_id=2**64 - 1,
                timestamp_ms=2**64 - 1,
                message_type=MessageType.REQUEST,
                method="plugin.invoke_hook",
                plugin_id=plugin_id,
                timeout_ms=timeout_ms,
                payload={"component_name": component_name, "args": {"hook_name": hook_name, **arguments}},
            )
            largest = max(largest, codec.encoded_size(envelope.model_dump()))
        return largest

    image_total = sum(
        len(part["image_base64"]) if isinstance(part.get("image_base64"), str) else MAX_FRAME_SIZE + 1
        for _, _, part in images
    )
    # 正常帧直接返回，避免极短图片的文字占位反而扩大临界帧。
    if image_total <= MAX_REQUEST_IMAGE_BYTES and frame_size(kwargs) <= MAX_FRAME_SIZE:
        return kwargs
    base_size = frame_size(result)
    if base_size > MAX_FRAME_SIZE:
        raise FrameTooLargeError(base_size)

    remaining = MAX_FRAME_SIZE - base_size
    image_remaining = MAX_REQUEST_IMAGE_BYTES
    placeholder_size = codec.encoded_size(placeholder)
    removed = 0
    for parts, index, part in reversed(images):
        encoded_size = codec.encoded_size(part)
        image_data = part.get("image_base64")
        image_size = len(image_data) if isinstance(image_data, str) else MAX_FRAME_SIZE + 1
        extra = encoded_size - placeholder_size
        if extra <= remaining and image_size <= image_remaining:
            parts[index] = part
            remaining -= extra
            image_remaining -= image_size
        else:
            removed += 1
    if removed:
        logger.warning(f"Hook {hook_name} 因完整帧或图片预算省略 {removed} 张图片；原始媒体未修改")
    return result
