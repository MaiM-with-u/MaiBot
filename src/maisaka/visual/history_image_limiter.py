"""在历史图片转 base64 之前构造受预算限制的请求视图。"""

from dataclasses import replace
from typing import Dict, List, Sequence, Tuple

from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.logger import get_logger
from src.maisaka.context.messages import (
    ComplexSessionMessage,
    LLMContextMessage,
    SessionBackedMessage,
    _guess_image_format,
)

from .image_budget import MAX_REQUEST_IMAGE_BYTES, image_replacements


logger = get_logger("maisaka_image_budget")


def limit_history_images(
    history: Sequence[LLMContextMessage],
    *,
    max_image_num: int,
    max_image_bytes: int = MAX_REQUEST_IMAGE_BYTES,
) -> List[LLMContextMessage]:
    """仅替换请求副本中的图片，不修改共享历史、原图字节或磁盘缓存。"""
    positions: List[Tuple[int, int]] = []
    sizes: List[int] = []
    for message_index, message in enumerate(history):
        if not isinstance(message, SessionBackedMessage) or isinstance(message, ComplexSessionMessage):
            continue
        for part_index, component in enumerate(message.raw_message.components):
            if not isinstance(component, (ImageComponent, EmojiComponent)):
                continue
            if not component.binary_data or not _guess_image_format(component.binary_data):
                continue
            positions.append((message_index, part_index))
            sizes.append(4 * ((len(component.binary_data) + 2) // 3))

    by_message: Dict[int, Dict[int, str]] = {}
    for index, reason in image_replacements(
        sizes, max_image_num=max_image_num, max_image_bytes=max_image_bytes
    ).items():
        message_index, part_index = positions[index]
        by_message.setdefault(message_index, {})[part_index] = reason
    if by_message:
        logger.warning(
            f"构造图片请求前省略 {sum(len(parts) for parts in by_message.values())} 张图片（数量或字节预算）"
        )
    result = list(history)
    for message_index, replacements in by_message.items():
        message = history[message_index]
        assert isinstance(message, SessionBackedMessage)
        components = [
            TextComponent(replacements[index]) if index in replacements else component
            for index, component in enumerate(message.raw_message.components)
        ]
        result[message_index] = replace(message, raw_message=MessageSequence(components))
    return result
