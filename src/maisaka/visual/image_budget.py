"""请求图片预算；不改变原始媒体和插件 Item 协议。"""

from typing import Dict, Sequence

# 图片 base64 最多占用半个 RPC 帧，其余空间留给文字、工具和元数据。
# 这不是完整帧大小保证；协议编码器仍需检查实际完整帧。
MAX_REQUEST_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_BUDGET_PLACEHOLDER = "[图片未附加：超出本轮图片字节预算]"
IMAGE_COUNT_PLACEHOLDER = "[图片]"


def image_replacements(
    sizes: Sequence[int], *, max_image_num: int, max_image_bytes: int = MAX_REQUEST_IMAGE_BYTES
) -> Dict[int, str]:
    """先保持最新 N 张的原有语义，再从新到旧分配字节预算。

    超大图片不阻挡同一数量窗口内更小的图片；数量窗口外的旧图不会补回。
    """
    first_kept = max(0, len(sizes) - max(0, int(max_image_num)))
    replacements = {index: IMAGE_COUNT_PLACEHOLDER for index in range(first_kept)}
    remaining = max(0, max_image_bytes)
    for index in range(len(sizes) - 1, first_kept - 1, -1):
        if sizes[index] > remaining:
            replacements[index] = IMAGE_BUDGET_PLACEHOLDER
        else:
            remaining -= sizes[index]
    return replacements
