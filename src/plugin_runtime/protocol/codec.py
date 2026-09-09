"""MsgPack 编解码器"""

from abc import ABC, abstractmethod
from datetime import date, datetime
from enum import Enum
from itertools import chain
from typing import Any, Dict

import msgpack

from src.plugin_runtime.transport.base import MAX_FRAME_SIZE

from .envelope import Envelope
from .errors import ErrorCode, RPCError


class FrameTooLargeError(RPCError):
    """在分配完整编码载荷前拒绝超大 RPC 帧。"""

    def __init__(self, size: int) -> None:
        super().__init__(
            ErrorCode.E_BAD_PAYLOAD,
            f"RPC 完整帧至少 {size} 字节，超过上限 {MAX_FRAME_SIZE}；请减少请求文字、工具或媒体载荷",
            {"size_at_least": size, "max_frame_size": MAX_FRAME_SIZE},
        )


DATETIME_EXT_CODE = 42
DATE_EXT_CODE = 43


class Codec(ABC):
    """消息编解码器基类"""

    @abstractmethod
    def encode_envelope(self, envelope: Envelope) -> bytes: ...

    @abstractmethod
    def decode_envelope(self, data: bytes) -> Envelope: ...

    @abstractmethod
    def encode(self, obj: Dict[str, Any]) -> bytes: ...

    @abstractmethod
    def decode(self, data: bytes) -> Dict[str, Any]: ...


class MsgPackCodec(Codec):
    """MsgPack 编解码器"""

    @staticmethod
    def _encode_ext_type(obj: Any) -> Any:
        """编码 MsgPack 原生不支持的 Python 类型。"""
        if isinstance(obj, datetime):
            return msgpack.ExtType(DATETIME_EXT_CODE, obj.isoformat().encode("utf-8"))
        if isinstance(obj, date):
            return msgpack.ExtType(DATE_EXT_CODE, obj.isoformat().encode("utf-8"))
        if isinstance(obj, Enum):
            return obj.value
        raise TypeError(f"can not serialize {type(obj).__name__!r} object")

    @staticmethod
    def _decode_ext_type(code: int, data: bytes) -> Any:
        if code == DATETIME_EXT_CODE:
            return datetime.fromisoformat(data.decode("utf-8"))
        if code == DATE_EXT_CODE:
            return date.fromisoformat(data.decode("utf-8"))
        return msgpack.ExtType(code, data)

    def encode(self, obj: Dict[str, Any]) -> bytes:
        result = msgpack.packb(obj, default=self._encode_ext_type, use_bin_type=True)
        if result is None:
            raise ValueError("msgpack.packb returned None, expected bytes")
        return result

    def decode(self, data: bytes) -> Dict[str, Any]:
        result = msgpack.unpackb(data, ext_hook=self._decode_ext_type, raw=False)
        if not isinstance(result, dict):
            raise ValueError(f"期望解码为 dict，实际为 {type(result)}")
        return result

    def encoded_size(self, obj: Any, *, limit: int = MAX_FRAME_SIZE) -> int:
        """计算与 packb 相同的字节数；大字符串分块计量，不分配其编码副本。

        容器头和普通标量使用 MsgPack 自己的编码器。str/bin 长度头遵循
        MsgPack 格式；累计超过 limit 即停止，返回值此时是实际大小的下界。
        """
        packer = msgpack.Packer(default=self._encode_ext_type, use_bin_type=True)
        total = 0

        # 显式迭代栈避免递归闭包形成引用环，及时释放 Packer 的内部缓冲。
        stack = [iter((obj,))]
        while stack and total <= limit:
            try:
                value = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if isinstance(value, dict):
                total += len(packer.pack_map_header(len(value)))
                stack.append(iter(chain.from_iterable(value.items())))
            elif isinstance(value, msgpack.ExtType):
                size = len(value.data)
                total += size + (2 if size in {1, 2, 4, 8, 16} else 3 if size <= 255 else 4 if size <= 65535 else 6)
            elif isinstance(value, (list, tuple)):
                total += len(packer.pack_array_header(len(value)))
                stack.append(iter(value))
            elif isinstance(value, str):
                if value.isascii():
                    size = len(value)
                else:
                    size = 0
                    for offset in range(0, len(value), 4096):
                        size += len(value[offset : offset + 4096].encode("utf-8"))
                        if total + size > limit:
                            break
                total += size + (1 if size <= 31 else 2 if size <= 255 else 3 if size <= 65535 else 5)
            elif isinstance(value, (bytes, bytearray, memoryview)):
                size = value.nbytes if isinstance(value, memoryview) else len(value)
                total += size + (2 if size <= 255 else 3 if size <= 65535 else 5)
            elif isinstance(value, Enum):
                stack.append(iter((value.value,)))
            else:
                total += len(packer.pack(value))
            if len(stack) > 512:
                raise ValueError("RPC 载荷嵌套超过 MsgPack 支持的深度")

        return total

    def encode_envelope(self, envelope: Envelope) -> bytes:
        payload = envelope.model_dump()
        size = self.encoded_size(payload)
        if size > MAX_FRAME_SIZE:
            raise FrameTooLargeError(size)
        return self.encode(payload)

    def decode_envelope(self, data: bytes) -> Envelope:
        raw = self.decode(data)
        return Envelope.model_validate(raw)
