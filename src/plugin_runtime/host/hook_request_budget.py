"""Maisaka 请求 Hook 的完整帧预算，不改变 SDK 载荷结构。"""

from typing import Any, Dict, List, Sequence, Tuple

from src.common.logger import get_logger
from src.maisaka.visual.image_budget import IMAGE_BUDGET_PLACEHOLDER, MAX_REQUEST_IMAGE_BYTES
from src.plugin_runtime.protocol.codec import FrameTooLargeError, MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.transport.base import MAX_FRAME_SIZE

logger = get_logger("plugin_runtime.host.hook_request_budget")
REQUEST_IMAGE_HOOKS = {"maisaka.planner.before_request", "maisaka.replyer.before_model_request"}


class HookRequestBudget:
    """一次分发共享固定信封开销，每次发送重新核算可变参数，不持有图片载荷。"""

    def __init__(self, hook_name: str, targets: Sequence[Tuple[str, str, int]]) -> None:
        self._hook_name = hook_name
        self._codec = MsgPackCodec()
        self._frame_overhead = 0
        if hook_name not in REQUEST_IMAGE_HOOKS:
            return
        for plugin_id, component_name, timeout_ms in targets:
            envelope = Envelope(
                request_id=2**64 - 1,
                timestamp_ms=2**64 - 1,
                message_type=MessageType.REQUEST,
                method="plugin.invoke_hook",
                plugin_id=plugin_id,
                timeout_ms=timeout_ms,
                payload={"component_name": component_name, "args": {}},
            )
            # 只扣除空 args 的编码；拟合时完整重算参数 map，涵盖 map 头宽度变化。
            # request_id / timestamp 预留最大整数宽度，实际发送前仍由编码器校验。
            overhead = self._codec.encoded_size(envelope.model_dump()) - self._codec.encoded_size({})
            self._frame_overhead = max(self._frame_overhead, overhead)

    def fit(self, kwargs: Dict[str, Any], *, outbound: bool = True) -> Dict[str, Any]:
        """优先保留新图片；非图片载荷超限明确报错，不复制 base64 或修改共享历史。

        outbound=False 用于链末本地返回：继续限制图片和参数大小，但不预留
        不会发送的请求信封；Runner 响应的真实帧边界已由协议编码器检查。
        """
        if self._hook_name not in REQUEST_IMAGE_HOOKS:
            return kwargs
        codec = self._codec
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
            if not outbound:
                return codec.encoded_size(arguments)
            return self._frame_overhead + codec.encoded_size({"hook_name": self._hook_name, **arguments})

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
            logger.warning(f"Hook {self._hook_name} 因完整帧或图片预算省略 {removed} 张图片；原始媒体未修改")
        return result
