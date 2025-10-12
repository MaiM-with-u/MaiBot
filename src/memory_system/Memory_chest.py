import asyncio
import json
import re
import time
import random

from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config
from src.common.database.database_model import MemoryChest as MemoryChestModel
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system.apis.message_api import build_readable_messages
from src.plugin_system.apis.message_api import get_raw_msg_by_timestamp_with_chat
from json_repair import repair_json
from src.memory_system.questions import global_conflict_tracker

from .memory_utils import (
    find_best_matching_memory,
    check_title_exists_fuzzy,
    get_all_titles,
    get_memory_titles_by_chat_id_weighted,

)

logger = get_logger("memory")

class MemoryChest:
    def __init__(self):
        
        self.LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.utils_small,
            request_type="memory_chest",
        )
        
        self.LLMRequest_build = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="memory_chest_build",
        )
        
  
        self.running_content_list = {}  # {chat_id: {"content": running_content, "last_update_time": timestamp, "create_time": timestamp}}
        self.fetched_memory_list = []  # [(chat_id, (question, answer, timestamp)), ...]

    async def build_running_content(self, chat_id: str = None) -> str:
        """
        构建记忆仓库的运行内容

        Args:
            message_str: 消息内容
            chat_id: 聊天ID，用于提取对应的运行内容

        Returns:
            str: 构建后的运行内容
        """
        # 检查是否需要更新：基于消息数量和最新消息时间差的智能更新机制
        # 
        # 更新机制说明：
        # 1. 消息数量 > 100：直接触发更新（高频消息场景）
        # 2. 消息数量 > 70 且最新消息时间差 > 30秒：触发更新（中高频消息场景）
        # 3. 消息数量 > 50 且最新消息时间差 > 60秒：触发更新（中频消息场景）
        # 4. 消息数量 > 30 且最新消息时间差 > 300秒：触发更新（低频消息场景）
        # 
        # 设计理念：
        # - 消息越密集，时间阈值越短，确保及时更新记忆
        # - 消息越稀疏，时间阈值越长，避免频繁无意义的更新
        # - 通过最新消息时间差判断消息活跃度，而非简单的总时间差
        # - 平衡更新频率与性能，在保证记忆及时性的同时减少计算开销
        if chat_id not in self.running_content_list:
            self.running_content_list[chat_id] = {
                "content": "",
                "last_update_time": time.time(),
                "create_time": time.time()
            }
        
        should_update = True
        if chat_id and chat_id in self.running_content_list:
            last_update_time = self.running_content_list[chat_id]["last_update_time"]
            current_time = time.time()
            # 使用message_api获取消息数量
            message_list =  get_raw_msg_by_timestamp_with_chat(
                timestamp_start=last_update_time,
                timestamp_end=current_time,
                chat_id=chat_id,
                limit=global_config.chat.max_context_size * 2,
            )

            new_messages_count = len(message_list)
            
            # 获取最新消息的时间戳
            latest_message_time = last_update_time
            if message_list:
                # 假设消息列表按时间排序，取最后一条消息的时间戳
                latest_message = message_list[-1]
                if hasattr(latest_message, 'timestamp'):
                    latest_message_time = latest_message.timestamp
                elif isinstance(latest_message, dict) and 'timestamp' in latest_message:
                    latest_message_time = latest_message['timestamp']
            
            # 计算最新消息时间与现在时间的差（秒）
            latest_message_time_diff = current_time - latest_message_time
            
            # 智能更新条件判断 - 按优先级从高到低检查
            should_update = False
            update_reason = ""
            
            if global_config.memory.memory_build_frequency > 0:
                if new_messages_count > 100/global_config.memory.memory_build_frequency:
                    # 条件1：消息数量 > 100，直接触发更新
                    # 适用场景：群聊刷屏、高频讨论等消息密集场景
                    # 无需时间限制，确保重要信息不被遗漏
                    should_update = True
                    update_reason = f"消息数量 {new_messages_count} > 100，直接触发更新"
                elif new_messages_count > 70/global_config.memory.memory_build_frequency and latest_message_time_diff > 30:
                    # 条件2：消息数量 > 70 且最新消息时间差 > 30秒
                    # 适用场景：中高频讨论，但需要确保消息流已稳定
                    # 30秒的时间差确保不是正在进行的实时对话
                    should_update = True
                    update_reason = f"消息数量 {new_messages_count} > 70 且最新消息时间差 {latest_message_time_diff:.1f}s > 30s"
                elif new_messages_count > 50/global_config.memory.memory_build_frequency and latest_message_time_diff > 60:
                    # 条件3：消息数量 > 50 且最新消息时间差 > 60秒
                    # 适用场景：中等频率讨论，等待1分钟确保对话告一段落
                    # 平衡及时性与稳定性
                    should_update = True
                    update_reason = f"消息数量 {new_messages_count} > 50 且最新消息时间差 {latest_message_time_diff:.1f}s > 60s"
                elif new_messages_count > 30/global_config.memory.memory_build_frequency and latest_message_time_diff > 300:
                    # 条件4：消息数量 > 30 且最新消息时间差 > 300秒（5分钟）
                    # 适用场景：低频但有一定信息量的讨论
                    # 5分钟的时间差确保对话完全结束，避免频繁更新
                    should_update = True
                    update_reason = f"消息数量 {new_messages_count} > 30 且最新消息时间差 {latest_message_time_diff:.1f}s > 300s"
                
                logger.debug(f"chat_id {chat_id} 更新检查: {update_reason if should_update else f'消息数量 {new_messages_count}，最新消息时间差 {latest_message_time_diff:.1f}s，不满足更新条件'}")


        if should_update:
            # 如果有chat_id，先提取对应的running_content
            message_str = build_readable_messages(
                message_list,
                replace_bot_name=True,
                timestamp_mode="relative",
                read_mark=0.0,
                show_actions=False,
                remove_emoji_stickers=True,
            )
            
            # 随机从格式示例列表中选取若干行用于提示
            format_candidates = [
                "[概念] 是 [概念的含义(简短描述，不超过十个字)]",
                "[概念] 不是 [对概念的负面含义(简短描述，不超过十个字)]",
                "[概念1] 与 [概念2] 是 [概念1和概念2的关联(简短描述，不超过二十个字)]",
                "[概念1] 包含 [概念2] 和 [概念3]",
                "[概念1] 属于 [概念2]",
                "[概念1] 的例子是 [例子1] 和 [例子2]",
                "[概念] 的特征是 [特征1]、[特征2]",
                "[概念1] 导致 [概念2]",
                "[概念1] 需要 [条件1] 和 [条件2]",
                "[概念1] 的用途是 [用途1] 和 [用途2]",
                "[概念1] 与 [概念2] 的区别是 [区别点]",
                "[概念] 的别名是 [别名]",
                "[概念1] 包括但不限于 [概念2]、[概念3]",
                "[概念] 的反义是 [反义概念]",
                "[概念] 的组成有 [部分1]、[部分2]",
                "[概念] 出现于 [时间或场景]",
                "[概念] 的方法有 [方法1]、[方法2]",
            ]

            selected_count = random.randint(3, 6)
            selected_lines = random.sample(format_candidates, selected_count)
            format_section = "\n".join(selected_lines) + "\n......(不要包含中括号)"

            prompt = f"""
以下是一段你参与的聊天记录，请你在其中总结出记忆：

<聊天记录>
{message_str}
</聊天记录>
聊天记录中可能包含有效信息，也可能信息密度很低，请你根据聊天记录中的信息，总结出记忆内容
--------------------------------
对[图片]的处理：
1.除非与文本有关，不要将[图片]的内容整合到记忆中
2.如果图片与某个概念相关，将图片中的关键内容也整合到记忆中，不要写入图片原文，例如：

聊天记录（与图片有关）：
用户说：[图片1：这是一个黄色的龙形状玩偶，被一只手拿着。]
用户说：这个玩偶看起来很可爱，是我新买的奶龙
总结的记忆内容：
黄色的龙形状玩偶 是 奶龙

聊天记录（概念与图片无关）：
用户说：[图片1：这是一个台电脑，屏幕上显示了某种游戏。]
用户说：使命召唤今天发售了新一代，有没有人玩
总结的记忆内容：
使命召唤新一代 是 最新发售的游戏

请主要关注概念和知识或者时效性较强的信息！！，而不是聊天的琐事
1.不要关注诸如某个用户做了什么，说了什么，不要关注某个用户的行为，而是关注其中的概念性信息
2.概念要求精确，不啰嗦，像科普读物或教育课本那样
3.记忆为一段纯文本，逻辑清晰，指出概念的含义，并说明关系

 记忆内容的格式，你必须仿照下面的格式，但不一定全部使用:
{format_section}

请仿照上述格式输出，每个知识点一句话。输出成一段平文本
现在请你输出,不要输出其他内容，注意一定要直白，白话，口语化不要浮夸，修辞。：
"""

            if global_config.debug.show_prompt:
                logger.info(f"记忆仓库构建运行内容 prompt: {prompt}")
            else:
                logger.debug(f"记忆仓库构建运行内容 prompt: {prompt}")

            running_content, (reasoning_content, model_name, tool_calls) = await self.LLMRequest_build.generate_response_async(prompt)

            print(f"prompt: {prompt}\n记忆仓库构建运行内容: {running_content}")

            # 直接保存：每次构建后立即入库，并刷新时间戳窗口
            if chat_id and running_content:
                await self._save_to_database_and_clear(chat_id, running_content)


            return running_content
        
        
    async def get_answer_by_question(self, chat_id: str = "", question: str = "") -> str:
        """
        根据问题获取答案
        """
        logger.info(f"正在回忆问题答案: {question}")
        
        title = await self.select_title_by_question(question)
        
        if not title:
            return ""
        
        for memory in MemoryChestModel.select():
            if memory.title == title:
                content =  memory.content
                
        if random.random() < 0.5:        
            type = "要求原文能够较为全面的回答问题"
        else:
            type = "要求提取简短的内容"
            
        prompt = f"""
目标文段：
{content}

你现在需要从目标文段中找出合适的信息来回答问题：{question}
请务必从目标文段中提取相关信息的**原文**并输出，{type}
如果没有原文能够回答问题，输出"无有效信息"即可，不要输出其他内容：
"""

        if global_config.debug.show_prompt:
            logger.info(f"记忆仓库获取答案 prompt: {prompt}")
        else:
            logger.debug(f"记忆仓库获取答案 prompt: {prompt}")

        answer, (reasoning_content, model_name, tool_calls) = await self.LLMRequest.generate_response_async(prompt)
        
        if "无有效" in answer or "无有效信息" in answer or "无信息" in answer:
            logger.info(f"没有能够回答{question}的记忆")
            return ""
        
        logger.info(f"记忆仓库对问题 “{question}” 获取答案: {answer}")

        # 将问题和答案存到fetched_memory_list
        if chat_id and answer:
            self.fetched_memory_list.append((chat_id, (question, answer, time.time())))

            # 清理fetched_memory_list
            self._cleanup_fetched_memory_list()

        return answer

    def get_chat_memories_as_string(self, chat_id: str) -> str:
        """
        获取某个chat_id的所有记忆，并构建成字符串

        Args:
            chat_id: 聊天ID

        Returns:
            str: 格式化的记忆字符串，格式：问题：xxx,答案:xxxxx\n问题：xxx,答案:xxxxx\n...
        """
        try:
            memories = []

            # 从fetched_memory_list中获取该chat_id的所有记忆
            for cid, (question, answer, timestamp) in self.fetched_memory_list:
                if cid == chat_id:
                    memories.append(f"问题：{question},答案:{answer}")

            # 按时间戳排序（最新的在后面）
            memories.sort()

            # 用换行符连接所有记忆
            result = "\n".join(memories)

            # logger.info(f"chat_id {chat_id} 共有 {len(memories)} 条记忆")
            return result

        except Exception as e:
            logger.error(f"获取chat_id {chat_id} 的记忆时出错: {e}")
            return ""


    async def select_title_by_question(self, question: str) -> str:
        """
        根据消息内容选择最匹配的标题

        Args:
            question: 问题

        Returns:
            str: 选择的标题
        """
        # 获取所有标题并构建格式化字符串（排除锁定的记忆）
        titles = get_all_titles(exclude_locked=True)
        formatted_titles = ""
        for title in titles:
            formatted_titles += f"{title}\n"

        prompt = f"""
所有主题：
{formatted_titles}

请根据以下问题，选择一个能够回答问题的主题：
问题：{question}
请你输出主题，不要输出其他内容，完整输出主题名：
"""

        if global_config.debug.show_prompt:
            logger.info(f"记忆仓库选择标题 prompt: {prompt}")
        else:
            logger.debug(f"记忆仓库选择标题 prompt: {prompt}")
            
            
        title, (reasoning_content, model_name, tool_calls) = await self.LLMRequest.generate_response_async(prompt)

        # 根据 title 获取 titles 里的对应项
        selected_title = None

        # 使用模糊查找匹配标题
        best_match = find_best_matching_memory(title, similarity_threshold=0.8)
        if best_match:
            selected_title = best_match[0]  # 获取匹配的标题
            logger.info(f"记忆仓库选择标题: {selected_title} (相似度: {best_match[2]:.3f})")
        else:
            logger.warning(f"未找到相似度 >= 0.7 的标题匹配: {title}")
            selected_title = None

        return selected_title

    def _cleanup_fetched_memory_list(self):
        """
        清理fetched_memory_list，移除超过10分钟的记忆和超过10条的最旧记忆
        """
        try:
            current_time = time.time()
            ten_minutes_ago = current_time - 600  # 10分钟 = 600秒

            # 移除超过10分钟的记忆
            self.fetched_memory_list = [
                (chat_id, (question, answer, timestamp))
                for chat_id, (question, answer, timestamp) in self.fetched_memory_list
                if timestamp > ten_minutes_ago
            ]

            # 如果记忆条数超过10条，移除最旧的5条
            if len(self.fetched_memory_list) > 10:
                # 按时间戳排序，移除最旧的5条
                self.fetched_memory_list.sort(key=lambda x: x[1][2])  # 按timestamp排序
                self.fetched_memory_list = self.fetched_memory_list[5:]  # 保留最新的5条

            logger.debug(f"fetched_memory_list清理后，当前有 {len(self.fetched_memory_list)} 条记忆")

        except Exception as e:
            logger.error(f"清理fetched_memory_list时出错: {e}")

    async def _save_to_database_and_clear(self, chat_id: str, content: str):
        """
        生成标题，保存到数据库，并清空对应chat_id的running_content

        Args:
            chat_id: 聊天ID
            content: 要保存的内容
        """
        try:
            # 生成标题
            title = ""
            title_prompt = f"""
请为以下内容生成一个描述全面的标题，要求描述内容的主要概念和事件：
{content}

标题不要分点，不要换行，不要输出其他内容
请只输出标题，不要输出其他内容：
"""

            if global_config.debug.show_prompt:
                logger.info(f"记忆仓库生成标题 prompt: {title_prompt}")
            else:
                logger.debug(f"记忆仓库生成标题 prompt: {title_prompt}")

            title, (reasoning_content, model_name, tool_calls) = await self.LLMRequest_build.generate_response_async(title_prompt)

            
            await asyncio.sleep(0.5)
            
            if title:
                # 保存到数据库
                MemoryChestModel.create(
                    title=title.strip(),
                    content=content,
                    chat_id=chat_id
                )
                logger.info(f"已保存记忆仓库内容，标题: {title.strip()}, chat_id: {chat_id}")

                # 清空内容并刷新时间戳，但保留条目用于增量计算
                if chat_id in self.running_content_list:
                    current_time = time.time()
                    self.running_content_list[chat_id] = {
                        "content": "",
                        "last_update_time": current_time,
                        "create_time": current_time
                    }
                    logger.info(f"已保存并刷新chat_id {chat_id} 的时间戳，准备下一次增量构建")
            else:
                logger.warning(f"生成标题失败，chat_id: {chat_id}")

        except Exception as e:
            logger.error(f"保存记忆仓库内容时出错: {e}")
    
    async def choose_merge_target(self, memory_title: str, chat_id: str = None) -> list[str]:
        """
        选择与给定记忆标题相关的记忆目标

        Args:
            memory_title: 要匹配的记忆标题
            chat_id: 聊天ID，用于加权抽样

        Returns:
            list[str]: 选中的记忆内容列表
        """
        try:
        # 如果提供了chat_id，使用加权抽样
            all_titles = get_memory_titles_by_chat_id_weighted(chat_id)
            # 剔除掉输入的 memory_title 本身
            all_titles = [title for title in all_titles if title and title.strip() != (memory_title or "").strip()]
            
            content = ""
            display_index = 1
            for title in all_titles:
                content += f"{display_index}. {title}\n"
                display_index += 1
            
            prompt = f"""
所有记忆列表
{content}

请根据以上记忆列表，选择一个与"{memory_title}"相关的记忆，用json输出：
如果没有相关记忆，输出:
{{
    "selected_title": ""
}}
可以选择多个相关的记忆，但最多不超过5个
例如：
{{
    "selected_title": "选择的相关记忆标题"
}},
{{
    "selected_title": "选择的相关记忆标题"
}},
{{
    "selected_title": "选择的相关记忆标题"
}}
...
注意：请返回原始标题本身作为 selected_title，不要包含前面的序号或多余字符。
请输出JSON格式，不要输出其他内容：
"""

            # logger.info(f"选择合并目标 prompt: {prompt}")

            if global_config.debug.show_prompt:
                logger.info(f"选择合并目标 prompt: {prompt}")
            else:
                logger.debug(f"选择合并目标 prompt: {prompt}")
                
            merge_target_response, (reasoning_content, model_name, tool_calls) = await self.LLMRequest_build.generate_response_async(prompt)
            
            # 解析JSON响应
            selected_titles = self._parse_merge_target_json(merge_target_response)
            
            # 根据标题查找对应的内容
            selected_contents = self._get_memories_by_titles(selected_titles)
            
            logger.info(f"选择合并目标结果: {len(selected_contents)} 条记忆:{selected_titles}")
            return selected_titles,selected_contents
            
        except Exception as e:
            logger.error(f"选择合并目标时出错: {e}")
            return []
    
    def _get_memories_by_titles(self, titles: list[str]) -> list[str]:
        """
        根据标题列表查找对应的记忆内容

        Args:
            titles: 记忆标题列表

        Returns:
            list[str]: 记忆内容列表
        """
        try:
            contents = []
            for title in titles:
                if not title or not title.strip():
                    continue

                # 使用模糊查找匹配记忆
                try:
                    best_match = find_best_matching_memory(title.strip(), similarity_threshold=0.8)
                    if best_match:
                        # 检查记忆是否被锁定
                        memory_title = best_match[0]
                        memory_content = best_match[1]

                        # 查询数据库中的锁定状态
                        for memory in MemoryChestModel.select():
                            if memory.title == memory_title and memory.locked:
                                logger.warning(f"记忆 '{memory_title}' 已锁定，跳过合并")
                                continue

                        contents.append(memory_content)
                        logger.debug(f"找到记忆: {memory_title} (相似度: {best_match[2]:.3f})")
                    else:
                        logger.warning(f"未找到相似度 >= 0.8 的标题匹配: '{title}'")
                except Exception as e:
                    logger.error(f"查找标题 '{title}' 的记忆时出错: {e}")
                    continue

            # logger.info(f"成功找到 {len(contents)} 条记忆内容")
            return contents

        except Exception as e:
            logger.error(f"根据标题查找记忆时出错: {e}")
            return []
    
    def _parse_merged_parts(self, merged_response: str) -> tuple[str, str]:
        """
        解析合并记忆的part1和part2内容

        Args:
            merged_response: LLM返回的合并记忆响应

        Returns:
            tuple[str, str]: (part1_content, part2_content)
        """
        try:
            # 使用正则表达式提取part1和part2内容
            import re

            # 提取part1内容
            part1_pattern = r'<part1>(.*?)</part1>'
            part1_match = re.search(part1_pattern, merged_response, re.DOTALL)
            part1_content = part1_match.group(1).strip() if part1_match else ""

            # 提取part2内容
            part2_pattern = r'<part2>(.*?)</part2>'
            part2_match = re.search(part2_pattern, merged_response, re.DOTALL)
            part2_content = part2_match.group(1).strip() if part2_match else ""

            # 检查是否包含none或None（不区分大小写）
            def is_none_content(content: str) -> bool:
                if not content:
                    return True
                # 检查是否只包含"none"或"None"（不区分大小写）
                return re.match(r'^\s*none\s*$', content, re.IGNORECASE) is not None

            # 如果包含none，则设置为空字符串
            if is_none_content(part1_content):
                part1_content = ""
                logger.info("part1内容为none，设置为空")

            if is_none_content(part2_content):
                part2_content = ""
                logger.info("part2内容为none，设置为空")

            return part1_content, part2_content

        except Exception as e:
            logger.error(f"解析合并记忆part1/part2时出错: {e}")
            return "", ""

    def _parse_merge_target_json(self, json_text: str) -> list[str]:
        """
        解析choose_merge_target生成的JSON响应
        
        Args:
            json_text: LLM返回的JSON文本
            
        Returns:
            list[str]: 解析出的记忆标题列表
        """
        try:
            # 清理JSON文本，移除可能的额外内容
            repaired_content = repair_json(json_text)
            
            # 尝试直接解析JSON
            try:
                parsed_data = json.loads(repaired_content)
                if isinstance(parsed_data, list):
                    # 如果是列表，提取selected_title字段
                    titles = []
                    for item in parsed_data:
                        if isinstance(item, dict) and "selected_title" in item:
                            value = item.get("selected_title", "")
                            if isinstance(value, str) and value.strip():
                                titles.append(value)
                    return titles
                elif isinstance(parsed_data, dict) and "selected_title" in parsed_data:
                    # 如果是单个对象
                    value = parsed_data.get("selected_title", "")
                    if isinstance(value, str) and value.strip():
                        return [value]
                    else:
                        # 空字符串表示没有相关记忆
                        return []
            except json.JSONDecodeError:
                pass
            
            # 如果直接解析失败，尝试提取JSON对象
            # 查找所有包含selected_title的JSON对象
            pattern = r'\{[^}]*"selected_title"[^}]*\}'
            matches = re.findall(pattern, repaired_content)
            
            titles = []
            for match in matches:
                try:
                    obj = json.loads(match)
                    if "selected_title" in obj:
                        value = obj.get("selected_title", "")
                        if isinstance(value, str) and value.strip():
                            titles.append(value)
                except json.JSONDecodeError:
                    continue
            
            if titles:
                return titles
            
            logger.warning(f"无法解析JSON响应: {json_text[:200]}...")
            return []
            
        except Exception as e:
            logger.error(f"解析合并目标JSON时出错: {e}")
            return []
            
    async def merge_memory(self,memory_list: list[str], chat_id: str = None) -> tuple[str, str]:
        """
        合并记忆
        """
        try:
            content = ""
            for memory in memory_list:
                content += f"{memory}\n"
            
            prompt = f"""
以下是多段记忆内容，请将它们进行整合和修改：
{content}
--------------------------------
请将上面的多段记忆内容，合并成两部分内容，第一部分是可以整合，不冲突的概念和知识，第二部分是相互有冲突的概念和知识
请主要关注概念和知识，而不是聊天的琐事
重要！！你要关注的概念和知识必须是较为不常见的信息，或者时效性较强的信息！！
不要！！关注常见的只是，或者已经过时的信息！！
1.不要关注诸如某个用户做了什么，说了什么，不要关注某个用户的行为，而是关注其中的概念性信息
2.概念要求精确，不啰嗦，像科普读物或教育课本那样
3.如果有图片，请只关注图片和文本结合的知识和概念性内容
4.记忆为一段纯文本，逻辑清晰，指出概念的含义，并说明关系
**第一部分**
1.如果两个概念在描述同一件事情，且相互之间逻辑不冲突（请你严格判断），且相互之间没有矛盾，请将它们整合成一个概念，并输出到第一部分
2.如果某个概念在时间上更新了另一个概念，请用新概念更新就概念来整合，并输出到第一部分
3.如果没有可整合的概念，请你输出none
**第二部分**
1.如果记忆中有无法整合的地方，例如概念不一致，有逻辑上的冲突，请你输出到第二部分
2.如果两个概念在描述同一件事情，但相互之间逻辑冲突，请将它们输出到第二部分
3.如果没有无法整合的概念，请你输出none

**输出格式要求**
请你按以下格式输出：
<part1>
第一部分内容，整合后的概念，如果第一部分为none，请输出none
</part1>
<part2>
第二部分内容，无法整合，冲突的概念，如果第二部分为none，请输出none  
</part2>
不要输出其他内容，现在请你输出,不要输出其他内容，注意一定要直白，白话，口语化不要浮夸，修辞。：
"""

            if global_config.debug.show_prompt:
                logger.info(f"合并记忆 prompt: {prompt}")
            else:
                logger.debug(f"合并记忆 prompt: {prompt}")

            merged_memory, (reasoning_content, model_name, tool_calls) = await self.LLMRequest_build.generate_response_async(prompt)

            # 解析part1和part2
            part1_content, part2_content = self._parse_merged_parts(merged_memory)

            # 处理part2：独立记录冲突内容（无论part1是否为空）
            if part2_content and part2_content.strip() != "none":
                logger.info(f"合并记忆part2记录冲突内容: {len(part2_content)} 字符")
                # 记录冲突到数据库
                await global_conflict_tracker.record_memory_merge_conflict(part2_content,chat_id)

            # 处理part1：生成标题并保存
            if part1_content and part1_content.strip() != "none":
                merged_title = await self._generate_title_for_merged_memory(part1_content)

                # 保存part1到数据库
                MemoryChestModel.create(
                    title=merged_title,
                    content=part1_content,
                    chat_id=chat_id
                )

                logger.info(f"合并记忆part1已保存: {merged_title}")

                return merged_title, part1_content
            else:
                logger.warning("合并记忆part1为空，跳过保存")
                return "", ""
        except Exception as e:
            logger.error(f"合并记忆时出错: {e}")
            return "", ""
    
    async def _generate_title_for_merged_memory(self, merged_content: str) -> str:
        """
        为合并后的记忆生成标题
        
        Args:
            merged_content: 合并后的记忆内容
            
        Returns:
            str: 生成的标题
        """
        try:
            prompt = f"""
请为以下内容生成一个描述全面的标题，要求描述内容的主要概念和事件：
例如：
<example>
标题：达尔文的自然选择理论
内容：达尔文的自然选择是生物进化理论的重要组成部分，它解释了生物进化过程中的自然选择机制。
</example>
<example>
标题：麦麦的禁言插件和支持版本
内容：
麦麦的禁言插件是一款能够实现禁言的插件
麦麦的禁言插件可能不支持0.10.2
MutePlugin 是禁言插件的名称
</example>


需要对以下内容生成标题：
{merged_content}


标题不要分点，不要换行，不要输出其他内容，不要浮夸，以白话简洁的风格输出标题
请只输出标题，不要输出其他内容：
"""
            
            if global_config.debug.show_prompt:
                logger.info(f"生成合并记忆标题 prompt: {prompt}")
            else:
                logger.debug(f"生成合并记忆标题 prompt: {prompt}")
            
            title_response, (reasoning_content, model_name, tool_calls) = await self.LLMRequest.generate_response_async(prompt)
            
            # 清理标题，移除可能的引号或多余字符
            title = title_response.strip().strip('"').strip("'").strip()
            
            if title:
                # 检查是否存在相似标题
                if check_title_exists_fuzzy(title, similarity_threshold=0.9):
                    logger.warning(f"生成的标题 '{title}' 与现有标题相似，使用时间戳后缀")
                    title = f"{title}_{int(time.time())}"
                
                logger.info(f"生成合并记忆标题: {title}")
                return title
            else:
                logger.warning("生成合并记忆标题失败，使用默认标题")
                return f"合并记忆_{int(time.time())}"
                
        except Exception as e:
            logger.error(f"生成合并记忆标题时出错: {e}")
            return f"合并记忆_{int(time.time())}"
    
    
global_memory_chest = MemoryChest()