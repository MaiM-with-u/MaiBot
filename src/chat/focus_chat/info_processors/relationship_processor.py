from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.heart_flow.observation.observation import Observation
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
import time
import traceback
from src.common.logger import get_logger
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.person_info.relationship_manager import get_relationship_manager
from .base_processor import BaseProcessor
from typing import List
from typing import Dict
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info.relation_info import RelationInfo
from json_repair import repair_json
from src.person_info.person_info import get_person_info_manager
import json
import asyncio
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_with_chat


# 配置常量：是否启用小模型即时信息提取
# 开启时：使用小模型并行即时提取，速度更快，但精度可能略低
# 关闭时：使用原来的异步模式，精度更高但速度较慢
ENABLE_INSTANT_INFO_EXTRACTION = True

logger = get_logger("processor")


def init_prompt():
    relationship_prompt = """
<聊天记录>
{chat_observe_info}
</聊天记录>

<调取记录>
{info_cache_block}
</调取记录>

{name_block}
请你阅读聊天记录，查看是否需要调取某个人的信息，这个人可以是出现在聊天记录中的，也可以是记录中提到的人。
你不同程度上认识群聊里的人，以及他们谈论到的人，你可以根据聊天记录，回忆起有关他们的信息，帮助你参与聊天
1.你需要提供用户名，以及你想要提取的信息名称类型来进行调取
2.你也可以完全不输出任何信息
3.阅读调取记录，如果已经回忆过某个人的信息，请不要重复调取，除非你忘记了

请以json格式输出，例如：

{{
    "用户A": "昵称",
    "用户A": "性别",
    "用户B": "对你的态度",
    "用户C": "你和ta最近做的事",
    "用户D": "你对ta的印象",
}}


请严格按照以下输出格式，不要输出多余内容，person_name可以有多个：
{{
    "person_name": "信息名称",
    "person_name": "信息名称",
}}

"""
    Prompt(relationship_prompt, "relationship_prompt")

    fetch_info_prompt = """
    
{name_block}
以下是你对{person_name}的了解，请你从中提取用户的有关"{info_type}"的信息，如果用户没有相关信息，请输出none：
<对{person_name}的总体了解>
{person_impression}
</对{person_name}的总体了解>

<你记得{person_name}最近的事>
{points_text}
</你记得{person_name}最近的事>

请严格按照以下json输出格式，不要输出多余内容：
{{
    {info_json_str}
}}
"""
    Prompt(fetch_info_prompt, "fetch_info_prompt")


