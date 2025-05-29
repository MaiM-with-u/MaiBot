from typing import Dict, Any, Type, TypeVar, List, Optional
import traceback
from json_repair import repair_json
from rich.traceback import install
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.focus_chat.working_memory.memory_item import MemoryItem
import json  # 添加json模块导入


install(extra_lines=3)
logger = get_logger("working_memory")

T = TypeVar("T")


class MemoryManager:
    def __init__(self, chat_id: str):
        """
        初始化工作记忆

        Args:
            chat_id: 关联的聊天ID，用于标识该工作记忆属于哪个聊天
        """
        # 关联的聊天ID
        self._chat_id = chat_id

        # 主存储: 数据类型 -> 记忆项列表
        self._memory: Dict[Type, List[MemoryItem]] = {}

        # ID到记忆项的映射
        self._id_map: Dict[str, MemoryItem] = {}

        self.llm_summarizer = LLMRequest(
            model=global_config.model.focus_working_memory,
            temperature=0.3,
            max_tokens=512,
            request_type="focus.processor.working_memory",
        )

    @property
    def chat_id(self) -> str:
        """获取关联的聊天ID"""
        return self._chat_id

    @chat_id.setter
    def chat_id(self, value: str):
        """设置关联的聊天ID"""
        self._chat_id = value

    def push_item(self, memory_item: MemoryItem) -> str:
        """
        推送一个已创建的记忆项到工作记忆中

        Args:
            memory_item: 要存储的记忆项

        Returns:
            记忆项的ID
        """
        data_type = memory_item.data_type

        # 确保存在该类型的存储列表
        if data_type not in self._memory:
            self._memory[data_type] = []

        # 添加到内存和ID映射
        self._memory[data_type].append(memory_item)
        self._id_map[memory_item.id] = memory_item

        return memory_item.id

    async def push_with_summary(self, data: T, from_source: str = "", tags: Optional[List[str]] = None) -> MemoryItem:
        """
        推送一段有类型的信息到工作记忆中，并自动生成总结

        Args:
            data: 要存储的数据
            from_source: 数据来源
            tags: 数据标签列表

        Returns:
            包含原始数据和总结信息的字典
        """
        # 如果数据是字符串类型，则先进行总结
        if isinstance(data, str):
            # 先生成总结
            summary = await self.summarize_memory_item(data)

            # 准备标签
            memory_tags = list(tags) if tags else []

            # 创建记忆项
            memory_item = MemoryItem(data, from_source, memory_tags)

            # 将总结信息保存到记忆项中
            memory_item.set_summary(summary)

            # 推送记忆项
            self.push_item(memory_item)

            return memory_item
        else:
            # 非字符串类型，直接创建并推送记忆项
            memory_item = MemoryItem(data, from_source, tags)
            self.push_item(memory_item)

            return memory_item

    def get_by_id(self, memory_id: str) -> Optional[MemoryItem]:
        """
        通过ID获取记忆项

        Args:
            memory_id: 记忆项ID

        Returns:
            找到的记忆项，如果不存在则返回None
        """
        memory_item = self._id_map.get(memory_id)
        if memory_item:
            # 检查记忆强度，如果小于1则删除
            if not memory_item.is_memory_valid():
                print(f"记忆 {memory_id} 强度过低 ({memory_item.memory_strength})，已自动移除")
                self.delete(memory_id)
                return None

        return memory_item

    def get_all_items(self) -> List[MemoryItem]:
        """获取所有记忆项"""
        return list(self._id_map.values())

    def find_items(
        self,
        data_type: Optional[Type] = None,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        memory_id: Optional[str] = None,
        limit: Optional[int] = None,
        newest_first: bool = False,
        min_strength: float = 0.0,
    ) -> List[MemoryItem]:
        """
        按条件查找记忆项

        Args:
            data_type: 要查找的数据类型
            source: 数据来源
            tags: 必须包含的标签列表
            start_time: 开始时间戳
            end_time: 结束时间戳
            memory_id: 特定记忆项ID
            limit: 返回结果的最大数量
            newest_first: 是否按最新优先排序
            min_strength: 最小记忆强度

        Returns:
            符合条件的记忆项列表
        """
        # 如果提供了特定ID，直接查找
        if memory_id:
            item = self.get_by_id(memory_id)
            return [item] if item else []

        results = []

        # 确定要搜索的类型列表
        types_to_search = [data_type] if data_type else list(self._memory.keys())

        # 对每个类型进行搜索
        for typ in types_to_search:
            if typ not in self._memory:
                continue

            # 获取该类型的所有项目
            items = self._memory[typ]

            # 如果需要最新优先，则反转遍历顺序
            if newest_first:
                items_to_check = list(reversed(items))
            else:
                items_to_check = items

            # 遍历项目
            for item in items_to_check:
                # 检查来源是否匹配
                if source is not None and not item.matches_source(source):
                    continue

                # 检查标签是否匹配
                if tags is not None and not item.has_all_tags(tags):
                    continue

                # 检查时间范围
                if start_time is not None and item.timestamp < start_time:
                    continue
                if end_time is not None and item.timestamp > end_time:
                    continue

                # 检查记忆强度
                if min_strength > 0 and item.memory_strength < min_strength:
                    continue

                # 所有条件都满足，添加到结果中
                results.append(item)

                # 如果达到限制数量，提前返回
                if limit is not None and len(results) >= limit:
                    return results

        return results

    async def summarize_memory_item(self, content: str) -> Dict[str, Any]:
        """
        使用LLM总结记忆项

        Args:
            content: 需要总结的内容

        Returns:
            包含总结、概括、关键概念和事件的字典
        """
        prompt = f"""请对以下内容进行总结，总结成记忆，输出四部分：
1. 记忆内容主题（精简，20字以内）：让用户可以一眼看出记忆内容是什么
2. 记忆内容概括（200字以内）：让用户可以了解记忆内容的大致内容
3. 关键概念和知识（keypoints）：多条，提取关键的概念、知识点和关键词，要包含对概念的解释
4. 事件描述（events）：多条，描述谁（人物）在什么时候（时间）做了什么（事件）

内容：
{content}

请按以下JSON格式输出：
```json
{{
  "brief": "记忆内容主题（20字以内）",
  "detailed": "记忆内容概括（200字以内）",
  "keypoints": [
    "概念1：解释",
    "概念2：解释",
    ...
  ],
  "events": [
    "事件1：谁在什么时候做了什么",
    "事件2：谁在什么时候做了什么",
    ...
  ]
}}
```
请确保输出是有效的JSON格式，不要添加任何额外的说明或解释。
"""
        default_summary = {
            "brief": "主题未知的记忆",
            "detailed": "大致内容未知的记忆",
            "keypoints": ["未知的概念"],
            "events": ["未知的事件"],
        }

        try:
            # 调用LLM生成总结
            response, _ = await self.llm_summarizer.generate_response_async(prompt)

            # 使用repair_json解析响应
            try:
                # 使用repair_json修复JSON格式
                fixed_json_string = repair_json(response)

                # 如果repair_json返回的是字符串，需要解析为Python对象
                if isinstance(fixed_json_string, str):
                    try:
                        json_result = json.loads(fixed_json_string)
                    except json.JSONDecodeError as decode_error:
                        logger.error(f"JSON解析错误: {str(decode_error)}")
                        return default_summary
                else:
                    # 如果repair_json直接返回了字典对象，直接使用
                    json_result = fixed_json_string

                # 进行额外的类型检查
                if not isinstance(json_result, dict):
                    logger.error(f"修复后的JSON不是字典类型: {type(json_result)}")
                    return default_summary

                # 确保所有必要字段都存在且类型正确
                if "brief" not in json_result or not isinstance(json_result["brief"], str):
                    json_result["brief"] = "主题未知的记忆"

                if "detailed" not in json_result or not isinstance(json_result["detailed"], str):
                    json_result["detailed"] = "大致内容未知的记忆"

                # 处理关键概念
                if "keypoints" not in json_result or not isinstance(json_result["keypoints"], list):
                    json_result["keypoints"] = ["未知的概念"]
                else:
                    # 确保keypoints中的每个项目都是字符串
                    json_result["keypoints"] = [str(point) for point in json_result["keypoints"] if point is not None]
                    if not json_result["keypoints"]:
                        json_result["keypoints"] = ["未知的概念"]

                # 处理事件
                if "events" not in json_result or not isinstance(json_result["events"], list):
                    json_result["events"] = ["未知的事件"]
                else:
                    # 确保events中的每个项目都是字符串
                    json_result["events"] = [str(event) for event in json_result["events"] if event is not None]
                    if not json_result["events"]:
                        json_result["events"] = ["未知的事件"]

                # 兼容旧版，将keypoints和events合并到key_points中
                json_result["key_points"] = json_result["keypoints"] + json_result["events"]

                return json_result

            except Exception as json_error:
                logger.error(f"JSON处理失败: {str(json_error)}，将使用默认摘要")
                # 返回默认结构
                return default_summary

        except Exception as e:
            # 出错时返回简单的结构
            logger.error(f"生成总结时出错: {str(e)}")
            return default_summary

    async def refine_memory(self, memory_id: str, requirements: str = "") -> Dict[str, Any]:
        """
        对记忆进行精简操作，根据要求修改要点、总结和概括

        Args:
            memory_id: 记忆ID
            requirements: 精简要求，描述如何修改记忆，包括可能需要移除的要点

        Returns:
            修改后的记忆总结字典
        """
        # 获取指定ID的记忆项
        logger.info(f"精简记忆: {memory_id}")
        memory_item = self.get_by_id(memory_id)
        if not memory_item:
            raise ValueError(f"未找到ID为{memory_id}的记忆项")

        # 增加精简次数
        memory_item.increase_compress_count()

        summary = memory_item.summary

        # 使用LLM根据要求对总结、概括和要点进行精简修改
        prompt = f"""
请根据以下要求，对记忆内容的主题、概括、关键概念和事件进行精简，模拟记忆的遗忘过程：
要求：{requirements}
你可以随机对关键概念和事件进行压缩，模糊或者丢弃，修改后，同样修改主题和概括

目前主题：{summary["brief"]}

目前概括：{summary["detailed"]}

目前关键概念：
{chr(10).join([f"- {point}" for point in summary.get("keypoints", [])])}

目前事件：
{chr(10).join([f"- {point}" for point in summary.get("events", [])])}

请生成修改后的主题、概括、关键概念和事件，遵循以下格式：
```json
{{
    "brief": "修改后的主题（20字以内）",
    "detailed": "修改后的概括（200字以内）",
    "keypoints": [
        "修改后的概念1：解释",
        "修改后的概念2：解释"
    ],
    "events": [
        "修改后的事件1：谁在什么时候做了什么",
        "修改后的事件2：谁在什么时候做了什么"
    ]
}}
```
请确保输出是有效的JSON格式，不要添加任何额外的说明或解释。
"""
        # 检查summary中是否有旧版结构，转换为新版结构
        if "keypoints" not in summary and "events" not in summary and "key_points" in summary:
            # 尝试区分key_points中的keypoints和events
            # 简单地将前半部分视为keypoints，后半部分视为events
            key_points = summary.get("key_points", [])
            halfway = len(key_points) // 2
            summary["keypoints"] = key_points[:halfway] or ["未知的概念"]
            summary["events"] = key_points[halfway:] or ["未知的事件"]

        # 定义默认的精简结果
        default_refined = {
            "brief": summary["brief"],
            "detailed": summary["detailed"],
            "keypoints": summary.get("keypoints", ["未知的概念"])[:1],  # 默认只保留第一个关键概念
            "events": summary.get("events", ["未知的事件"])[:1],  # 默认只保留第一个事件
        }

        try:
            # 调用LLM修改总结、概括和要点
            response, _ = await self.llm_summarizer.generate_response_async(prompt)
            logger.debug(f"精简记忆响应: {response}")
            # 使用repair_json处理响应
            try:
                # 修复JSON格式
                fixed_json_string = repair_json(response)

                # 将修复后的字符串解析为Python对象
                if isinstance(fixed_json_string, str):
                    try:
                        refined_data = json.loads(fixed_json_string)
                    except json.JSONDecodeError as decode_error:
                        logger.error(f"JSON解析错误: {str(decode_error)}")
                        refined_data = default_refined
                else:
                    # 如果repair_json直接返回了字典对象，直接使用
                    refined_data = fixed_json_string

                # 确保是字典类型
                if not isinstance(refined_data, dict):
                    logger.error(f"修复后的JSON不是字典类型: {type(refined_data)}")
                    refined_data = default_refined

                # 更新总结、概括
                summary["brief"] = refined_data.get("brief", "主题未知的记忆")
                summary["detailed"] = refined_data.get("detailed", "大致内容未知的记忆")

                # 更新关键概念
                keypoints = refined_data.get("keypoints", [])
                if isinstance(keypoints, list) and keypoints:
                    # 确保所有关键概念都是字符串
                    summary["keypoints"] = [str(point) for point in keypoints if point is not None]
                else:
                    # 如果keypoints不是列表或为空，使用默认值
                    summary["keypoints"] = ["主要概念已遗忘"]

                # 更新事件
                events = refined_data.get("events", [])
                if isinstance(events, list) and events:
                    # 确保所有事件都是字符串
                    summary["events"] = [str(event) for event in events if event is not None]
                else:
                    # 如果events不是列表或为空，使用默认值
                    summary["events"] = ["事件细节已遗忘"]

                # 兼容旧版，维护key_points
                summary["key_points"] = summary["keypoints"] + summary["events"]

            except Exception as e:
                logger.error(f"精简记忆出错: {str(e)}")
                traceback.print_exc()

                # 出错时使用简化的默认精简
                summary["brief"] = summary["brief"] + " (已简化)"
                summary["keypoints"] = summary.get("keypoints", ["未知的概念"])[:1]
                summary["events"] = summary.get("events", ["未知的事件"])[:1]
                summary["key_points"] = summary["keypoints"] + summary["events"]

        except Exception as e:
            logger.error(f"精简记忆调用LLM出错: {str(e)}")
            traceback.print_exc()

        # 更新原记忆项的总结
        memory_item.set_summary(summary)

        return memory_item

    def decay_memory(self, memory_id: str, decay_factor: float = 0.8) -> bool:
        """
        使单个记忆衰减

        Args:
            memory_id: 记忆ID
            decay_factor: 衰减因子(0-1之间)

        Returns:
            是否成功衰减
        """
        memory_item = self.get_by_id(memory_id)
        if not memory_item:
            return False

        # 计算衰减量（当前强度 * (1-衰减因子)）
        old_strength = memory_item.memory_strength
        decay_amount = old_strength * (1 - decay_factor)

        # 更新强度
        memory_item.memory_strength = decay_amount

        return True

    def delete(self, memory_id: str) -> bool:
        """
        删除指定ID的记忆项

        Args:
            memory_id: 要删除的记忆项ID

        Returns:
            是否成功删除
        """
        if memory_id not in self._id_map:
            return False

        # 获取要删除的项
        item = self._id_map[memory_id]

        # 从内存中删除
        data_type = item.data_type
        if data_type in self._memory:
            self._memory[data_type] = [i for i in self._memory[data_type] if i.id != memory_id]

        # 从ID映射中删除
        del self._id_map[memory_id]

        return True

    def clear(self, data_type: Optional[Type] = None) -> None:
        """
        清除记忆中的数据

        Args:
            data_type: 要清除的数据类型，如果为None则清除所有数据
        """
        if data_type is None:
            # 清除所有数据
            self._memory.clear()
            self._id_map.clear()
        elif data_type in self._memory:
            # 清除指定类型的数据
            for item in self._memory[data_type]:
                if item.id in self._id_map:
                    del self._id_map[item.id]
            del self._memory[data_type]

    async def merge_memories(
        self, memory_id1: str, memory_id2: str, reason: str, delete_originals: bool = True
    ) -> MemoryItem:
        """
        合并两个记忆项

        Args:
            memory_id1: 第一个记忆项ID
            memory_id2: 第二个记忆项ID
            reason: 合并原因
            delete_originals: 是否删除原始记忆，默认为True

        Returns:
            包含合并后的记忆信息的字典
        """
        # 获取两个记忆项
        memory_item1 = self.get_by_id(memory_id1)
        memory_item2 = self.get_by_id(memory_id2)

        if not memory_item1 or not memory_item2:
            raise ValueError("无法找到指定的记忆项")

        content1 = memory_item1.data
        content2 = memory_item2.data

        # 获取记忆的摘要信息（如果有）
        summary1 = memory_item1.summary
        summary2 = memory_item2.summary

        # 构建合并提示
        prompt = f"""
请根据以下原因，将两段记忆内容有机合并成一段新的记忆内容。
合并时保留两段记忆的重要信息，避免重复，确保生成的内容连贯、自然。

合并原因：{reason}
"""

        # 如果有摘要信息，添加到提示中
        if summary1:
            prompt += f"记忆1主题：{summary1['brief']}\n"
            prompt += f"记忆1概括：{summary1['detailed']}\n"

            if "keypoints" in summary1:
                prompt += "记忆1关键概念：\n" + "\n".join([f"- {point}" for point in summary1["keypoints"]]) + "\n\n"

            if "events" in summary1:
                prompt += "记忆1事件：\n" + "\n".join([f"- {point}" for point in summary1["events"]]) + "\n\n"
            elif "key_points" in summary1:
                prompt += "记忆1要点：\n" + "\n".join([f"- {point}" for point in summary1["key_points"]]) + "\n\n"

        if summary2:
            prompt += f"记忆2主题：{summary2['brief']}\n"
            prompt += f"记忆2概括：{summary2['detailed']}\n"

            if "keypoints" in summary2:
                prompt += "记忆2关键概念：\n" + "\n".join([f"- {point}" for point in summary2["keypoints"]]) + "\n\n"

            if "events" in summary2:
                prompt += "记忆2事件：\n" + "\n".join([f"- {point}" for point in summary2["events"]]) + "\n\n"
            elif "key_points" in summary2:
                prompt += "记忆2要点：\n" + "\n".join([f"- {point}" for point in summary2["key_points"]]) + "\n\n"

        # 添加记忆原始内容
        prompt += f"""
记忆1原始内容：
{content1}

记忆2原始内容：
{content2}

请按以下JSON格式输出合并结果：
```json
{{
    "content": "合并后的记忆内容文本（尽可能保留原信息，但去除重复）",
    "brief": "合并后的主题（20字以内）",
    "detailed": "合并后的概括（200字以内）",
    "keypoints": [
        "合并后的概念1：解释",
        "合并后的概念2：解释",
        "合并后的概念3：解释"
    ],
    "events": [
        "合并后的事件1：谁在什么时候做了什么",
        "合并后的事件2：谁在什么时候做了什么"
    ]
}}
```
请确保输出是有效的JSON格式，不要添加任何额外的说明或解释。
"""

        # 默认合并结果
        default_merged = {
            "content": f"{content1}\n\n{content2}",
            "brief": f"合并：{summary1['brief']} + {summary2['brief']}",
            "detailed": f"合并了两个记忆：{summary1['detailed']} 以及 {summary2['detailed']}",
            "keypoints": [],
            "events": [],
        }

        # 合并旧版key_points
        if "key_points" in summary1:
            default_merged["keypoints"].extend(summary1.get("keypoints", []))
            default_merged["events"].extend(summary1.get("events", []))
            # 如果没有新的结构，尝试从旧结构分离
            if not default_merged["keypoints"] and not default_merged["events"] and "key_points" in summary1:
                key_points = summary1["key_points"]
                halfway = len(key_points) // 2
                default_merged["keypoints"].extend(key_points[:halfway])
                default_merged["events"].extend(key_points[halfway:])

        if "key_points" in summary2:
            default_merged["keypoints"].extend(summary2.get("keypoints", []))
            default_merged["events"].extend(summary2.get("events", []))
            # 如果没有新的结构，尝试从旧结构分离
            if not default_merged["keypoints"] and not default_merged["events"] and "key_points" in summary2:
                key_points = summary2["key_points"]
                halfway = len(key_points) // 2
                default_merged["keypoints"].extend(key_points[:halfway])
                default_merged["events"].extend(key_points[halfway:])

        # 确保列表不为空
        if not default_merged["keypoints"]:
            default_merged["keypoints"] = ["合并的关键概念"]
        if not default_merged["events"]:
            default_merged["events"] = ["合并的事件"]

        # 添加key_points兼容
        default_merged["key_points"] = default_merged["keypoints"] + default_merged["events"]

        try:
            # 调用LLM合并记忆
            response, _ = await self.llm_summarizer.generate_response_async(prompt)

            # 处理LLM返回的合并结果
            try:
                # 修复JSON格式
                fixed_json_string = repair_json(response)

                # 将修复后的字符串解析为Python对象
                if isinstance(fixed_json_string, str):
                    try:
                        merged_data = json.loads(fixed_json_string)
                    except json.JSONDecodeError as decode_error:
                        logger.error(f"JSON解析错误: {str(decode_error)}")
                        merged_data = default_merged
                else:
                    # 如果repair_json直接返回了字典对象，直接使用
                    merged_data = fixed_json_string

                # 确保是字典类型
                if not isinstance(merged_data, dict):
                    logger.error(f"修复后的JSON不是字典类型: {type(merged_data)}")
                    merged_data = default_merged

                # 确保所有必要字段都存在且类型正确
                if "content" not in merged_data or not isinstance(merged_data["content"], str):
                    merged_data["content"] = default_merged["content"]

                if "brief" not in merged_data or not isinstance(merged_data["brief"], str):
                    merged_data["brief"] = default_merged["brief"]

                if "detailed" not in merged_data or not isinstance(merged_data["detailed"], str):
                    merged_data["detailed"] = default_merged["detailed"]

                # 处理关键概念
                if "keypoints" not in merged_data or not isinstance(merged_data["keypoints"], list):
                    merged_data["keypoints"] = default_merged["keypoints"]
                else:
                    # 确保keypoints中的每个项目都是字符串
                    merged_data["keypoints"] = [str(point) for point in merged_data["keypoints"] if point is not None]
                    if not merged_data["keypoints"]:
                        merged_data["keypoints"] = ["合并的关键概念"]

                # 处理事件
                if "events" not in merged_data or not isinstance(merged_data["events"], list):
                    merged_data["events"] = default_merged["events"]
                else:
                    # 确保events中的每个项目都是字符串
                    merged_data["events"] = [str(event) for event in merged_data["events"] if event is not None]
                    if not merged_data["events"]:
                        merged_data["events"] = ["合并的事件"]

                # 添加key_points兼容
                merged_data["key_points"] = merged_data["keypoints"] + merged_data["events"]

            except Exception as e:
                logger.error(f"合并记忆时处理JSON出错: {str(e)}")
                traceback.print_exc()
                merged_data = default_merged
        except Exception as e:
            logger.error(f"合并记忆调用LLM出错: {str(e)}")
            traceback.print_exc()
            merged_data = default_merged

        # 创建新的记忆项
        # 合并记忆项的标签
        merged_tags = memory_item1.tags.union(memory_item2.tags)

        # 取两个记忆项中更强的来源
        merged_source = (
            memory_item1.from_source
            if memory_item1.memory_strength >= memory_item2.memory_strength
            else memory_item2.from_source
        )

        # 创建新的记忆项
        merged_memory = MemoryItem(data=merged_data["content"], from_source=merged_source, tags=list(merged_tags))

        # 设置合并后的摘要
        summary = {
            "brief": merged_data["brief"],
            "detailed": merged_data["detailed"],
            "keypoints": merged_data["keypoints"],
            "events": merged_data["events"],
            "key_points": merged_data["key_points"],
        }
        merged_memory.set_summary(summary)

        # 记忆强度取两者最大值
        merged_memory.memory_strength = max(memory_item1.memory_strength, memory_item2.memory_strength)

        # 添加到存储中
        self.push_item(merged_memory)

        # 如果需要，删除原始记忆
        if delete_originals:
            self.delete(memory_id1)
            self.delete(memory_id2)

        return merged_memory

    def delete_earliest_memory(self) -> bool:
        """
        删除最早的记忆项

        Returns:
            是否成功删除
        """
        # 获取所有记忆项
        all_memories = self.get_all_items()

        if not all_memories:
            return False

        # 按时间戳排序，找到最早的记忆项
        earliest_memory = min(all_memories, key=lambda item: item.timestamp)

        # 删除最早的记忆项
        return self.delete(earliest_memory.id)
