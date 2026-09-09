"""RPC 完整帧边界和实际 Runner 错误响应回归。"""

from datetime import date, datetime
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock

import tracemalloc

import msgpack
import pytest

from src.plugin_runtime.host.hook_dispatcher import HookDispatcher
from src.plugin_runtime.host.hook_request_budget import HookRequestBudget
from src.plugin_runtime.host.rpc_server import RPCServer
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.protocol.codec import FrameTooLargeError, MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.runner.rpc_client import RPCClient
from src.plugin_runtime.transport.base import MAX_FRAME_SIZE


@pytest.mark.parametrize("size", [0, 1, 15, 16, 31, 32, 255, 256, 65535, 65536])
def test_size_matches_msgpack_at_type_boundaries(size):
    codec = MsgPackCodec()
    obj = {
        "str": "x" * size,
        "unicode": "图片😀" * size,
        "bin": b"x" * size,
        "array": [None] * size,
        "map": {str(i): False for i in range(size)},
        "ext": msgpack.ExtType(3, b"x" * size),
    }
    assert codec.encoded_size(obj) == len(codec.encode(obj))


def test_size_matches_extensions_and_scalars():
    class Number(Enum):
        VALUE = 25

    codec = MsgPackCodec()
    obj = {
        "values": [
            None,
            True,
            False,
            -33,
            128,
            65536,
            2**63,
            1.5,
            Number.VALUE,
            date(2026, 1, 1),
            datetime(2026, 1, 1),
            b"abc",
            bytearray(b"def"),
        ],
        "tuple": ("value",),
    }
    assert codec.encoded_size(obj) == len(codec.encode(obj))


@pytest.mark.parametrize("extra", [-1, 0, 1])
def test_complete_frame_boundary(extra):
    codec = MsgPackCodec()
    envelope = Envelope(request_id=1, message_type=MessageType.REQUEST, payload={"text": "x" * 65536})
    overhead = len(codec.encode(envelope.model_dump())) - 65536
    envelope.payload["text"] = "x" * (MAX_FRAME_SIZE - overhead + extra)
    if extra > 0:
        with pytest.raises(FrameTooLargeError):
            codec.encode_envelope(envelope)
    else:
        assert len(codec.encode_envelope(envelope)) == MAX_FRAME_SIZE + extra


def hook_kwargs(image_size, text_size=0):
    return {
        "item_schema_version": 1,
        "items": [
            {
                "item_type": "UserMessageItem",
                "parts": [
                    {"type": "image", "image_base64": "A" * image_size, "image_format": "png"},
                ],
            }
        ],
        "tool_definitions": [{"description": "x" * text_size}],
    }


def test_full_hook_budget_accounts_for_tools_metadata_and_preserves_original():
    original = hook_kwargs(8 * 1024 * 1024, 9 * 1024 * 1024)
    targets = [("test-plugin", "test-handler", 60000)]
    result = HookRequestBudget("maisaka.planner.before_request", targets).fit(original)
    assert result["items"][0]["parts"][0]["type"] == "text"
    assert original["items"][0]["parts"][0]["type"] == "image"
    assert result["tool_definitions"] is original["tool_definitions"]
    envelope = Envelope(
        request_id=1,
        message_type=MessageType.REQUEST,
        method="plugin.invoke_hook",
        plugin_id="test-plugin",
        timeout_ms=60000,
        payload={"component_name": "test-handler", "args": {"hook_name": "maisaka.planner.before_request", **result}},
    )
    assert len(MsgPackCodec().encode_envelope(envelope)) <= MAX_FRAME_SIZE


def test_pure_text_hook_cannot_be_fixed_by_image_budget():
    with pytest.raises(FrameTooLargeError):
        HookRequestBudget("maisaka.replyer.before_model_request", [("test", "test", 60000)]).fit(
            hook_kwargs(4, MAX_FRAME_SIZE)
        )


@pytest.mark.asyncio
async def test_runner_replies_small_error_before_packing_oversize_result(monkeypatch):
    client = RPCClient("unused", "synthetic-token")
    connection = AsyncMock()
    connection.is_closed = False
    client._connection = connection
    envelope = Envelope(request_id=1, message_type=MessageType.REQUEST, method="plugin.invoke_hook")
    response = envelope.make_response({"modified_kwargs": hook_kwargs(18 * 1024 * 1024)})
    client.register_method("plugin.invoke_hook", AsyncMock(return_value=response))
    packed_sizes = []
    original_encode = client._codec.encode

    def encode(obj):
        packed_sizes.append(client._codec.encoded_size(obj))
        return original_encode(obj)

    monkeypatch.setattr(client._codec, "encode", encode)
    await client._handle_request(envelope)
    connection.send_frame.assert_awaited_once()
    error = client._codec.decode_envelope(connection.send_frame.call_args.args[0])
    assert error.error["code"] == "E_BAD_PAYLOAD"
    assert "帧" in error.error["message"]
    assert max(packed_sizes) < 1024


