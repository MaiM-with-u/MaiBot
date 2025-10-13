import time
import random
from typing import List, Optional, Tuple
from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat, build_readable_messages
from src.common.database.database_model import MemoryConflict
from src.config.config import global_config


class QuestionMaker:
    def __init__(self, chat_id: str, context: str = "") -> None:
        """问题生成器。

        - chat_id: 会话 ID，用于筛选该会话下的冲突记录。
        - context: 额外上下文，可用于后续扩展。

        用法示例：
        >>> qm = QuestionMaker(chat_id="some_chat")
        >>> question, chat_ctx, conflict_ctx = await qm.make_question()
        """
        self.chat_id = chat_id
        self.context = context

    def get_context(self, timestamp: float = time.time()) -> str:
        """获取指定时间点之前的对话上下文字符串。"""
        latest_30_msgs = get_raw_msg_before_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp=timestamp,
            limit=30,
        )   

        all_dialogue_prompt_str = build_readable_messages(
            latest_30_msgs,
            replace_bot_name=True,
            timestamp_mode="normal_no_YMD",
        )
        return all_dialogue_prompt_str


    async def get_all_conflicts(self) -> List[MemoryConflict]:
        """获取当前会话下的所有记忆冲突记录。"""
        conflicts: List[MemoryConflict] = list(MemoryConflict.select().where(MemoryConflict.chat_id == self.chat_id))
        return conflicts
    
    async def get_un_answered_conflict(self) -> List[MemoryConflict]:
        """获取未回答的记忆冲突记录（answer 为空）。"""
        conflicts = await self.get_all_conflicts()
        return [conflict for conflict in conflicts if not conflict.answer]

    async def get_random_unanswered_conflict(self) -> Optional[MemoryConflict]:
        """按权重随机选取一个未回答的冲突并自增 raise_time。

        选择规则：
        - 若存在 `raise_time == 0` 的项：按权重抽样（0 次权重 1.0，≥1 次权重 0.05）。
        - 若不存在 `raise_time == 0` 的项：仅 5% 概率返回其中任意一条，否则返回 None。
        - 每次成功选中后，将该条目的 `raise_time` 自增 1 并保存。
        """
        conflicts = await self.get_un_answered_conflict()
        if not conflicts:
            return None

        # 如果没有 raise_time==0 的项，则仅有 5% 概率抽样一个
        conflicts_with_zero = [c for c in conflicts if (getattr(c, "raise_time", 0) or 0) == 0]
        if conflicts_with_zero:
            # 权重规则：raise_time == 0 -> 1.0；raise_time >= 1 -> 0.01
            weights = []
            for conflict in conflicts:
                current_raise_time = getattr(conflict, "raise_time", 0) or 0
                weight = 1.0 if current_raise_time == 0 else 0.01
                weights.append(weight)

            # 按权重随机选择
            chosen_conflict = random.choices(conflicts, weights=weights, k=1)[0]
        else:
            # 所有冲突都已被提问过（raise_time ≥ 1），仅 5% 概率返回
            if random.random() > 0.05:
                return None
            chosen_conflict = random.choice(conflicts)

        # 选中后，自增 raise_time 并保存
        chosen_conflict.raise_time = (getattr(chosen_conflict, "raise_time", 0) or 0) + 1
        chosen_conflict.save()

        return chosen_conflict

    async def make_question(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """生成一条用于询问用户的冲突问题与上下文。

        返回三元组 (question, chat_context, conflict_context)：
        - question: 冲突文本；若本次未选中任何冲突则为 None。
        - chat_context: 该冲突创建时间点前的会话上下文字符串；若无则为 None。
        - conflict_context: 冲突在 DB 中存储的上下文；若无则为 None。
        """
        conflict = await self.get_random_unanswered_conflict()
        if not conflict:
            return None, None, None
        question = conflict.conflict_content
        conflict_context = conflict.context
        create_time = conflict.create_time
        chat_context = self.get_context(create_time)

        return question, chat_context, conflict_context