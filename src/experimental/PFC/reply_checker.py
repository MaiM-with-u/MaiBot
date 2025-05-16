from typing import Tuple, List, Dict, Any
from src.common.logger import get_module_logger
from src.config.config import global_config  # 为了获取 BOT_QQ
from .chat_observer import ChatObserver
import re

logger = get_module_logger("pfc_checker")


class ReplyChecker:
    """回复检查器 - 新版：仅检查机器人自身发言的精确重复"""

    def __init__(self, stream_id: str, private_name: str):
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.bot_qq_str = str(global_config.BOT_QQ)

    def _normalize_text(self, text: str) -> str:
        """
        规范化文本，去除首尾空格，移除末尾的特定标点符号。
        """
        if not text:
            return ""
        text = text.strip()  # 1. 去除首尾空格
        # 2. 移除末尾的一个或多个特定标点符号
        # 可以根据需要调整正则表达式以包含更多或更少的标点
        text = re.sub(r"[~\s,.!?；;，。]+$", "", text)
        # 如果需要忽略大小写，可以取消下面一行的注释
        # text = text.lower()
        return text

    async def check(
        self,
        reply: str,
        goal: str,  # 当前逻辑未使用
        chat_history: List[Dict[str, Any]],
        chat_history_text: str,  # 当前逻辑未使用
        current_time_str: str,  # 当前逻辑未使用
        retry_count: int = 0,  # 当前逻辑未使用
    ) -> Tuple[bool, str, bool]:
        """检查生成的回复是否与机器人之前的发言完全一致（长度大于4）

        Args:
            reply: 待检查的机器人回复内容
            chat_history: 对话历史记录 (包含用户和机器人的消息字典列表)
        Returns:
            Tuple[bool, str, bool]: (是否合适, 原因, 是否需要重新规划)
                                    对于重复消息: (False, "机器人尝试发送重复消息", False)
                                    对于非重复消息: (True, "消息内容未与机器人历史发言重复。", False)
        """
        if not self.bot_qq_str:
            logger.error(
                f"[私聊][{self.private_name}] ReplyChecker: BOT_QQ 未配置，无法检查{global_config.BOT_NICKNAME}自身消息。"
            )
            return True, "BOT_QQ未配置，跳过重复检查。", False  # 无法检查则默认通过

        # 对当前待发送的回复进行规范化
        normalized_reply = self._normalize_text(reply)

        if len(normalized_reply) <= 4:
            return True, "消息长度小于等于4字符，跳过重复检查。", False

        try:
            match_found = False  # <--- 用于调试
            for i, msg_dict in enumerate(reversed(chat_history)):
                if not isinstance(msg_dict, dict):
                    continue

                user_info_data = msg_dict.get("user_info")
                if not isinstance(user_info_data, dict):
                    continue

                sender_id = str(user_info_data.get("user_id"))

                if sender_id == self.bot_qq_str:
                    historical_message_text = msg_dict.get("processed_plain_text", "")
                    # 对历史消息也进行同样的规范化处理
                    normalized_historical_text = self._normalize_text(historical_message_text)

                    logger.debug(
                        f"[私聊][{self.private_name}] ReplyChecker: 历史记录 (反向索引 {i}) ({global_config.BOT_NICKNAME}): "
                        f"原始='{historical_message_text[:50]}...', 规范化后='{normalized_historical_text[:50]}...'"
                    )
                    if (
                        normalized_reply == normalized_historical_text and len(normalized_reply) > 0
                    ):  # 确保规范化后不为空串才比较
                        logger.warning(f"[私聊][{self.private_name}] ReplyChecker: !!! 成功拦截一次复读 !!!")
                        logger.warning(
                            f"[私聊][{self.private_name}] ReplyChecker 检测到{global_config.BOT_NICKNAME}自身重复消息 (规范化后内容相同): '{normalized_reply[:50]}...'"
                        )
                        match_found = True
                        # 返回: 不合适, 原因, 不需要重规划 (让上层逻辑决定是否重试生成)
                        return (False, "机器人尝试发送与历史发言相似的消息 (内容规范化后相同)", False)

            if not match_found:
                logger.debug(f"[私聊][{self.private_name}] ReplyChecker: 未找到重复内容 (规范化后比较)。")
            return (True, "消息内容未与机器人历史发言重复 (规范化后比较)。", False)

        except Exception as e:
            import traceback

            logger.error(f"[私聊][{self.private_name}] ReplyChecker 检查重复时出错: 类型={type(e)}, 值={e}")
            logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
            return (True, f"检查重复时发生内部错误 (规范化检查): {str(e)}", False)
