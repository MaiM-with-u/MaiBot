"""多模态消息图片数量和字节限制工具。"""

from dataclasses import replace
from typing import List, Sequence, Tuple

from src.common.logger import get_logger
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextContentPart,
    ContextImagePart,
    ContextItem,
    ContextTextPart,
    SystemMessageItem,
    UserMessageItem,
)

from .image_budget import IMAGE_COUNT_PLACEHOLDER, MAX_REQUEST_IMAGE_BYTES, image_replacements

IMAGE_LIMIT_PLACEHOLDER = IMAGE_COUNT_PLACEHOLDER


logger = get_logger("maisaka_image_budget")


def limit_latest_images_in_messages(
    messages: Sequence[ContextItem],
    *,
    max_image_num: int,
    placeholder: str = IMAGE_LIMIT_PLACEHOLDER,
    max_image_bytes: int = MAX_REQUEST_IMAGE_BYTES,
) -> List[ContextItem]:
    """限制 prompt 中的图片数量和累计 base64 字节，优先保留最新图片。

    超出数量的旧图片会被替换为文本占位，避免多模态模型收到过多图片。
    """

    sizes: List[int] = []
    image_positions: List[Tuple[int, int]] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
            continue
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ContextImagePart):
                image_positions.append((message_index, part_index))
                # base64 为 ASCII；非 ASCII 输入交由完整帧预检按 UTF-8 计算。
                sizes.append(len(part.image_base64))

    replacements = {
        image_positions[index]: placeholder if reason == IMAGE_COUNT_PLACEHOLDER else reason
        for index, reason in image_replacements(
            sizes, max_image_num=max_image_num, max_image_bytes=max_image_bytes
        ).items()
    }
    if not replacements:
        return list(messages)
    logger.warning(f"请求上下文省略 {len(replacements)} 张图片（数量或字节预算），原始媒体未修改")
    limited_messages: List[ContextItem] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
            limited_messages.append(message)
            continue
        limited_parts: List[ContextContentPart] = []
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ContextImagePart) and (message_index, part_index) in replacements:
                limited_parts.append(ContextTextPart(replacements[(message_index, part_index)]))
                continue
            limited_parts.append(part)

        if tuple(limited_parts) == message.parts:
            limited_messages.append(message)
        elif isinstance(message, AssistantMessageItem):
            limited_messages.append(replace(message, parts=tuple(limited_parts), replay=None))
        else:
            limited_messages.append(replace(message, parts=tuple(limited_parts)))

    return limited_messages
