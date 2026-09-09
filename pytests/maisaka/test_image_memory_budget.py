"""合成图片的请求预算回归，不访问模型或图片服务。"""

import pytest

from src.llm_models.payload_content.context_item import ContextImagePart, ContextItemBuilder
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages
from src.plugin_runtime.hook_payloads import serialize_prompt_items
from src.plugin_runtime.protocol.codec import MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.protocol.errors import RPCError
from src.plugin_runtime.transport.base import MAX_FRAME_SIZE


def image_message(size):
    return ContextItemBuilder().add_image_content("png", "A" * size).build()


@pytest.mark.parametrize("sizes", [(18 * 1024 * 1024,), (6 * 1024 * 1024,) * 3])
def test_limited_hook_request_fits_frame(sizes):
    originals = [image_message(size) for size in sizes]
    limited = limit_latest_images_in_messages(originals, max_image_num=20)
    envelope = Envelope(
        request_id=1,
        message_type=MessageType.REQUEST,
        method="plugin.invoke_hook",
        payload={
            "component_name": "synthetic",
            "args": {
                "hook_name": "maisaka.planner.before_request",
                "items": serialize_prompt_items(limited),
                "tool_definitions": [{"name": "test", "description": "工具说明" * 100}],
            },
        },
    )
    payload = MsgPackCodec().encode_envelope(envelope)
    assert len(payload) <= MAX_FRAME_SIZE
    assert all(isinstance(item.parts[0], ContextImagePart) for item in originals)


def test_envelope_rejects_oversize_before_pack(monkeypatch):
    codec = MsgPackCodec()
    envelope = Envelope(request_id=1, message_type=MessageType.REQUEST, payload={"text": "x" * MAX_FRAME_SIZE})

    def forbid_encode(_obj):
        pytest.fail("超大帧不应先分配完整 MsgPack 副本")

    monkeypatch.setattr(codec, "encode", forbid_encode)
    with pytest.raises(RPCError, match="帧"):
        codec.encode_envelope(envelope)


def test_count_window_order_and_small_images():
    originals = [image_message(4), image_message(8), image_message(12)]
    assert limit_latest_images_in_messages(originals, max_image_num=3) == originals
    limited = limit_latest_images_in_messages(originals, max_image_num=2, max_image_bytes=12)
    assert limited[0].parts[0].text == "[图片]"
    assert "字节预算" in limited[1].parts[0].text
    assert limited[2] is originals[2]
    assert all(item.parts[0].text == "[图片]" for item in limit_latest_images_in_messages(originals, max_image_num=0))
    assert all(item.parts[0].text == "[图片]" for item in limit_latest_images_in_messages(originals, max_image_num=-1))


@pytest.mark.parametrize("limit,expected", [(7, False), (8, True), (9, True)])
def test_image_byte_boundary(limit, expected):
    limited = limit_latest_images_in_messages([image_message(8)], max_image_num=1, max_image_bytes=limit)
    assert isinstance(limited[0].parts[0], ContextImagePart) is expected