class RelationshipProcessor(BaseProcessor):
    log_prefix = "关系"

    def __init__(self, subheartflow_id: str):
        super().__init__()

        self.subheartflow_id = subheartflow_id
        self.info_fetching_cache: List[Dict[str, any]] = []
        self.info_fetched_cache: Dict[
            str, Dict[str, any]
        ] = {}  # {person_id: {"info": str, "ttl": int, "start_time": float}}
        self.person_engaged_cache: List[Dict[str, any]] = []  # [{person_id: str, start_time: float, rounds: int}]
        self.grace_period_rounds = 5

        self.llm_model = LLMRequest(
            model=global_config.model.relation,
            request_type="focus.relationship",
        )

        # 小模型用于即时信息提取
        if ENABLE_INSTANT_INFO_EXTRACTION:
            self.instant_llm_model = LLMRequest(
                model=global_config.model.utils_small,
                request_type="focus.relationship.instant",
            )

        name = get_chat_manager().get_stream_name(self.subheartflow_id)
        self.log_prefix = f"[{name}] "

    async def process_info(self, observations: List[Observation] = None, *infos) -> List[InfoBase]:
        """处理信息对象

        Args:
            *infos: 可变数量的InfoBase类型的信息对象

        Returns:
            List[InfoBase]: 处理后的结构化信息列表
        """
        relation_info_str = await self.relation_identify(observations)

        if relation_info_str:
            relation_info = RelationInfo()
            relation_info.set_relation_info(relation_info_str)
        else:
            relation_info = None
            return None

        return [relation_info]

    async def relation_identify(
        self,
        observations: List[Observation] = None,
    ):
        """
        在回复前进行思考，生成内心想法并收集工具调用结果
        """
        # 0. 从观察信息中提取所需数据
        # 需要兼容私聊

        chat_observe_info = ""
        current_time = time.time()
        if observations:
            for observation in observations:
                if isinstance(observation, ChattingObservation):
                    chat_observe_info = observation.get_observe_info()
                    break

        # 1. 处理person_engaged_cache
        for record in list(self.person_engaged_cache):
            record["rounds"] += 1
            time_elapsed = current_time - record["start_time"]
            message_count = len(
                get_raw_msg_by_timestamp_with_chat(self.subheartflow_id, record["start_time"], current_time)
            )

            print(record)

            # 根据消息数量和时间设置不同的触发条件
            should_trigger = (
                message_count >= 50  # 50条消息必定满足
                or (message_count >= 35 and time_elapsed >= 300)  # 35条且10分钟
                or (message_count >= 25 and time_elapsed >= 900)  # 25条且30分钟
                or (message_count >= 10 and time_elapsed >= 2000)  # 10条且1小时
            )

            if should_trigger:
                logger.info(
                    f"{self.log_prefix} 用户 {record['person_id']} 满足关系构建条件，开始构建关系。消息数：{message_count}，时长：{time_elapsed:.0f}秒"
                )
                asyncio.create_task(
                    self.update_impression_on_cache_expiry(
                        record["person_id"], self.subheartflow_id, record["start_time"], current_time
                    )
                )
                self.person_engaged_cache.remove(record)

        # 2. 减少info_fetched_cache中所有信息的TTL
        for person_id in list(self.info_fetched_cache.keys()):
            for info_type in list(self.info_fetched_cache[person_id].keys()):
                self.info_fetched_cache[person_id][info_type]["ttl"] -= 1
                if self.info_fetched_cache[person_id][info_type]["ttl"] <= 0:
                    # 在删除前查找匹配的info_fetching_cache记录
                    matched_record = None
                    min_time_diff = float("inf")
                    for record in self.info_fetching_cache:
                        if (
                            record["person_id"] == person_id
                            and record["info_type"] == info_type
                            and not record["forget"]
                        ):
                            time_diff = abs(
                                record["start_time"] - self.info_fetched_cache[person_id][info_type]["start_time"]
                            )
                            if time_diff < min_time_diff:
                                min_time_diff = time_diff
                                matched_record = record

                    if matched_record:
                        matched_record["forget"] = True
                        logger.info(f"{self.log_prefix} 用户 {person_id} 的 {info_type} 信息已过期，标记为遗忘。")

                    del self.info_fetched_cache[person_id][info_type]
            if not self.info_fetched_cache[person_id]:
                del self.info_fetched_cache[person_id]

        # 5. 为需要处理的人员准备LLM prompt
        nickname_str = ",".join(global_config.bot.alias_names)
        name_block = f"你的名字是{global_config.bot.nickname},你的昵称有{nickname_str}，有人也会用这些昵称称呼你。"

        info_cache_block = ""
        if self.info_fetching_cache:
            for info_fetching in self.info_fetching_cache:
                if info_fetching["forget"]:
                    info_cache_block += f"在{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info_fetching['start_time']))}，你回忆了[{info_fetching['person_name']}]的[{info_fetching['info_type']}]，但是现在你忘记了\n"
                else:
                    info_cache_block += f"在{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info_fetching['start_time']))}，你回忆了[{info_fetching['person_name']}]的[{info_fetching['info_type']}]，还记着呢\n"

        prompt = (await global_prompt_manager.get_prompt_async("relationship_prompt")).format(
            name_block=name_block,
            time_now=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            chat_observe_info=chat_observe_info,
            info_cache_block=info_cache_block,
        )

        try:
            logger.debug(f"{self.log_prefix} 人物信息prompt: \n{prompt}\n")
            content, _ = await self.llm_model.generate_response_async(prompt=prompt)
            if content:
                print(f"content: {content}")
                content_json = json.loads(repair_json(content))

                # 收集即时提取任务
                instant_tasks = []
                async_tasks = []

                person_info_manager = get_person_info_manager()
                for person_name, info_type in content_json.items():
                    person_id = person_info_manager.get_person_id_by_person_name(person_name)
                    if person_id:
                        self.info_fetching_cache.append(
                            {
                                "person_id": person_id,
                                "person_name": person_name,
                                "info_type": info_type,
                                "start_time": time.time(),
                                "forget": False,
                            }
                        )
                        if len(self.info_fetching_cache) > 20:
                            self.info_fetching_cache.pop(0)
                    else:
                        logger.warning(f"{self.log_prefix} 未找到用户 {person_name} 的ID，跳过调取信息。")
                        continue

                    logger.info(f"{self.log_prefix} 调取用户 {person_name} 的 {info_type} 信息。")

                    # 检查person_engaged_cache中是否已存在该person_id
                    person_exists = any(record["person_id"] == person_id for record in self.person_engaged_cache)
                    if not person_exists:
                        self.person_engaged_cache.append(
                            {"person_id": person_id, "start_time": time.time(), "rounds": 0}
                        )

                    if ENABLE_INSTANT_INFO_EXTRACTION:
                        # 收集即时提取任务
                        instant_tasks.append((person_id, info_type, time.time()))
                    else:
                        # 使用原来的异步模式
                        async_tasks.append(
                            asyncio.create_task(self.fetch_person_info(person_id, [info_type], start_time=time.time()))
                        )

                # 执行即时提取任务
                if ENABLE_INSTANT_INFO_EXTRACTION and instant_tasks:
                    await self._execute_instant_extraction_batch(instant_tasks)

                # 启动异步任务（如果不是即时模式）
                if async_tasks:
                    # 异步任务不需要等待完成
                    pass

            else:
                logger.warning(f"{self.log_prefix} LLM返回空结果，关系识别失败。")

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行LLM请求或处理响应时出错: {e}")
            logger.error(traceback.format_exc())

        # 7. 合并缓存和新处理的信息
        persons_infos_str = ""
        # 处理已获取到的信息
        if self.info_fetched_cache:
            for person_id in self.info_fetched_cache:
                person_infos_str = ""
                for info_type in self.info_fetched_cache[person_id]:
                    person_name = self.info_fetched_cache[person_id][info_type]["person_name"]
                    if not self.info_fetched_cache[person_id][info_type]["unknow"]:
                        info_content = self.info_fetched_cache[person_id][info_type]["info"]
                        person_infos_str += f"[{info_type}]：{info_content}；"
                    else:
                        person_infos_str += f"你不了解{person_name}有关[{info_type}]的信息，不要胡乱回答，你可以直接说你不知道，或者你忘记了；"
                if person_infos_str:
                    persons_infos_str += f"你对 {person_name} 的了解：{person_infos_str}\n"

        # 处理正在调取但还没有结果的项目（只在非即时提取模式下显示）
        if not ENABLE_INSTANT_INFO_EXTRACTION:
            pending_info_dict = {}
            for record in self.info_fetching_cache:
                if not record["forget"]:
                    current_time = time.time()
                    # 只处理不超过2分钟的调取请求，避免过期请求一直显示
                    if current_time - record["start_time"] <= 120:  # 10分钟内的请求
                        person_id = record["person_id"]
                        person_name = record["person_name"]
                        info_type = record["info_type"]

                        # 检查是否已经在info_fetched_cache中有结果
                        if person_id in self.info_fetched_cache and info_type in self.info_fetched_cache[person_id]:
                            continue

                        # 按人物组织正在调取的信息
                        if person_name not in pending_info_dict:
                            pending_info_dict[person_name] = []
                        pending_info_dict[person_name].append(info_type)

            # 添加正在调取的信息到返回字符串
            for person_name, info_types in pending_info_dict.items():
                info_types_str = "、".join(info_types)
                persons_infos_str += f"你正在识图回忆有关 {person_name} 的 {info_types_str} 信息，稍等一下再回答...\n"

        return persons_infos_str

    async def _execute_instant_extraction_batch(self, instant_tasks: list):
        """
        批量执行即时提取任务
        """
        if not instant_tasks:
            return

        logger.info(f"{self.log_prefix} [即时提取] 开始批量提取 {len(instant_tasks)} 个信息")

        # 创建所有提取任务
        extraction_tasks = []
        for person_id, info_type, start_time in instant_tasks:
            # 检查缓存中是否已存在且未过期的信息
            if person_id in self.info_fetched_cache and info_type in self.info_fetched_cache[person_id]:
                logger.info(f"{self.log_prefix} 用户 {person_id} 的 {info_type} 信息已存在且未过期，跳过调取。")
                continue

            task = asyncio.create_task(self._fetch_single_info_instant(person_id, info_type, start_time))
            extraction_tasks.append(task)

        # 并行执行所有提取任务并等待完成
        if extraction_tasks:
            await asyncio.gather(*extraction_tasks, return_exceptions=True)
            logger.info(f"{self.log_prefix} [即时提取] 批量提取完成")

    async def _fetch_single_info_instant(self, person_id: str, info_type: str, start_time: float):
        """
        使用小模型提取单个信息类型
        """
        person_info_manager = get_person_info_manager()
        nickname_str = ",".join(global_config.bot.alias_names)
        name_block = f"你的名字是{global_config.bot.nickname},你的昵称有{nickname_str}，有人也会用这些昵称称呼你。"

        person_name = await person_info_manager.get_value(person_id, "person_name")

        person_impression = await person_info_manager.get_value(person_id, "impression")
        if not person_impression:
            impression_block = "你对ta没有什么深刻的印象"
        else:
            impression_block = f"{person_impression}"

        points = await person_info_manager.get_value(person_id, "points")
        if points:
            points_text = "\n".join([f"{point[2]}:{point[0]}" for point in points])
        else:
            points_text = "你不记得ta最近发生了什么"

        prompt = (await global_prompt_manager.get_prompt_async("fetch_info_prompt")).format(
            name_block=name_block,
            info_type=info_type,
            person_impression=impression_block,
            person_name=person_name,
            info_json_str=f'"{info_type}": "信息内容"',
            points_text=points_text,
        )

        try:
            # 使用小模型进行即时提取
            content, _ = await self.instant_llm_model.generate_response_async(prompt=prompt)

            logger.info(f"{self.log_prefix} [即时提取] {person_name} 的 {info_type} 结果: {content}")

            if content:
                content_json = json.loads(repair_json(content))
                if info_type in content_json:
                    info_content = content_json[info_type]
                    if info_content != "none" and info_content:
                        if person_id not in self.info_fetched_cache:
                            self.info_fetched_cache[person_id] = {}
                        self.info_fetched_cache[person_id][info_type] = {
                            "info": info_content,
                            "ttl": 8,  # 小模型提取的信息TTL稍短
                            "start_time": start_time,
                            "person_name": person_name,
                            "unknow": False,
                        }
                        logger.info(
                            f"{self.log_prefix} [即时提取] 成功获取 {person_name} 的 {info_type}: {info_content}"
                        )
                    else:
                        if person_id not in self.info_fetched_cache:
                            self.info_fetched_cache[person_id] = {}
                        self.info_fetched_cache[person_id][info_type] = {
                            "info": "unknow",
                            "ttl": 8,
                            "start_time": start_time,
                            "person_name": person_name,
                            "unknow": True,
                        }
                        logger.info(f"{self.log_prefix} [即时提取] {person_name} 的 {info_type} 信息不明确")
            else:
                logger.warning(
                    f"{self.log_prefix} [即时提取] 小模型返回空结果，获取 {person_name} 的 {info_type} 信息失败。"
                )
        except Exception as e:
            logger.error(f"{self.log_prefix} [即时提取] 执行小模型请求获取用户信息时出错: {e}")
            logger.error(traceback.format_exc())

    async def fetch_person_info(self, person_id: str, info_types: list[str], start_time: float):
        """
        获取某个人的信息
        """
        # 检查缓存中是否已存在且未过期的信息
        info_types_to_fetch = []

        for info_type in info_types:
            if person_id in self.info_fetched_cache and info_type in self.info_fetched_cache[person_id]:
                logger.info(f"{self.log_prefix} 用户 {person_id} 的 {info_type} 信息已存在且未过期，跳过调取。")
                continue
            info_types_to_fetch.append(info_type)

        if not info_types_to_fetch:
            return

        nickname_str = ",".join(global_config.bot.alias_names)
        name_block = f"你的名字是{global_config.bot.nickname},你的昵称有{nickname_str}，有人也会用这些昵称称呼你。"

        person_info_manager = get_person_info_manager()
        person_name = await person_info_manager.get_value(person_id, "person_name")

        info_type_str = ""
        info_json_str = ""
        for info_type in info_types_to_fetch:
            info_type_str += f"{info_type},"
            info_json_str += f'"{info_type}": "信息内容",'
        info_type_str = info_type_str[:-1]
        info_json_str = info_json_str[:-1]

        person_impression = await person_info_manager.get_value(person_id, "impression")
        if not person_impression:
            impression_block = "你对ta没有什么深刻的印象"
        else:
            impression_block = f"{person_impression}"

        points = await person_info_manager.get_value(person_id, "points")

        if points:
            points_text = "\n".join([f"{point[2]}:{point[0]}" for point in points])
        else:
            points_text = "你不记得ta最近发生了什么"

        prompt = (await global_prompt_manager.get_prompt_async("fetch_info_prompt")).format(
            name_block=name_block,
            info_type=info_type_str,
            person_impression=impression_block,
            person_name=person_name,
            info_json_str=info_json_str,
            points_text=points_text,
        )

        try:
            content, _ = await self.llm_model.generate_response_async(prompt=prompt)

            # logger.info(f"{self.log_prefix} fetch_person_info prompt: \n{prompt}\n")
            logger.info(f"{self.log_prefix} fetch_person_info 结果: {content}")

            if content:
                try:
                    content_json = json.loads(repair_json(content))
                    for info_type, info_content in content_json.items():
                        if info_content != "none" and info_content:
                            if person_id not in self.info_fetched_cache:
                                self.info_fetched_cache[person_id] = {}
                            self.info_fetched_cache[person_id][info_type] = {
                                "info": info_content,
                                "ttl": 10,
                                "start_time": start_time,
                                "person_name": person_name,
                                "unknow": False,
                            }
                        else:
                            if person_id not in self.info_fetched_cache:
                                self.info_fetched_cache[person_id] = {}

                            self.info_fetched_cache[person_id][info_type] = {
                                "info": "unknow",
                                "ttl": 10,
                                "start_time": start_time,
                                "person_name": person_name,
                                "unknow": True,
                            }
                except Exception as e:
                    logger.error(f"{self.log_prefix} 解析LLM返回的信息时出错: {e}")
                    logger.error(traceback.format_exc())
            else:
                logger.warning(f"{self.log_prefix} LLM返回空结果，获取用户 {person_name} 的 {info_type_str} 信息失败。")
        except Exception as e:
            logger.error(f"{self.log_prefix} 执行LLM请求获取用户信息时出错: {e}")
            logger.error(traceback.format_exc())

    async def update_impression_on_cache_expiry(self, person_id: str, chat_id: str, start_time: float, end_time: float):
        """
        在缓存过期时，获取聊天记录并更新用户印象
        """
        logger.info(f"缓存过期，开始为 {person_id} 更新印象。时间范围：{start_time} -> {end_time}")
        try:
            impression_messages = get_raw_msg_by_timestamp_with_chat(chat_id, start_time, end_time)
            if impression_messages:
                logger.info(f"为 {person_id} 获取到 {len(impression_messages)} 条消息用于印象更新。")
                relationship_manager = get_relationship_manager()
                await relationship_manager.update_person_impression(
                    person_id=person_id, timestamp=end_time, bot_engaged_messages=impression_messages
                )
            else:
                logger.info(f"在指定时间范围内没有找到 {person_id} 的消息，不更新印象。")

        except Exception as e:
            logger.error(f"为 {person_id} 更新印象时发生错误: {e}")
            logger.error(traceback.format_exc())


init_prompt()
