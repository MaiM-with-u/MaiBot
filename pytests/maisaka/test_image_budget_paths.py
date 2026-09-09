"""通过真实 Planner/Replyer 请求路径验证预算与原图隔离。"""

from datetime import datetime
from io import BytesIO
from PIL import Image
from types import SimpleNamespace
from unittest.mock import AsyncMock

import base64
import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.message_component_data_model import EmojiComponent, ImageComponent, MessageSequence
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItemBuilder,
    ContextItemMeta,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ProviderReplayFragment,
    ProviderScope,
)
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.context.messages import SessionBackedMessage
from src.maisaka.visual.history_image_limiter import limit_history_images
from src.maisaka.visual.image_budget import MAX_REQUEST_IMAGE_BYTES
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages
from src.plugin_runtime.hook_payloads import serialize_prompt_items
from src.plugin_runtime.protocol.codec import FrameTooLargeError


def make_history(size, component_class=ImageComponent):
    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, "PNG")
    data = buffer.getvalue().ljust(size, b"x")
    component = component_class(binary_hash="", binary_data=data, content="合成测试图片")
    return SessionBackedMessage(
        raw_message=MessageSequence([component]),
        visible_text="合成测试图片",
        timestamp=datetime(2026, 1, 1),
        message_id="synthetic",
    )


@pytest.mark.parametrize("component_class", [ImageComponent, EmojiComponent])
def test_pre_base64_limit_preserves_components_and_raw_bytes(monkeypatch, component_class):
    history = [make_history(4 * 1024 * 1024, component_class) for _ in range(3)]
    original_components = [item.raw_message.components[0] for item in history]
    projected = limit_history_images(history, max_image_num=10)
    calls = []
    encode = base64.b64encode

    def tracked_encode(data):
        calls.append(len(data))
        return encode(data)

    monkeypatch.setattr(base64, "b64encode", tracked_encode)
    items = [item.to_context_item() for item in projected]
    assert calls == [4 * 1024 * 1024]
    assert isinstance(items[-1].parts[-1], ContextImagePart)
    assert [item.raw_message.components[0] for item in history] == original_components
    assert all(len(component.binary_data) == 4 * 1024 * 1024 for component in original_components)


class RequestObserved(Exception):
    pass


@pytest.mark.asyncio
async def test_planner_limits_before_hook_and_after_hook_injection(monkeypatch):
    service = MaisakaChatLoopService(chat_system_prompt="测试系统提示词")
    history = [make_history(14 * 1024 * 1024), make_history(1024)]
    big_item = ContextItemBuilder().add_image_content("png", "A" * (MAX_REQUEST_IMAGE_BYTES + 4)).build()
    observed = []

    async def invoke_hook(name, **kwargs):
        assert name == "maisaka.planner.before_request"
        images = [part for item in kwargs["items"] for part in item.get("parts", []) if part["type"] == "image"]
        assert len(images) == 1
        assert len(images[0]["image_base64"]) < 2048
        kwargs["items"].extend(serialize_prompt_items([big_item]))
        return SimpleNamespace(kwargs=kwargs)

    async def generate(context_factory, options):
        observed.extend(context_factory(None))
        raise RequestObserved

    monkeypatch.setattr(service, "_resolve_enable_visual_message", lambda _kind: True)
    monkeypatch.setattr(service, "select_llm_context_messages", lambda *args, **kwargs: (history, "synthetic"))
    monkeypatch.setattr(service, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=invoke_hook))
    monkeypatch.setattr(
        service, "_get_llm_chat_client", lambda _kind: SimpleNamespace(generate_response_with_context=generate)
    )
    with pytest.raises(RequestObserved):
        await service.chat_loop_step(history, tool_definitions=[])
    images = [part for item in observed for part in item.parts if isinstance(part, ContextImagePart)]
    assert len(images) == 1
    assert sum(len(part.image_base64) for part in images) <= MAX_REQUEST_IMAGE_BYTES
    assert len(history[0].raw_message.components[0].binary_data) == 14 * 1024 * 1024


@pytest.mark.asyncio
async def test_replyer_limits_construction_and_hook_modified_images(monkeypatch):
    generator = BaseMaisakaReplyGenerator.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    history = [make_history(14 * 1024 * 1024), make_history(1024)]
    # 测试真实历史投影，提示词构造与网络服务不参与此次内存边界。
    items = generator._build_history_messages(history, True)
    assert sum(isinstance(p, ContextImagePart) for item in items for p in item.parts) == 1
    injected = ContextItemBuilder().add_image_content("png", "A" * (MAX_REQUEST_IMAGE_BYTES + 4)).build()

    async def invoke_hook(name, **kwargs):
        assert name == "maisaka.replyer.before_model_request"
        kwargs["items"].extend(serialize_prompt_items([injected]))
        return SimpleNamespace(kwargs=kwargs)

    monkeypatch.setattr(generator, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=invoke_hook))
    result = await generator._invoke_before_model_request_hook(
        request_messages=items,
        session_id="synthetic",
        active_task_name="replyer",
        active_model_name=None,
        model_info=None,
        attempt=1,
        retry_count=0,
        reply_message=None,
        reply_reason="",
        selected_expression_ids=[],
        reply_tool_args={},
    )
    assert sum(isinstance(p, ContextImagePart) for item in result for p in item.parts) == 1
    assert "字节预算" in result[-1].parts[0].text


def test_tool_structure_replay_and_originals_are_preserved():
    meta = ContextItemMeta.create(logical_turn_id="turn")
    call = FunctionCallItem(
        meta, ContextToolCall.create(call_id="call", func_name="test", args={"image_hash": "synthetic"})
    )
    output = FunctionCallOutputItem(meta, "call", "正常工具结果")
    scope = ProviderScope(1, "openai", "synthetic", "endpoint", "model")
    replay = ProviderReplayFragment.from_payload(scope, {"id": "synthetic"})
    small = AssistantMessageItem(meta, (ContextImagePart("png", "AAAA"),), replay=replay)
    big = AssistantMessageItem(meta, (ContextImagePart("png", "A" * (MAX_REQUEST_IMAGE_BYTES + 4)),), replay=replay)
    result = limit_latest_images_in_messages([call, output, small, big], max_image_num=20)
    assert result[:3] == [call, output, small]
    assert result[0] is call and result[1] is output and result[2] is small
    assert result[-1].replay is None
    assert big.replay is replay
    assert result[-1].meta is meta


@pytest.mark.asyncio
async def test_replyer_does_not_swallow_host_frame_error(monkeypatch):
    generator = BaseMaisakaReplyGenerator.__new__(BaseMaisakaReplyGenerator)
    generator.request_type = "maisaka.replyer"
    monkeypatch.setattr(
        generator,
        "_get_runtime_manager",
        lambda: SimpleNamespace(invoke_hook=AsyncMock(side_effect=FrameTooLargeError(20_000_000))),
    )
    with pytest.raises(FrameTooLargeError):
        await generator._invoke_before_model_request_hook(
            request_messages=[],
            session_id="synthetic",
            active_task_name="replyer",
            active_model_name=None,
            model_info=None,
            attempt=1,
            retry_count=0,
            reply_message=None,
            reply_reason="",
            selected_expression_ids=[],
            reply_tool_args={},
        )
