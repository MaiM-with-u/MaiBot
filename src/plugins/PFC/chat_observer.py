import time
import asyncio
from typing import Optional, Dict, Any, List
from src.common.logger import get_module_logger
from src.common.database import db
from ..message.message_base import UserInfo
from ..config.config import global_config

logger = get_module_logger("chat_observer")

class ChatObserver:
    """聊天状态观察器"""
    
    # 类级别的实例管理
    _instances: Dict[str, 'ChatObserver'] = {}
    
    @classmethod
    def get_instance(cls, stream_id: str) -> 'ChatObserver':
        """获取或创建观察器实例
        
        Args:
            stream_id: 聊天流ID
            
        Returns:
            ChatObserver: 观察器实例
        """
        if stream_id not in cls._instances:
            cls._instances[stream_id] = cls(stream_id)
        return cls._instances[stream_id]
    
    def __init__(self, stream_id: str):
        """初始化观察器
        
        Args:
            stream_id: 聊天流ID
        """
        if stream_id in self._instances:
            raise RuntimeError(f"ChatObserver for {stream_id} already exists. Use get_instance() instead.")
            
        self.stream_id = stream_id
        self.last_user_speak_time: Optional[float] = None  # 对方上次发言时间
        self.last_bot_speak_time: Optional[float] = None   # 机器人上次发言时间
        self.last_check_time: float = time.time()         # 上次查看聊天记录时间
        self.last_message_read: Optional[str] = None      # 最后读取的消息ID
        self.last_message_time: Optional[float] = None    # 最后一条消息的时间戳
        
        self.waiting_start_time: Optional[float] = None    # 等待开始时间
        
        # 消息历史记录
        self.message_history: List[Dict[str, Any]] = []  # 所有消息历史
        self.last_message_id: Optional[str] = None       # 最后一条消息的ID
        self.message_count: int = 0                      # 消息计数
        
        # 运行状态
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._update_event = asyncio.Event()  # 触发更新的事件
        self._update_complete = asyncio.Event()  # 更新完成的事件
    
    def new_message_after(self, time_point: float) -> bool:
        """判断是否在指定时间点后有新消息
        
        Args:
            time_point: 时间戳
            
        Returns:
            bool: 是否有新消息
        """
        return self.last_message_time is None or self.last_message_time > time_point
     
    def _add_message_to_history(self, message: Dict[str, Any]):
        """添加消息到历史记录
        
        Args:
            message: 消息数据
        """
        self.message_history.append(message)
        self.last_message_id = message["message_id"]
        self.last_message_time = message["time"]  # 更新最后消息时间
        self.message_count += 1
        
        # 更新说话时间
        user_info = UserInfo.from_dict(message.get("user_info", {}))
        if user_info.user_id == global_config.BOT_QQ:
            self.last_bot_speak_time = message["time"]
        else:
            self.last_user_speak_time = message["time"]
            
    def get_message_history(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: Optional[int] = None,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """获取消息历史
        
        Args:
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 限制返回消息数量
            user_id: 指定用户ID
            
        Returns:
            List[Dict[str, Any]]: 消息列表
        """
        filtered_messages = self.message_history
        
        if start_time is not None:
            filtered_messages = [m for m in filtered_messages if m["time"] >= start_time]
            
        if end_time is not None:
            filtered_messages = [m for m in filtered_messages if m["time"] <= end_time]
            
        if user_id is not None:
            filtered_messages = [
                m for m in filtered_messages 
                if UserInfo.from_dict(m.get("user_info", {})).user_id == user_id
            ]
            
        if limit is not None:
            filtered_messages = filtered_messages[-limit:]
            
        return filtered_messages
        
    async def _fetch_new_messages(self) -> List[Dict[str, Any]]:
        """获取新消息
        
        Returns:
            List[Dict[str, Any]]: 新消息列表
        """
        query = {"chat_id": self.stream_id}
        if self.last_message_read:
            # 获取ID大于last_message_read的消息
            last_message = db.messages.find_one({"message_id": self.last_message_read})
            if last_message:
                query["time"] = {"$gt": last_message["time"]}
                
        new_messages = list(
            db.messages.find(query).sort("time", 1)
        )
        
        if new_messages:
            self.last_message_read = new_messages[-1]["message_id"]
            
        return new_messages
    
    async def _fetch_new_messages_before(self, time_point: float) -> List[Dict[str, Any]]:
        """获取指定时间点之前的消息
        
        Args:
            time_point: 时间戳
            
        Returns:
            List[Dict[str, Any]]: 最多5条消息
        """
        query = {
            "chat_id": self.stream_id,
            "time": {"$lt": time_point}
        }
                
        new_messages = list(
            db.messages.find(query).sort("time", -1).limit(5)  # 倒序获取5条
        )
        
        # 将消息按时间正序排列
        new_messages.reverse()
        
        if new_messages:
            self.last_message_read = new_messages[-1]["message_id"]
            
        return new_messages
        
    async def _update_loop(self):
        """更新循环"""
        try:
            start_time = time.time()
            messages = await self._fetch_new_messages_before(start_time)
            for message in messages:
                self._add_message_to_history(message)
        except Exception as e:
            logger.error(f"缓冲消息出错: {e}")
        
        while self._running:
            try:
                # 等待事件或超时（1秒）
                try:
                    await asyncio.wait_for(self._update_event.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass  # 超时后也执行一次检查
                
                self._update_event.clear()  # 重置触发事件
                self._update_complete.clear()  # 重置完成事件
                
                # 获取新消息
                new_messages = await self._fetch_new_messages()
                
                if new_messages:
                    # 处理新消息
                    for message in new_messages:
                        self._add_message_to_history(message)
                
                # 设置完成事件
                self._update_complete.set()
                    
            except Exception as e:
                logger.error(f"更新循环出错: {e}")
                self._update_complete.set()  # 即使出错也要设置完成事件
                
    def trigger_update(self):
        """触发一次立即更新"""
        self._update_event.set()
        
    async def wait_for_update(self, timeout: float = 5.0) -> bool:
        """等待更新完成
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            bool: 是否成功完成更新（False表示超时）
        """
        try:
            await asyncio.wait_for(self._update_complete.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"等待更新完成超时（{timeout}秒）")
            return False
        
    def start(self):
        """启动观察器"""
        if self._running:
            return
            
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info(f"ChatObserver for {self.stream_id} started")
        
    def stop(self):
        """停止观察器"""
        self._running = False
        self._update_event.set()  # 设置事件以解除等待
        self._update_complete.set()  # 设置完成事件以解除等待
        if self._task:
            self._task.cancel()
        logger.info(f"ChatObserver for {self.stream_id} stopped")
        
    async def process_chat_history(self, messages: list):
        """处理聊天历史
        
        Args:
            messages: 消息列表
        """
        self.update_check_time()
        
        for msg in messages:
            try:
                user_info = UserInfo.from_dict(msg.get("user_info", {}))
                if user_info.user_id == global_config.BOT_QQ:
                    self.update_bot_speak_time(msg["time"])
                else:
                    self.update_user_speak_time(msg["time"])
            except Exception as e:
                logger.warning(f"处理消息时间时出错: {e}")
                continue 
        
    def update_check_time(self):
        """更新查看时间"""
        self.last_check_time = time.time()
        
    def update_bot_speak_time(self, speak_time: Optional[float] = None):
        """更新机器人说话时间"""
        self.last_bot_speak_time = speak_time or time.time()
        
    def update_user_speak_time(self, speak_time: Optional[float] = None):
        """更新用户说话时间"""
        self.last_user_speak_time = speak_time or time.time()
        
    def get_time_info(self) -> str:
        """获取时间信息文本"""
        current_time = time.time()
        time_info = ""
        
        if self.last_bot_speak_time:
            bot_speak_ago = current_time - self.last_bot_speak_time
            time_info += f"\n距离你上次发言已经过去了{int(bot_speak_ago)}秒"
            
        if self.last_user_speak_time:
            user_speak_ago = current_time - self.last_user_speak_time
            time_info += f"\n距离对方上次发言已经过去了{int(user_speak_ago)}秒"
            
        return time_info
