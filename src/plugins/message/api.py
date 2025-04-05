from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from typing import Dict, Any, Callable, List, Set
from src.common.logger import get_module_logger
from src.plugins.message.message_base import MessageBase
import aiohttp
import asyncio
import uvicorn
import os
import traceback

logger = get_module_logger("api")


class BaseMessageHandler:
    """消息处理基类"""

    def __init__(self):
        self.message_handlers: List[Callable] = []
        self.background_tasks = set()

    def register_message_handler(self, handler: Callable):
        """注册消息处理函数"""
        self.message_handlers.append(handler)

    async def process_message(self, message: Dict[str, Any]):
        """处理单条消息"""
        tasks = []
        for handler in self.message_handlers:
            try:
                tasks.append(handler(message))
            except Exception as e:
                logger.error(f"消息处理出错: {str(e)}")
                logger.error(traceback.format_exc())
                # 不抛出异常，而是记录错误并继续处理其他消息
                continue
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, message: Dict[str, Any]):
        """后台处理单个消息"""
        try:
            await self.process_message(message)
        except Exception as e:
            raise RuntimeError(str(e)) from e


class MessageServer(BaseMessageHandler):
    """WebSocket服务端"""

    _class_handlers: List[Callable] = []  # 类级别的消息处理器

    def __init__(self, host: str = "0.0.0.0", port: int = 18000, enable_token=False):
        super().__init__()
        # 将类级别的处理器添加到实例处理器中
        self.message_handlers.extend(self._class_handlers)
        self.app = FastAPI()
        self.host = host
        self.port = port
        self.active_websockets: Set[WebSocket] = set()
        self.platform_websockets: Dict[str, WebSocket] = {}  # 平台到websocket的映射
        self.valid_tokens: Set[str] = set()
        self.enable_token = enable_token
        self._setup_routes()
        self._running = False

    @classmethod
    def register_class_handler(cls, handler: Callable):
        """注册类级别的消息处理器"""
        if handler not in cls._class_handlers:
            cls._class_handlers.append(handler)

    def register_message_handler(self, handler: Callable):
        """注册实例级别的消息处理器"""
        if handler not in self.message_handlers:
            self.message_handlers.append(handler)

    async def verify_token(self, token: str) -> bool:
        if not self.enable_token:
            return True
        return token in self.valid_tokens

    def add_valid_token(self, token: str):
        self.valid_tokens.add(token)

    def remove_valid_token(self, token: str):
        self.valid_tokens.discard(token)

    def _setup_routes(self):
        @self.app.post("/api/message")
        async def handle_message(message: Dict[str, Any]):
            try:
                # 创建后台任务处理消息
                asyncio.create_task(self._handle_message(message))
                return {"status": "success"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            headers = dict(websocket.headers)
            token = headers.get("authorization")
            platform = headers.get("platform", "default")  # 获取platform标识
            if self.enable_token:
                if not token or not await self.verify_token(token):
                    await websocket.close(code=1008, reason="Invalid or missing token")
                    return

            await websocket.accept()
            self.active_websockets.add(websocket)

            # 添加到platform映射
            if platform not in self.platform_websockets:
                self.platform_websockets[platform] = websocket

            try:
                while True:
                    message = await websocket.receive_json()
                    # print(f"Received message: {message}")
                    asyncio.create_task(self._handle_message(message))
            except WebSocketDisconnect:
                self._remove_websocket(websocket, platform)
            except Exception as e:
                self._remove_websocket(websocket, platform)
                raise RuntimeError(str(e)) from e
            finally:
                self._remove_websocket(websocket, platform)

    def _remove_websocket(self, websocket: WebSocket, platform: str):
        """从所有集合中移除websocket"""
        if websocket in self.active_websockets:
            self.active_websockets.remove(websocket)
        if platform in self.platform_websockets:
            if self.platform_websockets[platform] == websocket:
                del self.platform_websockets[platform]

    async def broadcast_message(self, message: Dict[str, Any]):
        disconnected = set()
        for websocket in self.active_websockets:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)
        for websocket in disconnected:
            self.active_websockets.remove(websocket)

    async def broadcast_to_platform(self, platform: str, message: Dict[str, Any]):
        """向指定平台的所有WebSocket客户端广播消息"""
        if platform not in self.platform_websockets:
            raise ValueError(f"平台：{platform} 未连接")

        disconnected = set()
        try:
            await self.platform_websockets[platform].send_json(message)
        except Exception:
            disconnected.add(self.platform_websockets[platform])

        # 清理断开的连接
        for websocket in disconnected:
            self._remove_websocket(websocket, platform)

    async def send_message(self, message: MessageBase):
        await self.broadcast_to_platform(message.message_info.platform, message.to_dict())

    def run_sync(self):
        """同步方式运行服务器"""
        uvicorn.run(self.app, host=self.host, port=self.port)

    async def run(self):
        """异步方式运行服务器"""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, loop="asyncio")
        self.server = uvicorn.Server(config)
        try:
            await self.server.serve()
        except KeyboardInterrupt as e:
            await self.stop()
            raise KeyboardInterrupt from e

    async def start_server(self):
        """启动服务器的异步方法"""
        if not self._running:
            self._running = True
            await self.run()

    async def stop(self):
        """停止服务器"""
        # 清理platform映射
        self.platform_websockets.clear()

        # 取消所有后台任务
        for task in self.background_tasks:
            task.cancel()
        # 等待所有任务完成
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()

        # 关闭所有WebSocket连接
        for websocket in self.active_websockets:
            await websocket.close()
        self.active_websockets.clear()

        if hasattr(self, "server"):
            self._running = False
            # 正确关闭 uvicorn 服务器
            self.server.should_exit = True
            await self.server.shutdown()
            # 等待服务器完全停止
            if hasattr(self.server, "started") and self.server.started:
                await self.server.main_loop()
            # 清理处理程序
            self.message_handlers.clear()

    async def send_message_REST(self, url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """发送消息到指定端点"""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=data, headers={"Content-Type": "application/json"}) as response:
                    return await response.json()
            except Exception as e:
                raise e


