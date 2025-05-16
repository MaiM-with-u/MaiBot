from typing import List, Dict, Any

from .chat_observer import ChatObserver
from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from src.manager.mood_manager import mood_manager
from src.chat.utils.chat_message_builder import build_readable_messages
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from src.config.config import global_config  # 导入全局配置

logger = get_logger("pfc_emotion")


class PfcEmotionUpdater:
    def __init__(self, private_name: str, bot_name: str):
        """
        初始化情绪更新器。
        """
        self.private_name = private_name
        self.bot_name = bot_name
        self.mood_mng = mood_manager

        # LLM 实例 (根据 global_config.llm_summary 配置)
        llm_config_summary = getattr(global_config, "llm_summary", None)
        if llm_config_summary and isinstance(llm_config_summary, dict):
            logger.debug(f"[私聊][{self.private_name}] 使用 llm_summary 配置初始化情绪判断LLM。")
            self.llm = LLMRequest(
                model=llm_config_summary,
                temperature=llm_config_summary.get(
                    "temperature", 0.5
                ),  # temperature 来自其自身配置或默认0.7，这里用0.5
                max_tokens=llm_config_summary.get("max_tokens", 256),  # 情绪词输出不需要很多token
                request_type="pfc_emotion_evaluation",
            )
        else:
            logger.error(f"[私聊][{self.private_name}] 未找到 llm_summary 配置或配置无效！情绪判断功能将受限。")
            self.llm = None  # LLM 未初始化

        self.EMOTION_UPDATE_INTENSITY = getattr(global_config, "pfc_emotion_update_intensity", 0.6)
        self.EMOTION_HISTORY_COUNT = getattr(global_config, "pfc_emotion_history_count", 5)

    async def update_emotion_based_on_context(
        self,
        conversation_info: ConversationInfo,
        observation_info: ObservationInfo,
        chat_observer_for_history: ChatObserver,  # ChatObserver 实例
        event_description: str,
    ) -> None:
        if not self.llm:
            logger.error(f"[私聊][{self.private_name}] LLM未初始化，无法进行情绪更新。")
            # 即使LLM失败，也应该更新conversation_info中的情绪文本为MoodManager的当前状态
            if conversation_info and self.mood_mng:
                conversation_info.current_emotion_text = self.mood_mng.get_mood_prompt()
            return

        if not self.mood_mng or not conversation_info or not observation_info:
            logger.debug(f"[私聊][{self.private_name}] 情绪更新：缺少必要管理器或信息。")
            return

        recent_messages_for_emotion: List[Dict[str, Any]] = []
        if chat_observer_for_history:
            recent_messages_for_emotion = chat_observer_for_history.get_cached_messages(
                limit=self.EMOTION_HISTORY_COUNT
            )
        elif observation_info.chat_history:
            recent_messages_for_emotion = observation_info.chat_history[-self.EMOTION_HISTORY_COUNT :]

        readable_recent_history = await build_readable_messages(
            recent_messages_for_emotion, replace_bot_name=True, merge_messages=True, timestamp_mode="none"
        )

        current_mood_text_from_manager = self.mood_mng.current_mood.text  # 从 MoodManager 获取当前情绪文本
        sender_name_for_prompt = self.private_name
        relationship_text_for_prompt = getattr(
            conversation_info, "relationship_text", "关系一般。"
        )  # 从 ConversationInfo 获取关系文本

        emotion_prompt = f"""你是{self.bot_name}。你现在的心情是【{current_mood_text_from_manager}】。
你正在和用户【{sender_name_for_prompt}】私聊，你们的关系是：【{relationship_text_for_prompt}】。
最近发生的事件是：【{event_description}】
最近的对话摘要：
---
{readable_recent_history}
---
基于以上所有信息，你认为你现在最主要的情绪是什么？请从以下情绪词中选择一个（必须是列表中的一个）：
[开心, 害羞, 愤怒, 恐惧, 悲伤, 厌恶, 惊讶, 困惑, 平静]
请只输出一个最符合的情绪词。例如： 开心
如果难以判断或当前情绪依然合适，请输出： 无变化
"""
        try:
            logger.debug(f"[私聊][{self.private_name}] 情绪判断Prompt:\n{emotion_prompt}")
            content, _ = await self.llm.generate_response_async(emotion_prompt)
            detected_emotion_word = content.strip().replace('"', "").replace("'", "")
            logger.debug(f"[私聊][{self.private_name}] 情绪判断LLM原始返回: '{detected_emotion_word}'")

            if (
                detected_emotion_word
                and detected_emotion_word != "无变化"
                and detected_emotion_word in self.mood_mng.EMOTION_FACTOR_MAP
            ):
                self.mood_mng.update_mood_from_emotion(detected_emotion_word, intensity=self.EMOTION_UPDATE_INTENSITY)
                logger.debug(
                    f"[私聊][{self.private_name}] 基于事件 '{event_description}'，情绪已更新为倾向于 '{detected_emotion_word}'。当前心情: {self.mood_mng.current_mood.text}"
                )
            elif detected_emotion_word == "无变化":
                logger.debug(f"[私聊][{self.private_name}] 基于事件 '{event_description}'，LLM判断情绪无显著变化。")
            else:
                logger.warning(
                    f"[私聊][{self.private_name}] LLM返回了未知的情绪词 '{detected_emotion_word}' 或未返回有效词，情绪未主动更新。"
                )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}] 情绪判断LLM调用或处理失败: {e}")

        # 无论LLM判断如何，都更新conversation_info中的情绪文本以供Prompt使用
        if conversation_info and self.mood_mng:  # 确保conversation_info有效
            conversation_info.current_emotion_text = self.mood_mng.get_mood_prompt()
