import time
import asyncio
from src.common.logger import get_logger
from src.common.database.database_model import MemoryConflict
from src.chat.utils.chat_message_builder import (
    get_raw_msg_by_timestamp_with_chat,
    build_readable_messages,
)
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from typing import List
from src.memory_system.memory_utils import parse_md_json

logger = get_logger("conflict_tracker")

class QuestionTracker:
    """
    用于跟踪一个问题在后续聊天中的解答情况
    """

    def __init__(self, question: str, chat_id: str, context: str = "") -> None:
        self.question = question
        self.chat_id = chat_id
        now = time.time()
        self.context = context
        self.start_time = now
        self.last_read_time = now
        self.last_judge_time = now  # 上次判定的时间
        self.judge_debounce_interval = 10.0  # 判定防抖间隔：10秒
        self.consecutive_end_count = 0  # 连续END计数
        self.active = True
        # 将 LLM 实例作为类属性，使用 utils 模型
        self.llm_request = LLMRequest(model_set=model_config.model_task_config.utils, request_type="conflict.judge")

    def stop(self) -> None:
        self.active = False
    
    def should_judge_now(self) -> bool:
        """
        检查是否应该进行判定（防抖检查）
        
        Returns:
            bool: 是否可以判定
        """
        now = time.time()
        # 检查是否已经过了10秒的防抖间隔
        return (now - self.last_judge_time) >= self.judge_debounce_interval
    
    def __eq__(self, other) -> bool:
        """比较两个追踪器是否相等（基于问题内容和聊天ID）"""
        if not isinstance(other, QuestionTracker):
            return False
        return self.question == other.question and self.chat_id == other.chat_id
    
    def __hash__(self) -> int:
        """为对象提供哈希值，支持集合操作"""
        return hash((self.question, self.chat_id))

    async def judge_answer(self, conversation_text: str,chat_len: int) -> tuple[bool, str, str]:
        """
        使用模型判定问题是否已得到解答。
        
        Returns:
            tuple[bool, str, str]: (是否结束跟踪, 结束原因或答案, 判定类型)
            - True: 结束跟踪（已解答、话题转向等）
            - False: 继续跟踪
            判定类型: "ANSWERED", "END", "CONTINUE"
        """

        end_prompt = ""
        if chat_len > 20:
            end_prompt = "\n- 如果最新20条聊天记录内容与问题无关，话题已转向其他方向，请只输出：END"

        prompt = f"""你是一个严谨的判定器。下面给出聊天记录以及一个问题。
任务：判断在这段聊天中，该问题是否已经得到明确解答。
**你必须严格按照聊天记录的内容，不要添加额外的信息**

输出规则：
- 如果聊天记录内容的信息已解答问题，请只输出：YES: <简短答案>{end_prompt}
- 如果问题尚未解答但聊天仍在相关话题上，请只输出：NO

**问题**
{self.question}


**聊天记录**
{conversation_text}
"""

        if global_config.debug.show_prompt:
            logger.info(f"判定提示词: {prompt}")
        else:
            logger.debug("已发送判定提示词")

        result_text, _ = await self.llm_request.generate_response_async(prompt, temperature=0.5)
        
        logger.info(f"判定结果: {prompt}\n{result_text}")
        
        # 更新上次判定时间
        self.last_judge_time = time.time()
        
        if not result_text:
            return False, "", "CONTINUE"

        text = result_text.strip()
        if text.upper().startswith("YES:"):
            answer = text[4:].strip()
            return True, answer, "ANSWERED"
        if text.upper().startswith("YES"):
            # 兼容仅输出 YES 或 YES <answer>
            answer = text[3:].strip().lstrip(":").strip()
            return True, answer, "ANSWERED"
        if text.upper().startswith("END"):
            # 聊天内容与问题无关，放弃该问题思考
            return True, "话题已转向其他方向，放弃该问题思考", "END"
        return False, "", "CONTINUE"

