# TODO: 人格侧写（不要把人格侧写的功能实现写到这里！新建文件去）
import traceback
import re
from typing import Any
from datetime import datetime  # 确保导入 datetime
from maim_message import UserInfo  # UserInfo 来自 maim_message 包 # 从 maim_message 导入 MessageRecv
from src.config.config import global_config
from src.common.logger_manager import get_logger
from src.chat.utils.utils import get_embedding
from src.common.database import db
from .pfc_manager import PFCManager
from src.chat.message_receive.chat_stream import ChatStream, chat_manager
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.storage import MessageStorage


logger = get_logger("pfc_processor")


async def _handle_error(
    error: Exception, context: str, message: MessageRecv | None = None
) -> None:  # 明确 message 类型
    """统一的错误处理函数
    # ... (方法注释不变) ...
    """
    logger.error(f"{context}: {error}")
    logger.error(traceback.format_exc())
    # 检查 message 是否 None 以及是否有 raw_message 属性
    if (
        message and hasattr(message, "message_info") and hasattr(message.message_info, "raw_message")
    ):  # MessageRecv 结构可能没有直接的 raw_message
        raw_msg_content = getattr(message.message_info, "raw_message", None)  # 安全获取
        if raw_msg_content:
            logger.error(f"相关消息原始内容: {raw_msg_content}")
    elif message and hasattr(message, "raw_message"):  # 如果 MessageRecv 直接有 raw_message
        logger.error(f"相关消息原始内容: {message.raw_message}")