def test_size_preflight_does_not_retain_packer_buffers():
    codec = MsgPackCodec()
    tracemalloc.start()
    try:
        for _ in range(100):
            codec.encoded_size({"nested": [{"text": "合成图片"}]})
        current, _ = tracemalloc.get_traced_memory()
        assert current < 128 * 1024
    finally:
        tracemalloc.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["maisaka.planner.before_request", "maisaka.replyer.before_model_request"])
async def test_hook_chain_budget_through_host_rpc_and_runner_response(monkeypatch, hook_name):
    """真实分发、Host 编码和 Runner 响应；仅替换底层连接。"""

    server = RPCServer(AsyncMock(), host_version="synthetic")
    server._connection = SimpleNamespace(is_closed=False)
    codec = MsgPackCodec()
    seen = []

    async def send(_connection, data):
        assert len(data) <= MAX_FRAME_SIZE
        request = codec.decode_envelope(data)
        args = request.payload["args"]
        seen.append(args)
        if len(seen) == 1:
            args = {**args, **hook_kwargs(12 * 1024 * 1024)}
        response = request.make_response({"success": True, "modified_kwargs": args})
        # Runner 修改后的响应仍在 16 MiB 内，但需要在下一个 Hook 前应用 8 MiB 图片预算。
        server._handle_response(codec.decode_envelope(codec.encode_envelope(response)))

    monkeypatch.setattr(server, "_enqueue_send", send)
    supervisor = SimpleNamespace(_rpc_server=server, _ensure_accepting_runner_rpc=lambda: None)

    async def invoke_plugin(*args, **kwargs):
        return await PluginRunnerSupervisor.invoke_plugin(supervisor, *args, **kwargs)

    supervisor.invoke_plugin = invoke_plugin
    entries = [
        SimpleNamespace(
            plugin_id="synthetic",
            name=f"handler-{index}",
            full_name=f"synthetic.handler-{index}",
            timeout_ms=1000,
            is_observe=False,
            error_policy="skip",
        )
        for index in range(2)
    ]
    dispatcher = HookDispatcher()
    monkeypatch.setattr(
        dispatcher,
        "_collect_invocation_targets",
        lambda *args: [SimpleNamespace(supervisor=supervisor, entry=entry) for entry in entries],
    )
    result = await dispatcher.invoke_hook(hook_name, supervisors=[], **hook_kwargs(4))
    assert not result.errors
    assert seen[0]["items"][0]["parts"][0]["type"] == "image"
    assert seen[1]["items"][0]["parts"][0]["type"] == "text"
    assert result.kwargs["items"][0]["parts"][0]["type"] == "text"
    assert not server._pending_requests
    assert not server._pending_request_metadata


@pytest.mark.asyncio
async def test_host_oversize_error_releases_pending_without_sending(monkeypatch):
    server = RPCServer(AsyncMock(), host_version="synthetic")
    server._connection = SimpleNamespace(is_closed=False)
    send = AsyncMock()
    monkeypatch.setattr(server, "_enqueue_send", send)
    with pytest.raises(FrameTooLargeError):
        await server.send_request("plugin.invoke_hook", payload={"text": "x" * MAX_FRAME_SIZE})
    send.assert_not_awaited()
    assert not server._pending_requests
    assert not server._pending_request_metadata