class ConflictTracker:
    """
    记忆整合冲突追踪器

    用于记录和存储记忆整合过程中的冲突内容
    """
    def __init__(self):
        self.question_tracker_list:List[QuestionTracker] = []

        self.LLMRequest_tracker = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="conflict_tracker",
        )

    def get_questions_by_chat_id(self, chat_id: str) -> List[QuestionTracker]:
        return [tracker for tracker in self.question_tracker_list if tracker.chat_id == chat_id]

    async def track_conflict(self, question: str, context: str = "",start_following: bool = False,chat_id: str = "") -> bool:
        """
        跟踪冲突内容
        """
        tracker = QuestionTracker(question.strip(), chat_id, context)
        self.question_tracker_list.append(tracker)
        asyncio.create_task(self._follow_and_record(tracker, question.strip()))
        return True
        
    async def record_conflict(self, conflict_content: str, context: str = "",start_following: bool = False,chat_id: str = "") -> bool:
        """
        记录冲突内容

        Args:k
            conflict_content: 冲突内容

        Returns:
            bool: 是否成功记录
        """
        try:
            if not conflict_content or conflict_content.strip() == "":
                return False

            # 若需要跟随后续消息以判断是否得到解答，则进入跟踪流程
            if start_following and chat_id:
                tracker = QuestionTracker(conflict_content.strip(), chat_id, context)
                self.question_tracker_list.append(tracker)
                # 后台启动跟踪任务，避免阻塞
                asyncio.create_task(self._follow_and_record(tracker, conflict_content.strip()))
                return True

            # 默认：直接记录，不进行跟踪
            MemoryConflict.create(
                conflict_content=conflict_content,
                create_time=time.time(),
                update_time=time.time(),
                answer="",
                chat_id=chat_id,
            )

            logger.info(f"记录冲突内容: {len(conflict_content)} 字符")
            return True

        except Exception as e:
            logger.error(f"记录冲突内容时出错: {e}")
            return False

    async def _follow_and_record(self, tracker: QuestionTracker, original_question: str) -> None:
        """
        后台任务：跟踪问题是否被解答，并写入数据库。
        """
        try:
            max_duration = 10 * 60  # 30 分钟
            max_messages = 50      # 最多 100 条消息
            poll_interval = 2.0     # 秒
            logger.info(f"开始跟踪问题: {original_question}")
            while tracker.active:
                now_ts = time.time()
                # 终止条件：时长达到上限
                if now_ts - tracker.start_time >= max_duration:
                    logger.info("问题跟踪达到10分钟上限，判定为未解答")
                    break

                # 统计最近一段是否有新消息（不过滤机器人，过滤命令）
                recent_msgs = get_raw_msg_by_timestamp_with_chat(
                    chat_id=tracker.chat_id,
                    timestamp_start=tracker.last_read_time,
                    timestamp_end=now_ts,
                    limit=30,
                    limit_mode="latest",
                    filter_bot=False,
                    filter_command=True,
                )

                if len(recent_msgs) > 0:
                    tracker.last_read_time = now_ts

                    # 统计从开始到现在的总消息数（用于触发100条上限）
                    all_msgs = get_raw_msg_by_timestamp_with_chat(
                        chat_id=tracker.chat_id,
                        timestamp_start=tracker.start_time,
                        timestamp_end=now_ts,
                        limit=0,
                        limit_mode="latest",
                        filter_bot=False,
                        filter_command=True,
                    )

                    # 检查是否应该进行判定（防抖检查）
                    if not tracker.should_judge_now():
                        logger.debug(f"判定防抖中，跳过本次判定: {tracker.question}")
                        await asyncio.sleep(poll_interval)
                        continue

                    # 构建可读聊天文本
                    chat_text = build_readable_messages(
                        all_msgs,
                        replace_bot_name=True,
                        timestamp_mode="relative",
                        read_mark=0.0,
                        truncate=False,
                        show_actions=False,
                        show_pic=False,
                        remove_emoji_stickers=True,
                    )
                    chat_len = len(all_msgs)
                    # 让小模型判断是否有答案
                    answered, answer_text, judge_type = await tracker.judge_answer(chat_text,chat_len)
                    
                    if judge_type == "ANSWERED":
                        # 问题已解答，直接结束跟踪
                        logger.info("问题已得到解答，结束跟踪并写入答案")
                        await self.add_or_update_conflict(
                            conflict_content=tracker.question,
                            create_time=tracker.start_time,
                            update_time=time.time(),
                            answer=answer_text or "",
                            chat_id=tracker.chat_id,
                        )
                        return
                    elif judge_type == "END":
                        # 话题转向，增加END计数
                        tracker.consecutive_end_count += 1
                        logger.info(f"话题已转向，连续END次数: {tracker.consecutive_end_count}")
                        
                        if tracker.consecutive_end_count >= 2:
                            # 连续两次END，结束跟踪
                            logger.info("连续两次END，结束跟踪")
                            break
                        else:
                            # 第一次END，重置计数器并继续跟踪
                            logger.info("第一次END，继续跟踪")
                            continue
                    elif judge_type == "CONTINUE":
                        # 继续跟踪，重置END计数器
                        tracker.consecutive_end_count = 0
                        continue

                    if len(all_msgs) >= max_messages:
                        logger.info("问题跟踪达到100条消息上限，判定为未解答")
                        logger.info(f"追踪结束：{tracker.question}")
                        break

                # 无新消息时稍作等待
                await asyncio.sleep(poll_interval)

            # 未获取到答案，检查是否需要删除记录
            # 查找现有的冲突记录
            existing_conflict = MemoryConflict.get_or_none(
                MemoryConflict.conflict_content == original_question,
                MemoryConflict.chat_id == tracker.chat_id
            )
            
            if existing_conflict:
                # 检查raise_time是否大于3且没有答案
                current_raise_time = getattr(existing_conflict, "raise_time", 0) or 0
                if current_raise_time > 0 and not existing_conflict.answer:
                    # 删除该条目
                    await self.delete_conflict(original_question, tracker.chat_id)
                    logger.info(f"追踪结束后删除条目(raise_time={current_raise_time}且无答案): {original_question}")
                else:
                    # 更新记录但不删除
                    await self.add_or_update_conflict(
                        conflict_content=original_question,
                        create_time=existing_conflict.create_time,
                        update_time=time.time(),
                        answer="",
                        chat_id=tracker.chat_id,
                    )
                    logger.info(f"记录冲突内容(未解答): {len(original_question)} 字符")
            else:
                # 如果没有现有记录，创建新记录
                await self.add_or_update_conflict(
                    conflict_content=original_question,
                    create_time=time.time(),
                    update_time=time.time(),
                    answer="",
                    chat_id=tracker.chat_id,
                )
                logger.info(f"记录冲突内容(未解答): {len(original_question)} 字符")
            
            logger.info(f"问题跟踪结束：{original_question}")
        except Exception as e:
            logger.error(f"后台问题跟踪任务异常: {e}")
        finally:
            # 无论任务成功还是失败，都要从追踪列表中移除
            tracker.stop()
            self.remove_tracker(tracker)
    
    def remove_tracker(self, tracker: QuestionTracker) -> None:
        """
        从追踪列表中移除指定的追踪器
        
        Args:
            tracker: 要移除的追踪器对象
        """
        try:
            if tracker in self.question_tracker_list:
                self.question_tracker_list.remove(tracker)
                logger.info(f"已从追踪列表中移除追踪器: {tracker.question}")
            else:
                logger.warning(f"尝试移除不存在的追踪器: {tracker.question}")
        except Exception as e:
            logger.error(f"移除追踪器时出错: {e}")

    async def add_or_update_conflict(
        self,
        conflict_content: str,
        create_time: float,
        update_time: float,
        answer: str = "",
        context: str = "",
        chat_id: str = None
    ) -> bool:
        """
        根据conflict_content匹配数据库内容，如果找到相同的就更新update_time和answer，
        如果没有相同的，就新建一条保存全部内容
        """
        try:
            # 尝试根据conflict_content查找现有记录
            existing_conflict = MemoryConflict.get_or_none(
                MemoryConflict.conflict_content == conflict_content,
                MemoryConflict.chat_id == chat_id
            )
            
            if existing_conflict:
                # 如果找到相同的conflict_content，更新update_time和answer
                existing_conflict.update_time = update_time
                existing_conflict.answer = answer
                existing_conflict.save()
                return True
            else:
                # 如果没有找到相同的，创建新记录
                MemoryConflict.create(
                    conflict_content=conflict_content,
                    create_time=create_time,
                    update_time=update_time,
                    answer=answer,
                    context=context,
                    chat_id=chat_id,
                )
                return True
        except Exception as e:
            # 记录错误并返回False
            logger.error(f"添加或更新冲突记录时出错: {e}")
            return False

    async def record_memory_merge_conflict(self, part2_content: str, chat_id: str = None) -> bool:
        """
        记录记忆整合过程中的冲突内容（part2）

        Args:
            part2_content: 冲突内容（part2）

        Returns:
            bool: 是否成功记录
        """
        if not part2_content or part2_content.strip() == "":
            return False

        prompt = f"""以下是一段有冲突的信息，请你根据这些信息总结出几个具体的提问:
冲突信息：
{part2_content}

要求：
1.提问必须具体，明确
2.提问最好涉及指向明确的事物，而不是代称
3.如果缺少上下文，不要强行提问，可以忽略

请用json格式输出，不要输出其他内容，仅输出提问理由和具体提的提问:
**示例**
// 理由文本
```json
{{
    "question":"提问",
}}
```
```json
{{
    "question":"提问"
}}
```
...提问数量在1-3个之间，不要重复，现在请输出："""

        question_response, (reasoning_content, model_name, tool_calls) = await self.LLMRequest_tracker.generate_response_async(prompt)
            
        # 解析JSON响应
        questions, reasoning_content = parse_md_json(question_response)   

        print(prompt)
        print(question_response)

        for question in questions:
            await self.record_conflict(
                conflict_content=question["question"], 
                context=reasoning_content, 
                start_following=False,
                chat_id=chat_id,
            )
        return True

    async def get_conflict_count(self) -> int:
        """
        获取冲突记录数量

        Returns:
            int: 记录数量
        """
        try:
            return MemoryConflict.select().count()
        except Exception as e:
            logger.error(f"获取冲突记录数量时出错: {e}")
            return 0

    async def delete_conflict(self, conflict_content: str, chat_id: str) -> bool:
        """
        删除指定的冲突记录

        Args:
            conflict_content: 冲突内容
            chat_id: 聊天ID

        Returns:
            bool: 是否成功删除
        """
        try:
            conflict = MemoryConflict.get_or_none(
                MemoryConflict.conflict_content == conflict_content,
                MemoryConflict.chat_id == chat_id
            )
            
            if conflict:
                conflict.delete_instance()
                logger.info(f"已删除冲突记录: {conflict_content}")
                return True
            else:
                logger.warning(f"未找到要删除的冲突记录: {conflict_content}")
                return False
        except Exception as e:
            logger.error(f"删除冲突记录时出错: {e}")
            return False

# 全局冲突追踪器实例
global_conflict_tracker = ConflictTracker()