class BaseMessageAPI:
    def __init__(self, host: str = "0.0.0.0", port: int = 18000):
        self.app = FastAPI()
        self.host = host
        self.port = port
        self.message_handlers: List[Callable] = []
        self.cache = []
        self._setup_routes()
        self._running = False

    def _setup_routes(self):
        """设置基础路由"""

        @self.app.post("/api/message")
        async def handle_message(message: Dict[str, Any]):
            try:
                # 创建后台任务处理消息
                asyncio.create_task(self._background_message_handler(message))
                return {"status": "success"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def _background_message_handler(self, message: Dict[str, Any]):
        """后台处理单个消息"""
        try:
            await self.process_single_message(message)
        except Exception as e:
            logger.error(f"Background message processing failed: {str(e)}")
            logger.error(traceback.format_exc())

    def register_message_handler(self, handler: Callable):
        """注册消息处理函数"""
        self.message_handlers.append(handler)

    async def send_message(self, url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """发送消息到指定端点"""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=data, headers={"Content-Type": "application/json"}) as response:
                    return await response.json()
            except Exception:
                # logger.error(f"发送消息失败: {str(e)}")
                pass

    async def process_single_message(self, message: Dict[str, Any]):
        """处理单条消息"""
        tasks = []
        for handler in self.message_handlers:
            try:
                tasks.append(handler(message))
            except Exception as e:
                logger.error(str(e))
                logger.error(traceback.format_exc())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def run_sync(self):
        """同步方式运行服务器"""
        uvicorn.run(self.app, host=self.host, port=self.port)

    async def run(self):
        """异步方式运行服务器"""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, loop="asyncio")
        self.server = uvicorn.Server(config)
        try:
            await self.server.serve()
        except KeyboardInterrupt as e:
            await self.stop()
            raise KeyboardInterrupt from e

    async def start_server(self):
        """启动服务器的异步方法"""
        if not self._running:
            self._running = True
            await self.run()

    async def stop(self):
        """停止服务器"""
        if hasattr(self, "server"):
            self._running = False
            # 正确关闭 uvicorn 服务器
            self.server.should_exit = True
            await self.server.shutdown()
            # 等待服务器完全停止
            if hasattr(self.server, "started") and self.server.started:
                await self.server.main_loop()
            # 清理处理程序
            self.message_handlers.clear()

    def start(self):
        """启动服务器的便捷方法"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start_server())
        except KeyboardInterrupt:
            pass
        finally:
            loop.close()


global_api = MessageServer(host=os.environ["HOST"], port=int(os.environ["PORT"]))
