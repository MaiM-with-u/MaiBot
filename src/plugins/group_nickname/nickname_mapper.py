# src/plugins/group_nickname/nickname_mapper.py
from typing import Dict
from src.common.logger_manager import get_logger

# 这个文件现在只负责构建 Prompt，LLM 的初始化和调用移至 NicknameManager

logger = get_logger("nickname_mapper")

# LLMRequest 实例和 analyze_chat_for_nicknames 函数已被移除


def _build_mapping_prompt(chat_history_str: str, bot_reply: str, user_name_map: Dict[str, str]) -> str:
    """
    构建用于 LLM 进行绰号映射分析的 Prompt。

    Args:
        chat_history_str: 格式化后的聊天历史记录字符串。
        bot_reply: Bot 的最新回复字符串。
        user_name_map: 用户 ID 到已知名称（person_name 或 fallback nickname）的映射。

    Returns:
        str: 构建好的 Prompt 字符串。
    """
    # 将 user_name_map 格式化为列表字符串
    user_list_str = "\n".join([f"- {uid}: {name}" for uid, name in user_name_map.items() if uid and name])
    if not user_list_str:
        user_list_str = "无"  # 如果映射为空，明确告知

    # 核心 Prompt 内容
    prompt = f"""
任务：仔细分析以下聊天记录和“你的最新回复”，判断其中是否明确提到了某个用户的绰号，并且这个绰号可以清晰地与一个特定的用户 ID 对应起来。

已知用户信息（ID: 名称）：
{user_list_str}

聊天记录：
---
{chat_history_str}
---

你的最新回复：
{bot_reply}

分析要求与输出格式：
1.  找出聊天记录和“你的最新回复”中可能是用户绰号的词语。
2.  判断这些绰号是否在上下文中**清晰、无歧义**地指向了“已知用户信息”列表中的**某一个特定用户 ID**。必须是强关联，避免猜测。
3.  **不要**输出你自己（名称后带"(你)"的用户）的绰号映射。
    **不要**输出与用户已知名称完全相同的词语作为绰号。
    **不要**将在“你的最新回复”中你对他人使用的称呼或绰号进行映射（只分析聊天记录中他人对用户的称呼）。
    **不要**输出指代不明或过于通用的词语（如“大佬”、“兄弟”、“那个谁”等，除非上下文能非常明确地指向特定用户）。
4.  如果找到了**至少一个**满足上述所有条件的**明确**的用户 ID 到绰号的映射关系，请输出 JSON 对象：
        ```json
        {{
            "is_exist": true,
            "data": {{
                "用户A数字id": "绰号_A",
                "用户B数字id": "绰号_B"
            }}
        }}
        ```
        - `"data"` 字段的键必须是用户的**数字 ID (字符串形式)**，值是对应的**绰号 (字符串形式)**。
        - 只包含你能**百分百确认**映射关系的条目。宁缺毋滥。
    如果**无法找到任何一个**满足条件的明确映射关系，请输出 JSON 对象：
        ```json
        {{
            "is_exist": false
        }}
        ```
5.  请**仅**输出 JSON 对象，不要包含任何额外的解释、注释或代码块标记之外的文本。

输出：
"""
    # logger.debug(f"构建的绰号映射 Prompt (部分):\n{prompt[:500]}...") # 可以在 NicknameManager 中记录
    return prompt


# analyze_chat_for_nicknames 函数已被移除，其逻辑移至 NicknameManager._call_llm_for_analysis
