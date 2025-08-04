import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from src.common.logger import get_logger
from src.chat.message_receive.message import MessageRecvS4U
# 全局SuperChat管理器实例
from src.mais4u.constant_s4u import ENABLE_S4U

logger = get_logger("super_chat_manager")


@dataclass
class SuperChatRecord:
    """SuperChat记录数据类"""
    
    user_id: str
    user_nickname: str
    platform: str
    chat_id: str
    price: float
    message_text: str
    timestamp: float
    expire_time: float
    group_name: Optional[str] = None
    
    def is_expired(self) -> bool:
        """检查SuperChat是否已过期"""
        return time.time() > self.expire_time
    
    def remaining_time(self) -> float:
        """获取剩余时间（秒）"""
        return max(0, self.expire_time - time.time())
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "user_id": self.user_id,
            "user_nickname": self.user_nickname,
            "platform": self.platform,
            "chat_id": self.chat_id,
            "price": self.price,
            "message_text": self.message_text,
            "timestamp": self.timestamp,
            "expire_time": self.expire_time,
            "group_name": self.group_name,
            "remaining_time": self.remaining_time()
        }


class SuperChatManager:
    """SuperChat管理器，负责管理和跟踪SuperChat消息"""
    
    def __init__(self):
        self.super_chats: Dict[str, List[SuperChatRecord]] = {}  # chat_id -> SuperChat列表
        self._cleanup_task: Optional[asyncio.Task] = None
        self._is_initialized = False
        logger.info("SuperChat管理器已初始化")
    
    def _ensure_cleanup_task_started(self):
        """确保清理任务已启动（延迟启动）"""
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._cleanup_expired_superchats())
                self._is_initialized = True
                logger.info("SuperChat清理任务已启动")
            except RuntimeError:
                # 没有运行的事件循环，稍后再启动
                logger.debug("当前没有运行的事件循环，将在需要时启动清理任务")
    
    def _start_cleanup_task(self):
        """启动清理任务（已弃用，保留向后兼容）"""
        self._ensure_cleanup_task_started()

    async def _cleanup_expired_superchats(self):
        """定期清理过期的SuperChat"""
        while True:
            try:
                total_removed = 0
                
                for chat_id in list(self.super_chats.keys()):
                    original_count = len(self.super_chats[chat_id])
                    # 移除过期的SuperChat
                    self.super_chats[chat_id] = [
                        sc for sc in self.super_chats[chat_id] 
                        if not sc.is_expired()
                    ]
                    
                    removed_count = original_count - len(self.super_chats[chat_id])
                    total_removed += removed_count
                    
                    if removed_count > 0:
                        logger.info(f"从聊天 {chat_id} 中清理了 {removed_count} 个过期的SuperChat")
                    
                    # 如果列表为空，删除该聊天的记录
                    if not self.super_chats[chat_id]:
                        del self.super_chats[chat_id]
                
                if total_removed > 0:
                    logger.info(f"总共清理了 {total_removed} 个过期的SuperChat")
                
                # 每30秒检查一次
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"清理过期SuperChat时出错: {e}", exc_info=True)
                await asyncio.sleep(60)  # 出错时等待更长时间
    
    def _calculate_expire_time(self, price: float) -> float:
        """根据SuperChat金额计算过期时间"""
        current_time = time.time()
        
        # 根据金额阶梯设置不同的存活时间
        if price >= 500:
            # 500元以上：保持4小时
            duration = 4 * 3600
        elif price >= 200:
            # 200-499元：保持2小时
            duration = 2 * 3600
        elif price >= 100:
            # 100-199元：保持1小时
            duration = 1 * 3600
        elif price >= 50:
            # 50-99元：保持30分钟
            duration = 30 * 60
        elif price >= 20:
            # 20-49元：保持15分钟
            duration = 15 * 60
        elif price >= 10:
            # 10-19元：保持10分钟
            duration = 10 * 60
        else:
            # 10元以下：保持5分钟
            duration = 5 * 60
        
        return current_time + duration
    
    async def add_superchat(self, message: MessageRecvS4U) -> None:
        """添加新的SuperChat记录"""
        # 确保清理任务已启动
        self._ensure_cleanup_task_started()
        
        if not message.is_superchat or not message.superchat_price:
            logger.warning("尝试添加非SuperChat消息到SuperChat管理器")
            return
        
        try:
            price = float(message.superchat_price)
        except (ValueError, TypeError):
            logger.error(f"无效的SuperChat价格: {message.superchat_price}")
            return
        
        user_info = message.message_info.user_info
        group_info = message.message_info.group_info
        chat_id = getattr(message, 'chat_stream', None)
        if chat_id:
            chat_id = chat_id.stream_id
        else:
            # 生成chat_id的备用方法
            chat_id = f"{message.message_info.platform}_{user_info.user_id}"
            if group_info:
                chat_id = f"{message.message_info.platform}_{group_info.group_id}"
        
        expire_time = self._calculate_expire_time(price)
        
        record = SuperChatRecord(
            user_id=user_info.user_id,
            user_nickname=user_info.user_nickname,
            platform=message.message_info.platform,
            chat_id=chat_id,
            price=price,
            message_text=message.superchat_message_text or "",
            timestamp=message.message_info.time,
            expire_time=expire_time,
            group_name=group_info.group_name if group_info else None
        )
        
        # 添加到对应聊天的SuperChat列表
        if chat_id not in self.super_chats:
            self.super_chats[chat_id] = []
        
        self.super_chats[chat_id].append(record)
        
        # 按价格降序排序（价格高的在前）
        self.super_chats[chat_id].sort(key=lambda x: x.price, reverse=True)
        
        logger.info(f"添加SuperChat记录: {user_info.user_nickname} - {price}元 - {message.superchat_message_text}")
    
    def get_superchats_by_chat(self, chat_id: str) -> List[SuperChatRecord]:
        """获取指定聊天的所有有效SuperChat"""
        # 确保清理任务已启动
        self._ensure_cleanup_task_started()
        
        if chat_id not in self.super_chats:
            return []
        
        # 过滤掉过期的SuperChat
        valid_superchats = [sc for sc in self.super_chats[chat_id] if not sc.is_expired()]
        return valid_superchats
    
    def get_all_valid_superchats(self) -> Dict[str, List[SuperChatRecord]]:
        """获取所有有效的SuperChat"""
        # 确保清理任务已启动
        self._ensure_cleanup_task_started()
        
        result = {}
        for chat_id, superchats in self.super_chats.items():
            valid_superchats = [sc for sc in superchats if not sc.is_expired()]
            if valid_superchats:
                result[chat_id] = valid_superchats
        return result
    
    def build_superchat_display_string(self, chat_id: str, max_count: int = 10) -> str:
        """构建SuperChat显示字符串"""
        superchats = self.get_superchats_by_chat(chat_id)

        if not superchats:
            return ""

        # 限制显示数量
        display_superchats = superchats[:max_count]

        lines = ["📢 当前有效超级弹幕："]
        for i, sc in enumerate(display_superchats, 1):
            remaining_minutes = int(sc.remaining_time() / 60)
            remaining_seconds = int(sc.remaining_time() % 60)

            time_display = f"{remaining_minutes}分{remaining_seconds}秒" if remaining_minutes > 0 else f"{remaining_seconds}秒"

            line = f"{i}. 【{sc.price}元】{sc.user_nickname}: {sc.message_text}"
            if len(line) > 100:  # 限制单行长度
                line = f"{line[:97]}..."
            line += f" (剩余{time_display})"
            lines.append(line)

        if len(superchats) > max_count:
            lines.append(f"... 还有{len(superchats) - max_count}条SuperChat")

        return "\n".join(lines)
    
    def build_superchat_summary_string(self, chat_id: str) -> str:
        """构建SuperChat摘要字符串"""
        superchats = self.get_superchats_by_chat(chat_id)

        if not superchats:
            return "当前没有有效的超级弹幕"
        lines = []
        for sc in superchats:
            single_sc_str = f"{sc.user_nickname} - {sc.price}元 - {sc.message_text}"
            if len(single_sc_str) > 100:
                single_sc_str = f"{single_sc_str[:97]}..."
            single_sc_str += f" (剩余{int(sc.remaining_time())}秒)"
            lines.append(single_sc_str)

        total_amount = sum(sc.price for sc in superchats)
        count = len(superchats)
        highest_amount = max(sc.price for sc in superchats)

        final_str = f"当前有{count}条超级弹幕，总金额{total_amount}元，最高单笔{highest_amount}元"
        if lines:
            final_str += "\n" + "\n".join(lines)
        return final_str
    
    def get_superchat_statistics(self, chat_id: str) -> dict:
        """获取SuperChat统计信息"""
        superchats = self.get_superchats_by_chat(chat_id)
        
        if not superchats:
            return {
                "count": 0,
                "total_amount": 0,
                "average_amount": 0,
                "highest_amount": 0,
                "lowest_amount": 0
            }
        
        amounts = [sc.price for sc in superchats]
        
        return {
            "count": len(superchats),
            "total_amount": sum(amounts),
            "average_amount": sum(amounts) / len(amounts),
            "highest_amount": max(amounts),
            "lowest_amount": min(amounts)
        }
    
    async def shutdown(self):  # sourcery skip: use-contextlib-suppress
        """关闭管理器，清理资源"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("SuperChat管理器已关闭")




# sourcery skip: assign-if-exp
if ENABLE_S4U:
    super_chat_manager = SuperChatManager()
else:
    super_chat_manager = None

def get_super_chat_manager() -> SuperChatManager:
    """获取全局SuperChat管理器实例"""

    return super_chat_manager