class PFCProcessor:
    def __init__(self):
        """初始化 PFC 处理器，创建消息存储实例"""
        # MessageStorage() 的实例化位置和具体类是什么？
        # 我们假设它来自 src.plugins.storage.storage
        # 但由于我们不能修改那个文件，所以这里的 self.storage 将按原样使用

        self.storage: MessageStorage = MessageStorage()
        self.pfc_manager = PFCManager.get_instance()

    async def process_message(self, message_data: dict[str, Any]) -> None:  # 使用 dict[str, Any] 替代 Dict
        """处理接收到的原始消息数据
        # ... (方法注释不变) ...
        """
        message_obj: MessageRecv | None = None  # 初始化为 None，并明确类型
        try:
            # 1. 消息解析与初始化
            message_obj = MessageRecv(message_data)  # 使用你提供的 message.py 中的 MessageRecv

            groupinfo = getattr(message_obj.message_info, "group_info", None)
            userinfo = getattr(message_obj.message_info, "user_info", None)

            logger.trace(f"准备为{userinfo.user_id}创建/获取聊天流")
            chat = await chat_manager.get_or_create_stream(
                platform=message_obj.message_info.platform,
                user_info=userinfo,
                group_info=groupinfo,
            )
            message_obj.update_chat_stream(chat)  # message.py 中 MessageRecv 有此方法

            # 2. 过滤检查
            await message_obj.process()  # 调用 MessageRecv 的异步 process 方法
            if self._check_ban_words(message_obj.processed_plain_text, userinfo) or self._check_ban_regex(
                message_obj.raw_message, userinfo
            ):  # MessageRecv 有 raw_message 属性
                return

            # 3. 消息存储 (保持原有调用)
            # 这里的 self.storage.store_message 来自 src/plugins/storage/storage.py
            # 它内部会将 message_obj 转换为字典并存储
            await self.storage.store_message(message_obj, chat)
            logger.trace(f"存储成功 (初步): {message_obj.processed_plain_text}")

            await self._update_embedding_vector(message_obj, chat)  # 明确传递 message_obj

            # 4. 创建 PFC 聊天流
            await self._create_pfc_chat(message_obj)

            # 5. 日志记录
            # 确保 message_obj.message_info.time 是 float 类型的时间戳
            current_time_display = datetime.fromtimestamp(float(message_obj.message_info.time)).strftime("%H:%M:%S")

            # 确保 userinfo.user_nickname 存在
            user_nickname_display = getattr(userinfo, "user_nickname", "未知用户")

            logger.info(f"[{current_time_display}][私聊]{user_nickname_display}: {message_obj.processed_plain_text}")

        except Exception as e:
            await _handle_error(e, "消息处理失败", message_obj)  # 传递 message_obj

    async def _create_pfc_chat(self, message: MessageRecv):  # 明确 message 类型
        try:
            chat_id = str(message.chat_stream.stream_id)
            private_name = str(message.message_info.user_info.user_nickname)  # 假设 UserInfo 有 user_nickname

            if global_config.enable_pfc_chatting:
                await self.pfc_manager.get_or_create_conversation(chat_id, private_name)

        except Exception as e:
            logger.error(f"创建PFC聊天失败: {e}", exc_info=True)  # 添加 exc_info=True

    @staticmethod
    def _check_ban_words(text: str, userinfo: UserInfo) -> bool:  # 明确 userinfo 类型
        """检查消息中是否包含过滤词"""
        for word in global_config.ban_words:
            if word in text:
                logger.info(f"[私聊]{userinfo.user_nickname}:{text}")  # 假设 UserInfo 有 user_nickname
                logger.info(f"[过滤词识别]消息中含有{word}，filtered")
                return True
        return False

    @staticmethod
    def _check_ban_regex(text: str, userinfo: UserInfo) -> bool:  # 明确 userinfo 类型
        """检查消息是否匹配过滤正则表达式"""
        for pattern in global_config.ban_msgs_regex:
            if pattern.search(text):  # 假设 ban_msgs_regex 中的元素是已编译的正则对象
                logger.info(f"[私聊]{userinfo.user_nickname}:{text}")  # _nickname
                logger.info(f"[正则表达式过滤]消息匹配到{pattern.pattern}，filtered")  # .pattern 获取原始表达式字符串
                return True
        return False

    async def _update_embedding_vector(self, message_obj: MessageRecv, chat: ChatStream) -> None:
        """更新消息的嵌入向量"""
        # === 新增：为已存储的消息生成嵌入并更新数据库文档 ===
        embedding_vector = None
        text_for_embedding = message_obj.processed_plain_text  # 使用处理后的纯文本

        # 在 storage.py 中，会对 processed_plain_text 进行一次过滤
        # 为了保持一致，我们也在这里应用相同的过滤逻辑
        # 当然，更优的做法是 store_message 返回过滤后的文本，或在 message_obj 中增加一个 filtered_processed_plain_text 属性
        # 这里为了简单，我们先重复一次过滤逻辑
        pattern = r"<MainRule>.*?</MainRule>|<schedule>.*?</schedule>|<UserMessage>.*?</UserMessage>"
        if text_for_embedding:
            filtered_text_for_embedding = re.sub(pattern, "", text_for_embedding, flags=re.DOTALL)
        else:
            filtered_text_for_embedding = ""

        if filtered_text_for_embedding and filtered_text_for_embedding.strip():
            try:
                # request_type 参数根据你的 get_embedding 函数实际需求来定
                embedding_vector = await get_embedding(filtered_text_for_embedding, request_type="pfc_private_memory")
                if embedding_vector:
                    logger.debug(f"成功为消息 ID '{message_obj.message_info.message_id}' 生成嵌入向量。")

                    # 更新数据库中的对应文档
                    # 确保你有权限访问和操作 db 对象
                    update_result = db.messages.update_one(
                        {"message_id": message_obj.message_info.message_id, "chat_id": chat.stream_id},
                        {"$set": {"embedding_vector": embedding_vector}},
                    )
                    if update_result.modified_count > 0:
                        logger.info(f"成功为消息 ID '{message_obj.message_info.message_id}' 更新嵌入向量到数据库。")
                    elif update_result.matched_count > 0:
                        logger.warning(f"消息 ID '{message_obj.message_info.message_id}' 已存在嵌入向量或未作修改。")
                    else:
                        logger.error(
                            f"未能找到消息 ID '{message_obj.message_info.message_id}' (chat_id: {chat.stream_id}) 来更新嵌入向量。可能是存储和更新之间存在延迟或问题。"
                        )
                else:
                    logger.warning(
                        f"未能为消息 ID '{message_obj.message_info.message_id}' 的文本 '{filtered_text_for_embedding[:30]}...' 生成嵌入向量。"
                    )
            except Exception as e_embed_update:
                logger.error(
                    f"为消息 ID '{message_obj.message_info.message_id}' 生成嵌入或更新数据库时发生异常: {e_embed_update}",
                    exc_info=True,
                )
        else:
            logger.debug(f"消息 ID '{message_obj.message_info.message_id}' 的过滤后纯文本为空，不生成或更新嵌入。")
        # === 新增结束 ===
