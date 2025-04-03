import json
import datetime
from typing import Tuple
from src.common.logger import get_module_logger
from ..models.utils_model import LLM_request
from ..config.config import global_config
from .chat_observer import ChatObserver
from ..message.message_base import UserInfo

logger = get_module_logger("reply_checker")

class ReplyChecker:
    """回复检查器"""
    
    def __init__(self, stream_id: str):
        self.llm = LLM_request(
            model=global_config.llm_normal,
            temperature=0.7,
            max_tokens=1000,
            request_type="reply_check"
        )
        self.name = global_config.BOT_NICKNAME
        self.chat_observer = ChatObserver.get_instance(stream_id)
        self.max_retries = 2  # 最大重试次数
        
    async def check(
        self, 
        reply: str, 
        goal: str, 
        retry_count: int = 0
    ) -> Tuple[bool, str, bool]:
        """检查生成的回复是否合适
        
        Args:
            reply: 生成的回复
            goal: 对话目标
            retry_count: 当前重试次数
            
        Returns:
            Tuple[bool, str, bool]: (是否合适, 原因, 是否需要重新规划)
        """
        # 获取最新的消息记录
        messages = self.chat_observer.get_message_history(limit=5)
        chat_history_text = ""
        for msg in messages:
            time_str = datetime.datetime.fromtimestamp(msg["time"]).strftime("%H:%M:%S")
            user_info = UserInfo.from_dict(msg.get("user_info", {}))
            sender = user_info.user_nickname or f"用户{user_info.user_id}"
            if sender == self.name:
                sender = "你说"
            chat_history_text += f"{time_str},{sender}:{msg.get('processed_plain_text', '')}\n"
            
        prompt = f"""请检查以下回复是否合适：

当前对话目标：{goal}
最新的对话记录：
{chat_history_text}

待检查的回复：
{reply}

请检查以下几点：
1. 回复是否依然符合当前对话目标和实现方式
2. 回复是否与最新的对话记录保持一致性
3. 回复是否重复发言，重复表达
4. 回复是否包含违法违规内容（政治敏感、暴力等）
5. 回复是否以你的角度发言,不要把"你"说的话当做对方说的话，这是你自己说的话

请以JSON格式输出，包含以下字段：
1. suitable: 是否合适 (true/false)
2. reason: 原因说明
3. need_replan: 是否需要重新规划对话目标 (true/false)，当发现当前对话目标不再适合时设为true

输出格式示例：
{{
    "suitable": true,
    "reason": "回复符合要求，内容得体",
    "need_replan": false
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。"""

        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"检查回复的原始返回: {content}")
            
            # 清理内容，尝试提取JSON部分
            content = content.strip()
            try:
                # 尝试直接解析
                result = json.loads(content)
            except json.JSONDecodeError:
                # 如果直接解析失败，尝试查找和提取JSON部分
                import re
                json_pattern = r'\{[^{}]*\}'
                json_match = re.search(json_pattern, content)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        # 如果JSON解析失败，尝试从文本中提取结果
                        is_suitable = "不合适" not in content.lower() and "违规" not in content.lower()
                        reason = content[:100] if content else "无法解析响应"
                        need_replan = "重新规划" in content.lower() or "目标不适合" in content.lower()
                        return is_suitable, reason, need_replan
                else:
                    # 如果找不到JSON，从文本中判断
                    is_suitable = "不合适" not in content.lower() and "违规" not in content.lower()
                    reason = content[:100] if content else "无法解析响应"
                    need_replan = "重新规划" in content.lower() or "目标不适合" in content.lower()
                    return is_suitable, reason, need_replan
            
            # 验证JSON字段
            suitable = result.get("suitable", None)
            reason = result.get("reason", "未提供原因")
            need_replan = result.get("need_replan", False)
            
            # 如果suitable字段是字符串，转换为布尔值
            if isinstance(suitable, str):
                suitable = suitable.lower() == "true"
            
            # 如果suitable字段不存在或不是布尔值，从reason中判断
            if suitable is None:
                suitable = "不合适" not in reason.lower() and "违规" not in reason.lower()
            
            # 如果不合适且未达到最大重试次数，返回需要重试
            if not suitable and retry_count < self.max_retries:
                return False, reason, False
            
            # 如果不合适且已达到最大重试次数，返回需要重新规划
            if not suitable and retry_count >= self.max_retries:
                return False, f"多次重试后仍不合适: {reason}", True
            
            return suitable, reason, need_replan
            
        except Exception as e:
            logger.error(f"检查回复时出错: {e}")
            # 如果出错且已达到最大重试次数，建议重新规划
            if retry_count >= self.max_retries:
                return False, "多次检查失败，建议重新规划", True
            return False, f"检查过程出错，建议重试: {str(e)}", False 