@pytest.fixture
def dispatch_chain(monkeypatch):
    """保留真实分发和协议编解码，仅用合成响应代替外部插件。"""
    from src.plugin_runtime.host import circuit_breaker

    breaker = circuit_breaker.PluginCircuitBreaker(failure_threshold=1, base_cooldown_sec=1)
    monkeypatch.setattr(circuit_breaker, "_plugin_circuit_breaker", breaker)

    def build(payloads):
        server = RPCServer(AsyncMock(), host_version="synthetic")
        server._connection = SimpleNamespace(is_closed=False)
        seen = []

        async def send(_connection, data):
            request = server._codec.decode_envelope(data)
            payload = payloads[len(seen)]
            seen.append(request)
            response = request.make_response(payload)
            server._handle_response(server._codec.decode_envelope(server._codec.encode_envelope(response)))

        monkeypatch.setattr(server, "_enqueue_send", send)
        supervisor = SimpleNamespace(_rpc_server=server, _ensure_accepting_runner_rpc=lambda: None)

        async def invoke_plugin(*args, **kwargs):
            return await PluginRunnerSupervisor.invoke_plugin(supervisor, *args, **kwargs)

        supervisor.invoke_plugin = invoke_plugin
        targets = [
            SimpleNamespace(
                supervisor=supervisor,
                entry=SimpleNamespace(
                    plugin_id="synthetic",
                    name="handler-" + "x" * 256,
                    full_name=f"synthetic.handler-{i}",
                    timeout_ms=1000,
                    is_observe=False,
                    error_policy="skip",
                ),
            )
            for i in range(len(payloads))
        ]
        dispatcher = HookDispatcher()
        monkeypatch.setattr(dispatcher, "_collect_invocation_targets", lambda *args: targets)
        return dispatcher, server, targets, seen, breaker

    return build


@pytest.mark.asyncio
async def test_unsent_oversize_probe_releases_half_open_without_recovery(dispatch_chain, monkeypatch):
    from src.plugin_runtime.host import circuit_breaker

    now = 0.0
    monkeypatch.setattr(circuit_breaker, "time", SimpleNamespace(monotonic=lambda: now))
    dispatcher, server, targets, seen, breaker = dispatch_chain([{"success": True}])
    breaker.record_failure(breaker.try_acquire("synthetic", "handler", "hook"), "synthetic timeout")
    now = 2.0
    hook_name = "maisaka.planner.after_response"
    with pytest.raises(FrameTooLargeError):
        await dispatcher._invoke_handler(
            hook_name=hook_name,
            hook_spec=dispatcher.get_hook_spec(hook_name),
            target=targets[0],
            kwargs={"text": "x" * MAX_FRAME_SIZE},
        )
    assert not seen
    assert not server._pending_requests
    assert not server._pending_request_metadata
    status = breaker.get_plugin_statuses()["synthetic"]
    assert status["state"] == "half_open"
    assert status["cooldown_level"] == 1
    assert not status["half_open_inflight"]
    assert breaker.try_acquire("synthetic", "handler", "hook").allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["maisaka.planner.before_request", "maisaka.replyer.before_model_request"])
@pytest.mark.parametrize("next_target", [False, True])
async def test_valid_final_response_is_not_rechecked_as_outbound_request(dispatch_chain, hook_name, next_target):
    kwargs = {"text": "x" * 65536}
    payload = {"success": True, "modified_kwargs": kwargs, "custom_result": "保留结果"}
    response = Envelope(
        request_id=1,
        message_type=MessageType.RESPONSE,
        method="plugin.invoke_hook",
        plugin_id="synthetic",
        payload=payload,
    )
    codec = MsgPackCodec()
    overhead = len(codec.encode_envelope(response)) - 65536
    kwargs["text"] = "x" * (MAX_FRAME_SIZE - overhead)
    assert len(codec.encode_envelope(response)) == MAX_FRAME_SIZE
    # 先聚合一条错误，再接收合法临界响应；下一次请求另有组件名等开销。
    payloads = [{"success": False, "error_message": "合成评审错误"}, payload]
    if next_target:
        payloads.append({"success": True})
    dispatcher, _, _, seen, _ = dispatch_chain(payloads)
    if next_target:
        with pytest.raises(FrameTooLargeError):
            await dispatcher.invoke_hook(hook_name, supervisors=[], text="small")
        assert len(seen) == 2
    else:
        result = await dispatcher.invoke_hook(hook_name, supervisors=[], text="small")
        assert result.kwargs == kwargs
        assert result.custom_results == ["保留结果"]
        assert result.errors == ["合成评审错误"]


