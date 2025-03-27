from typing import Callable
import time

# 示例函数
async def refuse_response(response: list[str]) -> list[str]:
    return []
async def ping_response(response: list[str]) -> list[str]:
    return [f"Pong! at {time.asctime()}."]

# 可用函数表 注意每个函数都应该接收一个list[str](输入的响应), 输出一个list[str](输出的响应)
# 显然 你可以在函数里做各种操作来修改响应，做出其他动作，etc
# 注意到MaiMBot基于Python 3.9, 所以这里的注册顺序实际上决定了tag的执行顺序，越上方的越靠前
usable_action: dict[str, Callable[[list[str]], list[str]]] = {
    "[ping]"    : ping_response,
    "[refuse]"  : refuse_response,
}

usable_action_description = [
    "[ping]: 此标签**仅**用于用户确认系统是否在线，输出此标签会**导致消息被替换为**`Pong! at %当前时间%`"
    "[refuse]: 此标签用于标识认为无需回复的情况，输出此标签会**使得消息不被发送**。",
]