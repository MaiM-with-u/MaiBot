# -*- coding: utf-8 -*-
import time
import re
import json
import ast
import traceback

from json_repair import repair_json
from datetime import datetime, timedelta

from src.llm_models.utils_model import LLMRequest
from src.common.logger import get_logger
from src.common.database.database_model import Memory  # Peewee Models导入
from src.config.config import model_config


logger = get_logger(__name__)


class MemoryItem:
    def __init__(self, memory_id: str, chat_id: str, memory_text: str, keywords: list[str]):
        self.memory_id = memory_id
        self.chat_id = chat_id
        self.memory_text: str = memory_text
        self.keywords: list[str] = keywords
        self.create_time: float = time.time()
        self.last_view_time: float = time.time()


class MemoryManager:
    def __init__(self):
        # self.memory_items:list[MemoryItem] = []
        pass


class InstantMemory:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.last_view_time = time.time()
        self.summary_model = LLMRequest(
            model_set=model_config.model_task_config.memory,
            request_type="memory.summary",
        )

    async def if_need_build(self, text):
        prompt = f"""
请判断以下内容中是否有值得记忆的信息，如果有，请输出1，否则输出0
{text}
请只输出1或0就好
        """

        try:
            response, _ = await self.summary_model.generate_response_async(prompt, temperature=0.5)
            print(prompt)
            print(response)

            return "1" in response
        except Exception as e:
            logger.error(f"判断是否需要记忆出现错误：{str(e)} {traceback.format_exc()}")
            return False

    async def build_memory(self, text):
        prompt = f"""
        以下内容中存在值得记忆的信息，请你从中总结出一段值得记忆的信息，并输出
        {text}
        请以json格式输出一段概括的记忆内容和关键词
        {{
            "memory_text": "记忆内容",
            "keywords": "关键词，用/划分"
        }}
        """
        try:
            response, _ = await self.summary_model.generate_response_async(prompt, temperature=0.5)
            # print(prompt)
            # print(response)
            if not response:
                return None
            try:
                repaired = repair_json(response)
                result = json.loads(repaired)
                memory_text = result.get("memory_text", "")
                keywords = result.get("keywords", "")
                if isinstance(keywords, str):
                    keywords_list = [k.strip() for k in keywords.split("/") if k.strip()]
                elif isinstance(keywords, list):
                    keywords_list = keywords
                else:
                    keywords_list = []
                return {"memory_text": memory_text, "keywords": keywords_list}
            except Exception as parse_e:
                logger.error(f"解析记忆json失败：{str(parse_e)} {traceback.format_exc()}")
                return None
        except Exception as e:
            logger.error(f"构建记忆出现错误：{str(e)} {traceback.format_exc()}")
            return None

    async def create_and_store_memory(self, text):
        if_need = await self.if_need_build(text)
        if if_need:
            logger.info(f"需要记忆：{text}")
            memory = await self.build_memory(text)
            if memory and memory.get("memory_text"):
                memory_id = f"{self.chat_id}_{time.time()}"
                memory_item = MemoryItem(
                    memory_id=memory_id,
                    chat_id=self.chat_id,
                    memory_text=memory["memory_text"],
                    keywords=memory.get("keywords", []),
                )
                await self.store_memory(memory_item)
        else:
            logger.info(f"不需要记忆：{text}")

    async def store_memory(self, memory_item: MemoryItem):
        memory = Memory(
            memory_id=memory_item.memory_id,
            chat_id=memory_item.chat_id,
            memory_text=memory_item.memory_text,
            keywords=memory_item.keywords,
            create_time=memory_item.create_time,
            last_view_time=memory_item.last_view_time,
        )
        memory.save()

    async def get_memory(self, target: str):
        from json_repair import repair_json

        prompt = f"""
        请根据以下发言内容，判断是否需要提取记忆
        {target}
        请用json格式输出，包含以下字段：
        其中，time的要求是：
        可以选择具体日期时间，格式为YYYY-MM-DD HH:MM:SS，或者大致时间，格式为YYYY-MM-DD
        可以选择相对时间，例如：今天，昨天，前天，5天前，1个月前
        可以选择留空进行模糊搜索
        {{
            "need_memory": 1,
            "keywords": "希望获取的记忆关键词，用/划分",
            "time": "希望获取的记忆大致时间"
        }}
        请只输出json格式，不要输出其他多余内容
        """
        try:
            response, _ = await self.summary_model.generate_response_async(prompt, temperature=0.5)
            print(prompt)
            print(response)
            if not response:
                return None
            try:
                repaired = repair_json(response)
                result = json.loads(repaired)
                # 解析keywords
                keywords = result.get("keywords", "")
                if isinstance(keywords, str):
                    keywords_list = [k.strip() for k in keywords.split("/") if k.strip()]
                elif isinstance(keywords, list):
                    keywords_list = keywords
                else:
                    keywords_list = []
                # 解析time为时间段
                time_str = result.get("time", "").strip()
                start_time, end_time = self._parse_time_range(time_str)
                logger.info(f"start_time: {start_time}, end_time: {end_time}")
                # 检索包含关键词的记忆
                memories_set = set()
                if start_time and end_time:
                    start_ts = start_time.timestamp()
                    end_ts = end_time.timestamp()
                    query = Memory.select().where(
                        (Memory.chat_id == self.chat_id)
                        & (Memory.create_time >= start_ts)  # type: ignore
                        & (Memory.create_time < end_ts)  # type: ignore
                    )
                else:
                    query = Memory.select().where(Memory.chat_id == self.chat_id)

                for mem in query:
                    # 对每条记忆
                    mem_keywords = mem.keywords or ""
                    parsed = ast.literal_eval(mem_keywords)
                    if isinstance(parsed, list):
                        mem_keywords = [str(k).strip() for k in parsed if str(k).strip()]
                    else:
                        mem_keywords = []
                    # logger.info(f"mem_keywords: {mem_keywords}")
                    # logger.info(f"keywords_list: {keywords_list}")
                    for kw in keywords_list:
                        # logger.info(f"kw: {kw}")
                        # logger.info(f"kw in mem_keywords: {kw in mem_keywords}")
                        if kw in mem_keywords:
                            # logger.info(f"mem.memory_text: {mem.memory_text}")
                            memories_set.add(mem.memory_text)
                            break
                return list(memories_set)
            except Exception as parse_e:
                logger.error(f"解析记忆json失败：{str(parse_e)} {traceback.format_exc()}")
                return None
        except Exception as e:
            logger.error(f"获取记忆出现错误：{str(e)} {traceback.format_exc()}")
            return None

    def _parse_time_range(self, time_str):
        # sourcery skip: extract-duplicate-method, use-contextlib-suppress
        """
        支持解析如下格式：
        - 具体日期时间：YYYY-MM-DD HH:MM:SS
        - 具体日期：YYYY-MM-DD
        - 相对时间：今天，昨天，前天，N天前，N个月前
        - 空字符串：返回(None, None)
        """
        now = datetime.now()
        if not time_str:
            return 0, now
        time_str = time_str.strip()
        # 具体日期时间
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            return dt, dt + timedelta(hours=1)
        except Exception:
            pass
        # 具体日期
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d")
            return dt, dt + timedelta(days=1)
        except Exception:
            pass
        # 相对时间
        if time_str == "今天":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end
        if time_str == "昨天":
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end
        if time_str == "前天":
            start = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end
        if m := re.match(r"(\d+)天前", time_str):
            days = int(m.group(1))
            start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end
        if m := re.match(r"(\d+)个月前", time_str):
            months = int(m.group(1))
            # 近似每月30天
            start = (now - timedelta(days=months * 30)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end
        # 其他无法解析
        return 0, now