@pytest.mark.asyncio
async def test_budget_payload_scans_are_linear_in_handler_count(dispatch_chain, monkeypatch):
    import src.plugin_runtime.host.hook_request_budget as budget_module

    count = 5
    dispatcher, _, _, _, _ = dispatch_chain([{"success": True}] * count)
    scans = 0
    original = MsgPackCodec.encoded_size

    class CountingCodec(MsgPackCodec):
        def encoded_size(self, obj, **kwargs):
            nonlocal scans
            args = obj.get("payload", {}).get("args", obj) if isinstance(obj, dict) else {}
            if "probe" in args:
                scans += 1
            return original(self, obj, **kwargs)

    monkeypatch.setattr(budget_module, "MsgPackCodec", CountingCodec)
    await dispatcher.invoke_hook("maisaka.planner.before_request", supervisors=[], probe="x" * 32768)
    assert scans <= count + 1


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_unexecuted_permit_release_is_idempotent_and_preserves_next_probe(monkeypatch, outcome):
    from src.plugin_runtime.host import circuit_breaker

    now = 0.0
    monkeypatch.setattr(circuit_breaker, "time", SimpleNamespace(monotonic=lambda: now))
    breaker = circuit_breaker.PluginCircuitBreaker(failure_threshold=1, base_cooldown_sec=1)
    breaker.record_failure(breaker.try_acquire("test", "handler", "hook"), "合成超时")
    now = 2.0
    first = breaker.try_acquire("test", "handler", "hook")
    breaker.release_unexecuted(first)
    breaker.release_unexecuted(first)
    second = breaker.try_acquire("test", "handler", "hook")
    assert second.allowed and second.half_open
    breaker.release_unexecuted(first)
    assert not breaker.try_acquire("test", "handler", "hook").allowed
    if outcome == "success":
        breaker.record_success(second)
        assert not breaker.get_plugin_statuses()
    else:
        breaker.record_failure(second, "再次超时")
        status = breaker.get_plugin_statuses()["test"]
        assert status["state"] == "open"
        assert status["cooldown_level"] == 2
    breaker.release_unexecuted(second)
    breaker._states.clear()
    breaker.release_unexecuted(first)
    assert not breaker._states


@pytest.mark.parametrize("field_count", [13, 14, 15])
@pytest.mark.parametrize("override_hook_name", [False, True])
@pytest.mark.parametrize("extra", [-1, 0, 1])
def test_cached_overhead_preserves_exact_map_and_full_frame_boundaries(field_count, override_hook_name, extra):
    hook_name = "maisaka.planner.before_request"
    kwargs = {f"字段{i}": "😀" for i in range(field_count)}
    if override_hook_name:
        kwargs["hook_name"] = "合成覆盖值"
    kwargs["text"] = "x" * 65536
    targets = [("short", "a", 1), ("合成插件", "图像处理器" * 10, 60000)]
    budget = HookRequestBudget(hook_name, targets)
    codec = MsgPackCodec()
    envelope = Envelope(
        request_id=2**64 - 1,
        timestamp_ms=2**64 - 1,
        message_type=MessageType.REQUEST,
        method="plugin.invoke_hook",
        plugin_id=targets[1][0],
        timeout_ms=targets[1][2],
        payload={"component_name": targets[1][1], "args": {"hook_name": hook_name, **kwargs}},
    )
    overhead = len(codec.encode_envelope(envelope)) - 65536
    kwargs["text"] = "x" * (MAX_FRAME_SIZE - overhead + extra)
    if extra > 0:
        with pytest.raises(FrameTooLargeError):
            budget.fit(kwargs)
    else:
        result = budget.fit(kwargs)
        assert result is kwargs
        envelope.payload["args"] = {"hook_name": hook_name, **result}
        assert len(codec.encode_envelope(envelope)) == MAX_FRAME_SIZE + extra


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["maisaka.planner.before_request", "maisaka.replyer.before_model_request"])
@pytest.mark.parametrize("abort", [False, True])
async def test_final_modified_images_still_fit_budget_and_preserve_aggregate(dispatch_chain, hook_name, abort):
    original = hook_kwargs(12 * 1024 * 1024)
    payloads = [
        {
            "success": True,
            "modified_kwargs": original,
            "custom_result": "保留结果",
            "action": "abort" if abort else "continue",
        }
    ]
    if abort:
        payloads.append({"success": True})
    dispatcher, _, _, seen, _ = dispatch_chain(payloads)
    result = await dispatcher.invoke_hook(hook_name, supervisors=[], **hook_kwargs(4))
    assert len(seen) == 1
    assert result.aborted is abort
    assert result.custom_results == ["保留结果"]
    assert not result.errors
    assert result.kwargs["items"][0]["parts"][0]["type"] == "text"
    assert original["items"][0]["parts"][0]["type"] == "image"
    assert result.kwargs["tool_definitions"] == original["tool_definitions"]


def test_invocation_budget_does_not_retain_fitted_payload():
    import weakref

    class Payload(dict):
        pass

    budget = HookRequestBudget("maisaka.planner.before_request", [("test", "handler", 60000)])
    payload = Payload(hook_kwargs(1024 * 1024))
    reference = weakref.ref(payload)
    result = budget.fit(payload)
    assert result is payload
    del result, payload
    assert reference() is None
