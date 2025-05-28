# -*- coding: utf-8 -*-
import datetime
import math
import random
import time
import re
import json
from itertools import combinations

import jieba
import networkx as nx
import numpy as np
from collections import Counter
from ...llm_models.utils_model import LLMRequest
from src.common.logger_manager import get_logger
from src.chat.memory_system.sample_distribution import MemoryBuildScheduler  # 分布生成器
from ..utils.chat_message_builder import (
    get_raw_msg_by_timestamp,
    build_readable_messages,
)  # 导入 build_readable_messages
from ..utils.utils import translate_timestamp_to_human_readable
from rich.traceback import install

from ...config.config import global_config
from src.common.database.database_model import Messages, GraphNodes, GraphEdges  # Peewee Models导入

install(extra_lines=3)


def calculate_information_content(text):
    """计算文本的信息量（熵）"""
    char_count = Counter(text)
    total_chars = len(text)
    if total_chars == 0:
        return 0
    entropy = 0
    for count in char_count.values():
        probability = count / total_chars
        entropy -= probability * math.log2(probability)

    return entropy


def cosine_similarity(v1, v2):
    """计算余弦相似度"""
    dot_product = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0
    return dot_product / (norm1 * norm2)


logger = get_logger("memory")


class MemoryGraph:
    def __init__(self):
        self.G = nx.Graph()  # 使用 networkx 的图结构

    def connect_dot(self, concept1, concept2):
        # 避免自连接
        if concept1 == concept2:
            return

        current_time = datetime.datetime.now().timestamp()

        # 如果边已存在,增加 strength
        if self.G.has_edge(concept1, concept2):
            self.G[concept1][concept2]["strength"] = self.G[concept1][concept2].get("strength", 1) + 1
            # 更新最后修改时间
            self.G[concept1][concept2]["last_modified"] = current_time
        else:
            # 如果是新边,初始化 strength 为 1
            self.G.add_edge(
                concept1,
                concept2,
                strength=1,
                created_time=current_time,  # 添加创建时间
                last_modified=current_time,
            )  # 添加最后修改时间

    def add_dot(self, concept, memory):
        current_time = datetime.datetime.now().timestamp()

        if concept in self.G:
            if "memory_items" in self.G.nodes[concept]:
                if not isinstance(self.G.nodes[concept]["memory_items"], list):
                    self.G.nodes[concept]["memory_items"] = [self.G.nodes[concept]["memory_items"]]
                self.G.nodes[concept]["memory_items"].append(memory)
                # 更新最后修改时间
                self.G.nodes[concept]["last_modified"] = current_time
            else:
                self.G.nodes[concept]["memory_items"] = [memory]
                # 如果节点存在但没有memory_items,说明是第一次添加memory,设置created_time
                if "created_time" not in self.G.nodes[concept]:
                    self.G.nodes[concept]["created_time"] = current_time
                self.G.nodes[concept]["last_modified"] = current_time
        else:
            # 如果是新节点,创建新的记忆列表
            self.G.add_node(
                concept,
                memory_items=[memory],
                created_time=current_time,  # 添加创建时间
                last_modified=current_time,
            )  # 添加最后修改时间

    def get_dot(self, concept):
        # 检查节点是否存在于图中
        if concept in self.G:
            # 从图中获取节点数据
            node_data = self.G.nodes[concept]
            return concept, node_data
        return None

    def get_related_item(self, topic, depth=1):
        if topic not in self.G:
            return [], []

        first_layer_items = []
        second_layer_items = []

        # 获取相邻节点
        neighbors = list(self.G.neighbors(topic))

        # 获取当前节点的记忆项
        node_data = self.get_dot(topic)
        if node_data:
            concept, data = node_data
            if "memory_items" in data:
                memory_items = data["memory_items"]
                if isinstance(memory_items, list):
                    first_layer_items.extend(memory_items)
                else:
                    first_layer_items.append(memory_items)

        # 只在depth=2时获取第二层记忆
        if depth >= 2:
            # 获取相邻节点的记忆项
            for neighbor in neighbors:
                node_data = self.get_dot(neighbor)
                if node_data:
                    concept, data = node_data
                    if "memory_items" in data:
                        memory_items = data["memory_items"]
                        if isinstance(memory_items, list):
                            second_layer_items.extend(memory_items)
                        else:
                            second_layer_items.append(memory_items)

        return first_layer_items, second_layer_items

    @property
    def dots(self):
        # 返回所有节点对应的 Memory_dot 对象
        return [self.get_dot(node) for node in self.G.nodes()]

    def forget_topic(self, topic):
        """随机删除指定话题中的一条记忆，如果话题没有记忆则移除该话题节点"""
        if topic not in self.G:
            return None

        # 获取话题节点数据
        node_data = self.G.nodes[topic]

        # 如果节点存在memory_items
        if "memory_items" in node_data:
            memory_items = node_data["memory_items"]

            # 确保memory_items是列表
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            # 如果有记忆项可以删除
            if memory_items:
                # 随机选择一个记忆项删除
                removed_item = random.choice(memory_items)
                memory_items.remove(removed_item)

                # 更新节点的记忆项
                if memory_items:
                    self.G.nodes[topic]["memory_items"] = memory_items
                else:
                    # 如果没有记忆项了，删除整个节点
                    self.G.remove_node(topic)

                return removed_item

        return None


# 海马体
class Hippocampus:
    def __init__(self):
        self.memory_graph = MemoryGraph()
        self.model_summary = None
        self.entorhinal_cortex = None
        self.parahippocampal_gyrus = None

    def initialize(self):
        # 初始化子组件
        self.entorhinal_cortex = EntorhinalCortex(self)
        self.parahippocampal_gyrus = ParahippocampalGyrus(self)
        # 从数据库加载记忆图
        self.entorhinal_cortex.sync_memory_from_db()
        # TODO: API-Adapter修改标记
        self.model_summary = LLMRequest(global_config.model.memory_summary, request_type="memory")

    def get_all_node_names(self) -> list:
        """获取记忆图中所有节点的名字列表"""
        return list(self.memory_graph.G.nodes())

    @staticmethod
    def calculate_node_hash(concept, memory_items) -> int:
        """计算节点的特征值"""
        if not isinstance(memory_items, list):
            memory_items = [memory_items] if memory_items else []
        sorted_items = sorted(memory_items)
        content = f"{concept}:{'|'.join(sorted_items)}"
        return hash(content)

    @staticmethod
    def calculate_edge_hash(source, target) -> int:
        """计算边的特征值"""
        nodes = sorted([source, target])
        return hash(f"{nodes[0]}:{nodes[1]}")

    @staticmethod
    def find_topic_llm(text, topic_num):
        prompt = (
            f"这是一段文字：\n{text}\n\n请你从这段话中总结出最多{topic_num}个关键的概念，可以是名词，动词，或者特定人物，帮我列出来，"
            f"将主题用逗号隔开，并加上<>,例如<主题1>,<主题2>......尽可能精简。只需要列举最多{topic_num}个话题就好，不要有序号，不要告诉我其他内容。"
            f"如果确定找不出主题或者没有明显主题，返回<none>。"
        )
        return prompt

    @staticmethod
    def topic_what(text, topic):
        # 不再需要 time_info 参数
        prompt = (
            f'这是一段文字：\n{text}\n\n我想让你基于这段文字来概括"{topic}"这个概念，帮我总结成一句自然的话，'
            f"要求包含对这个概念的定义，内容，知识，可以包含时间和人物。只输出这句话就好"
        )
        return prompt

    @staticmethod
    def calculate_topic_num(text, compress_rate):
        """计算文本的话题数量"""
        information_content = calculate_information_content(text)
        topic_by_length = text.count("\n") * compress_rate
        topic_by_information_content = max(1, min(5, int((information_content - 3) * 2)))
        topic_num = int((topic_by_length + topic_by_information_content) / 2)
        logger.debug(
            f"topic_by_length: {topic_by_length}, topic_by_information_content: {topic_by_information_content}, "
            f"topic_num: {topic_num}"
        )
        return topic_num

    def get_memory_from_keyword(self, keyword: str, max_depth: int = 2) -> list:
        """从关键词获取相关记忆。

        Args:
            keyword (str): 关键词
            max_depth (int, optional): 记忆检索深度，默认为2。1表示只获取直接相关的记忆，2表示获取间接相关的记忆。

        Returns:
            list: 记忆列表，每个元素是一个元组 (topic, memory_items, similarity)
                - topic: str, 记忆主题
                - memory_items: list, 该主题下的记忆项列表
                - similarity: float, 与关键词的相似度
        """
        if not keyword:
            return []

        # 获取所有节点
        all_nodes = list(self.memory_graph.G.nodes())
        memories = []

        # 计算关键词的词集合
        keyword_words = set(jieba.cut(keyword))

        # 遍历所有节点，计算相似度
        for node in all_nodes:
            node_words = set(jieba.cut(node))
            all_words = keyword_words | node_words
            v1 = [1 if word in keyword_words else 0 for word in all_words]
            v2 = [1 if word in node_words else 0 for word in all_words]
            similarity = cosine_similarity(v1, v2)

            # 如果相似度超过阈值，获取该节点的记忆
            if similarity >= 0.3:  # 可以调整这个阈值
                node_data = self.memory_graph.G.nodes[node]
                memory_items = node_data.get("memory_items", [])
                if not isinstance(memory_items, list):
                    memory_items = [memory_items] if memory_items else []

                memories.append((node, memory_items, similarity))

        # 按相似度降序排序
        memories.sort(key=lambda x: x[2], reverse=True)
        return memories

    async def get_memory_from_text(
        self,
        text: str,
        max_memory_num: int = 3,
        max_memory_length: int = 2,
        max_depth: int = 3,
        fast_retrieval: bool = False,
    ) -> list:
        """从文本中提取关键词并获取相关记忆。

        Args:
            text (str): 输入文本
            max_memory_num (int, optional): 返回的记忆条目数量上限。默认为3，表示最多返回3条与输入文本相关度最高的记忆。
            max_memory_length (int, optional): 每个主题最多返回的记忆条目数量。默认为2，表示每个主题最多返回2条相似度最高的记忆。
            max_depth (int, optional): 记忆检索深度。默认为3。值越大，检索范围越广，可以获取更多间接相关的记忆，但速度会变慢。
            fast_retrieval (bool, optional): 是否使用快速检索。默认为False。
                如果为True，使用jieba分词和TF-IDF提取关键词，速度更快但可能不够准确。
                如果为False，使用LLM提取关键词，速度较慢但更准确。

        Returns:
            list: 记忆列表，每个元素是一个元组 (topic, memory_items, similarity)
                - topic: str, 记忆主题
                - memory_items: list, 该主题下的记忆项列表
                - similarity: float, 与文本的相似度
        """
        if not text:
            return []

        if fast_retrieval:
            # 使用jieba分词提取关键词
            words = jieba.cut(text)
            # 过滤掉停用词和单字词
            keywords = [word for word in words if len(word) > 1]
            # 去重
            keywords = list(set(keywords))
            # 限制关键词数量
            logger.debug(f"提取关键词: {keywords}")

        else:
            # 使用LLM提取关键词
            topic_num = min(5, max(1, int(len(text) * 0.1)))  # 根据文本长度动态调整关键词数量
            # logger.info(f"提取关键词数量: {topic_num}")
            topics_response = await self.model_summary.generate_response(self.find_topic_llm(text, topic_num))

            # 提取关键词
            keywords = re.findall(r"<([^>]+)>", topics_response[0])
            if not keywords:
                keywords = []
            else:
                keywords = [
                    keyword.strip()
                    for keyword in ",".join(keywords).replace("，", ",").replace("、", ",").replace(" ", ",").split(",")
                    if keyword.strip()
                ]

        # logger.info(f"提取的关键词: {', '.join(keywords)}")

        # 过滤掉不存在于记忆图中的关键词
        valid_keywords = [keyword for keyword in keywords if keyword in self.memory_graph.G]
        if not valid_keywords:
            logger.info("没有找到有效的关键词节点")
            return []

        logger.debug(f"有效的关键词: {', '.join(valid_keywords)}")

        # 从每个关键词获取记忆
        activate_map = {}  # 存储每个词的累计激活值

        # 对每个关键词进行扩散式检索
        for keyword in valid_keywords:
            logger.debug(f"开始以关键词 '{keyword}' 为中心进行扩散检索 (最大深度: {max_depth}):")
            # 初始化激活值
            activation_values = {keyword: 1.0}
            # 记录已访问的节点
            visited_nodes = {keyword}
            # 待处理的节点队列，每个元素是(节点, 激活值, 当前深度)
            nodes_to_process = [(keyword, 1.0, 0)]

            while nodes_to_process:
                current_node, current_activation, current_depth = nodes_to_process.pop(0)

                # 如果激活值小于0或超过最大深度，停止扩散
                if current_activation <= 0 or current_depth >= max_depth:
                    continue

                # 获取当前节点的所有邻居
                neighbors = list(self.memory_graph.G.neighbors(current_node))

                for neighbor in neighbors:
                    if neighbor in visited_nodes:
                        continue

                    # 获取连接强度
                    edge_data = self.memory_graph.G[current_node][neighbor]
                    strength = edge_data.get("strength", 1)

                    # 计算新的激活值
                    new_activation = current_activation - (1 / strength)

                    if new_activation > 0:
                        activation_values[neighbor] = new_activation
                        visited_nodes.add(neighbor)
                        nodes_to_process.append((neighbor, new_activation, current_depth + 1))
                        logger.trace(
                            f"节点 '{neighbor}' 被激活，激活值: {new_activation:.2f} (通过 '{current_node}' 连接，强度: {strength}, 深度: {current_depth + 1})"
                        )  # noqa: E501

            # 更新激活映射
            for node, activation_value in activation_values.items():
                if activation_value > 0:
                    if node in activate_map:
                        activate_map[node] += activation_value
                    else:
                        activate_map[node] = activation_value

        # 输出激活映射
        # logger.info("激活映射统计:")
        # for node, total_activation in sorted(activate_map.items(), key=lambda x: x[1], reverse=True):
        #     logger.info(f"节点 '{node}': 累计激活值 = {total_activation:.2f}")

        # 基于激活值平方的独立概率选择
        remember_map = {}
        # logger.info("基于激活值平方的归一化选择:")

        # 计算所有激活值的平方和
        total_squared_activation = sum(activation**2 for activation in activate_map.values())
        if total_squared_activation > 0:
            # 计算归一化的激活值
            normalized_activations = {
                node: (activation**2) / total_squared_activation for node, activation in activate_map.items()
            }

            # 按归一化激活值排序并选择前max_memory_num个
            sorted_nodes = sorted(normalized_activations.items(), key=lambda x: x[1], reverse=True)[:max_memory_num]

            # 将选中的节点添加到remember_map
            for node, normalized_activation in sorted_nodes:
                remember_map[node] = activate_map[node]  # 使用原始激活值
                logger.debug(
                    f"节点 '{node}' (归一化激活值: {normalized_activation:.2f}, 激活值: {activate_map[node]:.2f})"
                )
        else:
            logger.info("没有有效的激活值")

        # 从选中的节点中提取记忆
        all_memories = []
        # logger.info("开始从选中的节点中提取记忆:")
        for node, activation in remember_map.items():
            logger.debug(f"处理节点 '{node}' (激活值: {activation:.2f}):")
            node_data = self.memory_graph.G.nodes[node]
            memory_items = node_data.get("memory_items", [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            if memory_items:
                logger.debug(f"节点包含 {len(memory_items)} 条记忆")
                # 计算每条记忆与输入文本的相似度
                memory_similarities = []
                for memory in memory_items:
                    # 计算与输入文本的相似度
                    memory_words = set(jieba.cut(memory))
                    text_words = set(jieba.cut(text))
                    all_words = memory_words | text_words
                    v1 = [1 if word in memory_words else 0 for word in all_words]
                    v2 = [1 if word in text_words else 0 for word in all_words]
                    similarity = cosine_similarity(v1, v2)
                    memory_similarities.append((memory, similarity))

                # 按相似度排序
                memory_similarities.sort(key=lambda x: x[1], reverse=True)
                # 获取最匹配的记忆
                top_memories = memory_similarities[:max_memory_length]

                # 添加到结果中
                for memory, similarity in top_memories:
                    all_memories.append((node, [memory], similarity))
                    # logger.info(f"选中记忆: {memory} (相似度: {similarity:.2f})")
            else:
                logger.info("节点没有记忆")

        # 去重（基于记忆内容）
        logger.debug("开始记忆去重:")
        seen_memories = set()
        unique_memories = []
        for topic, memory_items, activation_value in all_memories:
            memory = memory_items[0]  # 因为每个topic只有一条记忆
            if memory not in seen_memories:
                seen_memories.add(memory)
                unique_memories.append((topic, memory_items, activation_value))
                logger.debug(f"保留记忆: {memory} (来自节点: {topic}, 激活值: {activation_value:.2f})")
            else:
                logger.debug(f"跳过重复记忆: {memory} (来自节点: {topic})")

        # 转换为(关键词, 记忆)格式
        result = []
        for topic, memory_items, _ in unique_memories:
            memory = memory_items[0]  # 因为每个topic只有一条记忆
            result.append((topic, memory))
            logger.debug(f"选中记忆: {memory} (来自节点: {topic})")

        return result

    async def get_memory_from_topic(
        self,
        keywords: list[str],
        max_memory_num: int = 3,
        max_memory_length: int = 2,
        max_depth: int = 3,
    ) -> list:
        """从文本中提取关键词并获取相关记忆。

        Args:
            keywords (list): 输入文本
            max_memory_num (int, optional): 返回的记忆条目数量上限。默认为3，表示最多返回3条与输入文本相关度最高的记忆。
            max_memory_length (int, optional): 每个主题最多返回的记忆条目数量。默认为2，表示每个主题最多返回2条相似度最高的记忆。
            max_depth (int, optional): 记忆检索深度。默认为3。值越大，检索范围越广，可以获取更多间接相关的记忆，但速度会变慢。

        Returns:
            list: 记忆列表，每个元素是一个元组 (topic, memory_items, similarity)
                - topic: str, 记忆主题
                - memory_items: list, 该主题下的记忆项列表
                - similarity: float, 与文本的相似度
        """
        if not keywords:
            return []

        logger.info(f"提取的关键词: {', '.join(keywords)}")

        # 过滤掉不存在于记忆图中的关键词
        valid_keywords = [keyword for keyword in keywords if keyword in self.memory_graph.G]
        if not valid_keywords:
            logger.info("没有找到有效的关键词节点")
            return []

        logger.debug(f"有效的关键词: {', '.join(valid_keywords)}")

        # 从每个关键词获取记忆
        activate_map = {}  # 存储每个词的累计激活值

        # 对每个关键词进行扩散式检索
        for keyword in valid_keywords:
            logger.debug(f"开始以关键词 '{keyword}' 为中心进行扩散检索 (最大深度: {max_depth}):")
            # 初始化激活值
            activation_values = {keyword: 1.0}
            # 记录已访问的节点
            visited_nodes = {keyword}
            # 待处理的节点队列，每个元素是(节点, 激活值, 当前深度)
            nodes_to_process = [(keyword, 1.0, 0)]

            while nodes_to_process:
                current_node, current_activation, current_depth = nodes_to_process.pop(0)

                # 如果激活值小于0或超过最大深度，停止扩散
                if current_activation <= 0 or current_depth >= max_depth:
                    continue

                # 获取当前节点的所有邻居
                neighbors = list(self.memory_graph.G.neighbors(current_node))

                for neighbor in neighbors:
                    if neighbor in visited_nodes:
                        continue

                    # 获取连接强度
                    edge_data = self.memory_graph.G[current_node][neighbor]
                    strength = edge_data.get("strength", 1)

                    # 计算新的激活值
                    new_activation = current_activation - (1 / strength)

                    if new_activation > 0:
                        activation_values[neighbor] = new_activation
                        visited_nodes.add(neighbor)
                        nodes_to_process.append((neighbor, new_activation, current_depth + 1))
                        logger.trace(
                            f"节点 '{neighbor}' 被激活，激活值: {new_activation:.2f} (通过 '{current_node}' 连接，强度: {strength}, 深度: {current_depth + 1})"
                        )  # noqa: E501

            # 更新激活映射
            for node, activation_value in activation_values.items():
                if activation_value > 0:
                    if node in activate_map:
                        activate_map[node] += activation_value
                    else:
                        activate_map[node] = activation_value

        # 基于激活值平方的独立概率选择
        remember_map = {}
        # logger.info("基于激活值平方的归一化选择:")

        # 计算所有激活值的平方和
        total_squared_activation = sum(activation**2 for activation in activate_map.values())
        if total_squared_activation > 0:
            # 计算归一化的激活值
            normalized_activations = {
                node: (activation**2) / total_squared_activation for node, activation in activate_map.items()
            }

            # 按归一化激活值排序并选择前max_memory_num个
            sorted_nodes = sorted(normalized_activations.items(), key=lambda x: x[1], reverse=True)[:max_memory_num]

            # 将选中的节点添加到remember_map
            for node, normalized_activation in sorted_nodes:
                remember_map[node] = activate_map[node]  # 使用原始激活值
                logger.debug(
                    f"节点 '{node}' (归一化激活值: {normalized_activation:.2f}, 激活值: {activate_map[node]:.2f})"
                )
        else:
            logger.info("没有有效的激活值")

        # 从选中的节点中提取记忆
        all_memories = []
        # logger.info("开始从选中的节点中提取记忆:")
        for node, activation in remember_map.items():
            logger.debug(f"处理节点 '{node}' (激活值: {activation:.2f}):")
            node_data = self.memory_graph.G.nodes[node]
            memory_items = node_data.get("memory_items", [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            if memory_items:
                logger.debug(f"节点包含 {len(memory_items)} 条记忆")
                # 计算每条记忆与输入文本的相似度
                memory_similarities = []
                for memory in memory_items:
                    # 计算与输入文本的相似度
                    memory_words = set(jieba.cut(memory))
                    text_words = set(keywords)
                    all_words = memory_words | text_words
                    v1 = [1 if word in memory_words else 0 for word in all_words]
                    v2 = [1 if word in text_words else 0 for word in all_words]
                    similarity = cosine_similarity(v1, v2)
                    memory_similarities.append((memory, similarity))

                # 按相似度排序
                memory_similarities.sort(key=lambda x: x[1], reverse=True)
                # 获取最匹配的记忆
                top_memories = memory_similarities[:max_memory_length]

                # 添加到结果中
                for memory, similarity in top_memories:
                    all_memories.append((node, [memory], similarity))
                    # logger.info(f"选中记忆: {memory} (相似度: {similarity:.2f})")
            else:
                logger.info("节点没有记忆")

        # 去重（基于记忆内容）
        logger.debug("开始记忆去重:")
        seen_memories = set()
        unique_memories = []
        for topic, memory_items, activation_value in all_memories:
            memory = memory_items[0]  # 因为每个topic只有一条记忆
            if memory not in seen_memories:
                seen_memories.add(memory)
                unique_memories.append((topic, memory_items, activation_value))
                logger.debug(f"保留记忆: {memory} (来自节点: {topic}, 激活值: {activation_value:.2f})")
            else:
                logger.debug(f"跳过重复记忆: {memory} (来自节点: {topic})")

        # 转换为(关键词, 记忆)格式
        result = []
        for topic, memory_items, _ in unique_memories:
            memory = memory_items[0]  # 因为每个topic只有一条记忆
            result.append((topic, memory))
            logger.debug(f"选中记忆: {memory} (来自节点: {topic})")

        return result

    async def get_activate_from_text(self, text: str, max_depth: int = 3, fast_retrieval: bool = False) -> float:
        """从文本中提取关键词并获取相关记忆。

        Args:
            text (str): 输入文本
            max_depth (int, optional): 记忆检索深度。默认为2。
            fast_retrieval (bool, optional): 是否使用快速检索。默认为False。
                如果为True，使用jieba分词和TF-IDF提取关键词，速度更快但可能不够准确。
                如果为False，使用LLM提取关键词，速度较慢但更准确。

        Returns:
            float: 激活节点数与总节点数的比值
        """
        if not text:
            return 0

        if fast_retrieval:
            # 使用jieba分词提取关键词
            words = jieba.cut(text)
            # 过滤掉停用词和单字词
            keywords = [word for word in words if len(word) > 1]
            # 去重
            keywords = list(set(keywords))
            # 限制关键词数量
            keywords = keywords[:5]
        else:
            # 使用LLM提取关键词
            topic_num = min(5, max(1, int(len(text) * 0.1)))  # 根据文本长度动态调整关键词数量
            # logger.info(f"提取关键词数量: {topic_num}")
            topics_response = await self.model_summary.generate_response(self.find_topic_llm(text, topic_num))

            # 提取关键词
            keywords = re.findall(r"<([^>]+)>", topics_response[0])
            if not keywords:
                keywords = []
            else:
                keywords = [
                    keyword.strip()
                    for keyword in ",".join(keywords).replace("，", ",").replace("、", ",").replace(" ", ",").split(",")
                    if keyword.strip()
                ]

        # logger.info(f"提取的关键词: {', '.join(keywords)}")

        # 过滤掉不存在于记忆图中的关键词
        valid_keywords = [keyword for keyword in keywords if keyword in self.memory_graph.G]
        if not valid_keywords:
            # logger.info("没有找到有效的关键词节点")
            return 0

        logger.debug(f"有效的关键词: {', '.join(valid_keywords)}")

        # 从每个关键词获取记忆
        activate_map = {}  # 存储每个词的累计激活值

        # 对每个关键词进行扩散式检索
        for keyword in valid_keywords:
            logger.trace(f"开始以关键词 '{keyword}' 为中心进行扩散检索 (最大深度: {max_depth}):")
            # 初始化激活值
            activation_values = {keyword: 1.0}
            # 记录已访问的节点
            visited_nodes = {keyword}
            # 待处理的节点队列，每个元素是(节点, 激活值, 当前深度)
            nodes_to_process = [(keyword, 1.0, 0)]

            while nodes_to_process:
                current_node, current_activation, current_depth = nodes_to_process.pop(0)

                # 如果激活值小于0或超过最大深度，停止扩散
                if current_activation <= 0 or current_depth >= max_depth:
                    continue

                # 获取当前节点的所有邻居
                neighbors = list(self.memory_graph.G.neighbors(current_node))

                for neighbor in neighbors:
                    if neighbor in visited_nodes:
                        continue

                    # 获取连接强度
                    edge_data = self.memory_graph.G[current_node][neighbor]
                    strength = edge_data.get("strength", 1)

                    # 计算新的激活值
                    new_activation = current_activation - (1 / strength)

                    if new_activation > 0:
                        activation_values[neighbor] = new_activation
                        visited_nodes.add(neighbor)
                        nodes_to_process.append((neighbor, new_activation, current_depth + 1))
                        # logger.debug(
                        # f"节点 '{neighbor}' 被激活，激活值: {new_activation:.2f} (通过 '{current_node}' 连接，强度: {strength}, 深度: {current_depth + 1})")  # noqa: E501

            # 更新激活映射
            for node, activation_value in activation_values.items():
                if activation_value > 0:
                    if node in activate_map:
                        activate_map[node] += activation_value
                    else:
                        activate_map[node] = activation_value

        # 输出激活映射
        # logger.info("激活映射统计:")
        # for node, total_activation in sorted(activate_map.items(), key=lambda x: x[1], reverse=True):
        #     logger.info(f"节点 '{node}': 累计激活值 = {total_activation:.2f}")

        # 计算激活节点数与总节点数的比值
        total_activation = sum(activate_map.values())
        logger.trace(f"总激活值: {total_activation:.2f}")
        total_nodes = len(self.memory_graph.G.nodes())
        # activated_nodes = len(activate_map)
        activation_ratio = total_activation / total_nodes if total_nodes > 0 else 0
        activation_ratio = activation_ratio * 60
        logger.info(f"总激活值: {total_activation:.2f}, 总节点数: {total_nodes}, 激活: {activation_ratio}")

        return activation_ratio


# 负责海马体与其他部分的交互
class EntorhinalCortex:
    def __init__(self, hippocampus: Hippocampus):
        self.hippocampus = hippocampus
        self.memory_graph = hippocampus.memory_graph

    def get_memory_sample(self):
        """从数据库获取记忆样本"""
        # 硬编码：每条消息最大记忆次数
        max_memorized_time_per_msg = 2

        # 创建双峰分布的记忆调度器
        sample_scheduler = MemoryBuildScheduler(
            n_hours1=global_config.memory.memory_build_distribution[0],
            std_hours1=global_config.memory.memory_build_distribution[1],
            weight1=global_config.memory.memory_build_distribution[2],
            n_hours2=global_config.memory.memory_build_distribution[3],
            std_hours2=global_config.memory.memory_build_distribution[4],
            weight2=global_config.memory.memory_build_distribution[5],
            total_samples=global_config.memory.memory_build_sample_num,
        )

        timestamps = sample_scheduler.get_timestamp_array()
        # 使用 translate_timestamp_to_human_readable 并指定 mode="normal"
        readable_timestamps = [translate_timestamp_to_human_readable(ts, mode="normal") for ts in timestamps]
        logger.info(f"回忆往事: {readable_timestamps}")
        chat_samples = []
        for timestamp in timestamps:
            # 调用修改后的 random_get_msg_snippet
            messages = self.random_get_msg_snippet(
                timestamp, global_config.memory.memory_build_sample_length, max_memorized_time_per_msg
            )
            if messages:
                time_diff = (datetime.datetime.now().timestamp() - timestamp) / 3600
                logger.debug(f"成功抽取 {time_diff:.1f} 小时前的消息样本，共{len(messages)}条")
                chat_samples.append(messages)
            else:
                logger.debug(f"时间戳 {timestamp} 的消息样本抽取失败")

        return chat_samples

    @staticmethod
    def random_get_msg_snippet(target_timestamp: float, chat_size: int, max_memorized_time_per_msg: int) -> list | None:
        """从数据库中随机获取指定时间戳附近的消息片段 (使用 chat_message_builder)"""
        try_count = 0
        time_window_seconds = random.randint(300, 1800)  # 随机时间窗口，5到30分钟

        while try_count < 3:
            # 定义时间范围：从目标时间戳开始，向后推移 time_window_seconds
            timestamp_start = target_timestamp
            timestamp_end = target_timestamp + time_window_seconds

            # 使用 chat_message_builder 的函数获取消息
            # limit_mode='earliest' 获取这个时间窗口内最早的 chat_size 条消息
            messages = get_raw_msg_by_timestamp(
                timestamp_start=timestamp_start, timestamp_end=timestamp_end, limit=chat_size, limit_mode="earliest"
            )

            if messages:
                # 检查获取到的所有消息是否都未达到最大记忆次数
                all_valid = True
                for message in messages:
                    if message.get("memorized_times", 0) >= max_memorized_time_per_msg:
                        all_valid = False
                        break

                # 如果所有消息都有效
                if all_valid:
                    # 更新数据库中的记忆次数
                    for message in messages:
                        # 确保在更新前获取最新的 memorized_times
                        current_memorized_times = message.get("memorized_times", 0)
                        # 使用 Peewee 更新记录
                        Messages.update(memorized_times=current_memorized_times + 1).where(
                            Messages.message_id == message["message_id"]
                        ).execute()
                    return messages  # 直接返回原始的消息列表

            # 如果获取失败或消息无效，增加尝试次数
            try_count += 1
            target_timestamp -= 120  # 如果第一次尝试失败，稍微向前调整时间戳再试

        # 三次尝试都失败，返回 None
        return None

    async def sync_memory_to_db(self):
        """将记忆图同步到数据库"""
        # 获取数据库中所有节点和内存中所有节点
        db_nodes = {node.concept: node for node in GraphNodes.select()}
        memory_nodes = list(self.memory_graph.G.nodes(data=True))

        # 检查并更新节点
        for concept, data in memory_nodes:
            memory_items = data.get("memory_items", [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            # 计算内存中节点的特征值
            memory_hash = self.hippocampus.calculate_node_hash(concept, memory_items)

            # 获取时间信息
            created_time = data.get("created_time", datetime.datetime.now().timestamp())
            last_modified = data.get("last_modified", datetime.datetime.now().timestamp())

            # 将memory_items转换为JSON字符串
            memory_items_json = json.dumps(memory_items, ensure_ascii=False)

            if concept not in db_nodes:
                # 数据库中缺少的节点,添加
                GraphNodes.create(
                    concept=concept,
                    memory_items=memory_items_json,
                    hash=memory_hash,
                    created_time=created_time,
                    last_modified=last_modified,
                )
            else:
                # 获取数据库中节点的特征值
                db_node = db_nodes[concept]
                db_hash = db_node.hash

                # 如果特征值不同,则更新节点
                if db_hash != memory_hash:
                    db_node.memory_items = memory_items_json
                    db_node.hash = memory_hash
                    db_node.last_modified = last_modified
                    db_node.save()

        # 处理边的信息
        db_edges = list(GraphEdges.select())
        memory_edges = list(self.memory_graph.G.edges(data=True))

        # 创建边的哈希值字典
        db_edge_dict = {}
        for edge in db_edges:
            edge_hash = self.hippocampus.calculate_edge_hash(edge.source, edge.target)
            db_edge_dict[(edge.source, edge.target)] = {"hash": edge_hash, "strength": edge.strength}

        # 检查并更新边
        for source, target, data in memory_edges:
            edge_hash = self.hippocampus.calculate_edge_hash(source, target)
            edge_key = (source, target)
            strength = data.get("strength", 1)

            # 获取边的时间信息
            created_time = data.get("created_time", datetime.datetime.now().timestamp())
            last_modified = data.get("last_modified", datetime.datetime.now().timestamp())

            if edge_key not in db_edge_dict:
                # 添加新边
                GraphEdges.create(
                    source=source,
                    target=target,
                    strength=strength,
                    hash=edge_hash,
                    created_time=created_time,
                    last_modified=last_modified,
                )
            else:
                # 检查边的特征值是否变化
                if db_edge_dict[edge_key]["hash"] != edge_hash:
                    edge = GraphEdges.get(GraphEdges.source == source, GraphEdges.target == target)
                    edge.hash = edge_hash
                    edge.strength = strength
                    edge.last_modified = last_modified
                    edge.save()

    def sync_memory_from_db(self):
        """从数据库同步数据到内存中的图结构"""
        current_time = datetime.datetime.now().timestamp()
        need_update = False

        # 清空当前图
        self.memory_graph.G.clear()

        # 从数据库加载所有节点
        nodes = list(GraphNodes.select())
        for node in nodes:
            concept = node.concept
            memory_items = json.loads(node.memory_items)
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            # 检查时间字段是否存在
            if not node.created_time or not node.last_modified:
                need_update = True
                # 更新数据库中的节点
                update_data = {}
                if not node.created_time:
                    update_data["created_time"] = current_time
                if not node.last_modified:
                    update_data["last_modified"] = current_time

                GraphNodes.update(**update_data).where(GraphNodes.concept == concept).execute()
                logger.info(f"[时间更新] 节点 {concept} 添加缺失的时间字段")

            # 获取时间信息(如果不存在则使用当前时间)
            created_time = node.created_time or current_time
            last_modified = node.last_modified or current_time

            # 添加节点到图中
            self.memory_graph.G.add_node(
                concept, memory_items=memory_items, created_time=created_time, last_modified=last_modified
            )

        # 从数据库加载所有边
        edges = list(GraphEdges.select())
        for edge in edges:
            source = edge.source
            target = edge.target
            strength = edge.strength

            # 检查时间字段是否存在
            if not edge.created_time or not edge.last_modified:
                need_update = True
                # 更新数据库中的边
                update_data = {}
                if not edge.created_time:
                    update_data["created_time"] = current_time
                if not edge.last_modified:
                    update_data["last_modified"] = current_time

                GraphEdges.update(**update_data).where(
                    (GraphEdges.source == source) & (GraphEdges.target == target)
                ).execute()
                logger.info(f"[时间更新] 边 {source} - {target} 添加缺失的时间字段")

            # 获取时间信息(如果不存在则使用当前时间)
            created_time = edge.created_time or current_time
            last_modified = edge.last_modified or current_time

            # 只有当源节点和目标节点都存在时才添加边
            if source in self.memory_graph.G and target in self.memory_graph.G:
                self.memory_graph.G.add_edge(
                    source, target, strength=strength, created_time=created_time, last_modified=last_modified
                )

        if need_update:
            logger.success("[数据库] 已为缺失的时间字段进行补充")

    async def resync_memory_to_db(self):
        """清空数据库并重新同步所有记忆数据"""
        start_time = time.time()
        logger.info("[数据库] 开始重新同步所有记忆数据...")

        # 清空数据库
        clear_start = time.time()
        GraphNodes.delete().execute()
        GraphEdges.delete().execute()
        clear_end = time.time()
        logger.info(f"[数据库] 清空数据库耗时: {clear_end - clear_start:.2f}秒")

        # 获取所有节点和边
        memory_nodes = list(self.memory_graph.G.nodes(data=True))
        memory_edges = list(self.memory_graph.G.edges(data=True))

        # 重新写入节点
        node_start = time.time()
        for concept, data in memory_nodes:
            memory_items = data.get("memory_items", [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            GraphNodes.create(
                concept=concept,
                memory_items=json.dumps(memory_items),
                hash=self.hippocampus.calculate_node_hash(concept, memory_items),
                created_time=data.get("created_time", datetime.datetime.now().timestamp()),
                last_modified=data.get("last_modified", datetime.datetime.now().timestamp()),
            )
        node_end = time.time()
        logger.info(f"[数据库] 写入 {len(memory_nodes)} 个节点耗时: {node_end - node_start:.2f}秒")

        # 重新写入边
        edge_start = time.time()
        for source, target, data in memory_edges:
            GraphEdges.create(
                source=source,
                target=target,
                strength=data.get("strength", 1),
                hash=self.hippocampus.calculate_edge_hash(source, target),
                created_time=data.get("created_time", datetime.datetime.now().timestamp()),
                last_modified=data.get("last_modified", datetime.datetime.now().timestamp()),
            )
        edge_end = time.time()
        logger.info(f"[数据库] 写入 {len(memory_edges)} 条边耗时: {edge_end - edge_start:.2f}秒")

        end_time = time.time()
        logger.success(f"[数据库] 重新同步完成，总耗时: {end_time - start_time:.2f}秒")
        logger.success(f"[数据库] 同步了 {len(memory_nodes)} 个节点和 {len(memory_edges)} 条边")


# 负责整合，遗忘，合并记忆
class ParahippocampalGyrus:
    def __init__(self, hippocampus: Hippocampus):
        self.hippocampus = hippocampus
        self.memory_graph = hippocampus.memory_graph

    async def memory_compress(self, messages: list, compress_rate=0.1):
        """压缩和总结消息内容，生成记忆主题和摘要。

        Args:
            messages (list): 消息列表，每个消息是一个字典，包含数据库消息结构。
            compress_rate (float, optional): 压缩率，用于控制生成的主题数量。默认为0.1。

        Returns:
            tuple: (compressed_memory, similar_topics_dict)
                - compressed_memory: set, 压缩后的记忆集合，每个元素是一个元组 (topic, summary)
                - similar_topics_dict: dict, 相似主题字典

        Process:
            1. 使用 build_readable_messages 生成包含时间、人物信息的格式化文本。
            2. 使用LLM提取关键主题。
            3. 过滤掉包含禁用关键词的主题。
            4. 为每个主题生成摘要。
            5. 查找与现有记忆中的相似主题。
        """
        if not messages:
            return set(), {}

        # 1. 使用 build_readable_messages 生成格式化文本
        # build_readable_messages 只返回一个字符串，不需要解包
        input_text = await build_readable_messages(
            messages,
            merge_messages=True,  # 合并连续消息
            timestamp_mode="normal",  # 使用 'YYYY-MM-DD HH:MM:SS' 格式
            replace_bot_name=False,  # 保留原始用户名
        )

        # 如果生成的可读文本为空（例如所有消息都无效），则直接返回
        if not input_text:
            logger.warning("无法从提供的消息生成可读文本，跳过记忆压缩。")
            return set(), {}

        logger.debug(f"用于压缩的格式化文本:\n{input_text}")

        # 2. 使用LLM提取关键主题
        topic_num = self.hippocampus.calculate_topic_num(input_text, compress_rate)
        topics_response = await self.hippocampus.model_summary.generate_response(
            self.hippocampus.find_topic_llm(input_text, topic_num)
        )

        # 提取<>中的内容
        topics = re.findall(r"<([^>]+)>", topics_response[0])

        if not topics:
            topics = ["none"]
        else:
            topics = [
                topic.strip()
                for topic in ",".join(topics).replace("，", ",").replace("、", ",").replace(" ", ",").split(",")
                if topic.strip()
            ]

        # 3. 过滤掉包含禁用关键词的topic
        filtered_topics = [
            topic for topic in topics if not any(keyword in topic for keyword in global_config.memory.memory_ban_words)
        ]

        logger.debug(f"过滤后话题: {filtered_topics}")

        # 4. 创建所有话题的摘要生成任务
        tasks = []
        for topic in filtered_topics:
            # 调用修改后的 topic_what，不再需要 time_info
            topic_what_prompt = self.hippocampus.topic_what(input_text, topic)
            try:
                task = self.hippocampus.model_summary.generate_response_async(topic_what_prompt)
                tasks.append((topic.strip(), task))
            except Exception as e:
                logger.error(f"生成话题 '{topic}' 的摘要时发生错误: {e}")
                continue

        # 等待所有任务完成
        compressed_memory = set()
        similar_topics_dict = {}

        for topic, task in tasks:
            response = await task
            if response:
                compressed_memory.add((topic, response[0]))

                existing_topics = list(self.memory_graph.G.nodes())
                similar_topics = []

                for existing_topic in existing_topics:
                    topic_words = set(jieba.cut(topic))
                    existing_words = set(jieba.cut(existing_topic))

                    all_words = topic_words | existing_words
                    v1 = [1 if word in topic_words else 0 for word in all_words]
                    v2 = [1 if word in existing_words else 0 for word in all_words]

                    similarity = cosine_similarity(v1, v2)

                    if similarity >= 0.7:
                        similar_topics.append((existing_topic, similarity))

                similar_topics.sort(key=lambda x: x[1], reverse=True)
                similar_topics = similar_topics[:3]
                similar_topics_dict[topic] = similar_topics

        return compressed_memory, similar_topics_dict

    async def operation_build_memory(self):
        logger.debug("------------------------------------开始构建记忆--------------------------------------")
        start_time = time.time()
        memory_samples = self.hippocampus.entorhinal_cortex.get_memory_sample()
        all_added_nodes = []
        all_connected_nodes = []
        all_added_edges = []
        for i, messages in enumerate(memory_samples, 1):
            all_topics = []
            progress = (i / len(memory_samples)) * 100
            bar_length = 30
            filled_length = int(bar_length * i // len(memory_samples))
            bar = "█" * filled_length + "-" * (bar_length - filled_length)
            logger.debug(f"进度: [{bar}] {progress:.1f}% ({i}/{len(memory_samples)})")

            compress_rate = global_config.memory.memory_compress_rate
            try:
                compressed_memory, similar_topics_dict = await self.memory_compress(messages, compress_rate)
            except Exception as e:
                logger.error(f"压缩记忆时发生错误: {e}")
                continue
            logger.debug(f"压缩后记忆数量: {compressed_memory}，似曾相识的话题: {similar_topics_dict}")

            current_time = datetime.datetime.now().timestamp()
            logger.debug(f"添加节点: {', '.join(topic for topic, _ in compressed_memory)}")
            all_added_nodes.extend(topic for topic, _ in compressed_memory)

            for topic, memory in compressed_memory:
                self.memory_graph.add_dot(topic, memory)
                all_topics.append(topic)

                if topic in similar_topics_dict:
                    similar_topics = similar_topics_dict[topic]
                    for similar_topic, similarity in similar_topics:
                        if topic != similar_topic:
                            strength = int(similarity * 10)

                            logger.debug(f"连接相似节点: {topic} 和 {similar_topic} (强度: {strength})")
                            all_added_edges.append(f"{topic}-{similar_topic}")

                            all_connected_nodes.append(topic)
                            all_connected_nodes.append(similar_topic)

                            self.memory_graph.G.add_edge(
                                topic,
                                similar_topic,
                                strength=strength,
                                created_time=current_time,
                                last_modified=current_time,
                            )

            for topic1, topic2 in combinations(all_topics, 2):
                logger.debug(f"连接同批次节点: {topic1} 和 {topic2}")
                all_added_edges.append(f"{topic1}-{topic2}")
                self.memory_graph.connect_dot(topic1, topic2)

        logger.success(f"更新记忆: {', '.join(all_added_nodes)}")
        logger.debug(f"强化连接: {', '.join(all_added_edges)}")
        logger.info(f"强化连接节点: {', '.join(all_connected_nodes)}")

        await self.hippocampus.entorhinal_cortex.sync_memory_to_db()

        end_time = time.time()
        logger.success(f"---------------------记忆构建耗时: {end_time - start_time:.2f} 秒---------------------")

    async def operation_forget_topic(self, percentage=0.005):
        start_time = time.time()
        logger.info("[遗忘] 开始检查数据库...")

        # 验证百分比参数
        if not 0 <= percentage <= 1:
            logger.warning(f"[遗忘] 无效的遗忘百分比: {percentage}, 使用默认值 0.005")
            percentage = 0.005

        all_nodes = list(self.memory_graph.G.nodes())
        all_edges = list(self.memory_graph.G.edges())

        if not all_nodes and not all_edges:
            logger.info("[遗忘] 记忆图为空,无需进行遗忘操作")
            return

        # 确保至少检查1个节点和边，且不超过总数
        check_nodes_count = max(1, min(len(all_nodes), int(len(all_nodes) * percentage)))
        check_edges_count = max(1, min(len(all_edges), int(len(all_edges) * percentage)))

        # 只有在有足够的节点和边时才进行采样
        if len(all_nodes) >= check_nodes_count and len(all_edges) >= check_edges_count:
            try:
                nodes_to_check = random.sample(all_nodes, check_nodes_count)
                edges_to_check = random.sample(all_edges, check_edges_count)
            except ValueError as e:
                logger.error(f"[遗忘] 采样错误: {str(e)}")
                return
        else:
            logger.info("[遗忘] 没有足够的节点或边进行遗忘操作")
            return

        # 使用列表存储变化信息
        edge_changes = {
            "weakened": [],  # 存储减弱的边
            "removed": [],  # 存储移除的边
        }
        node_changes = {
            "reduced": [],  # 存储减少记忆的节点
            "removed": [],  # 存储移除的节点
        }

        current_time = datetime.datetime.now().timestamp()

        logger.info("[遗忘] 开始检查连接...")
        edge_check_start = time.time()
        for source, target in edges_to_check:
            edge_data = self.memory_graph.G[source][target]
            last_modified = edge_data.get("last_modified")

            if current_time - last_modified > 3600 * global_config.memory.memory_forget_time:
                current_strength = edge_data.get("strength", 1)
                new_strength = current_strength - 1

                if new_strength <= 0:
                    self.memory_graph.G.remove_edge(source, target)
                    edge_changes["removed"].append(f"{source} -> {target}")
                else:
                    edge_data["strength"] = new_strength
                    edge_data["last_modified"] = current_time
                    edge_changes["weakened"].append(f"{source}-{target} (强度: {current_strength} -> {new_strength})")
        edge_check_end = time.time()
        logger.info(f"[遗忘] 连接检查耗时: {edge_check_end - edge_check_start:.2f}秒")

        logger.info("[遗忘] 开始检查节点...")
        node_check_start = time.time()
        for node in nodes_to_check:
            # 检查节点是否存在，以防在迭代中被移除（例如边移除导致）
            if node not in self.memory_graph.G:
                continue

            node_data = self.memory_graph.G.nodes[node]

            # 首先获取记忆项
            memory_items = node_data.get("memory_items", [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            # 新增：检查节点是否为空
            if not memory_items:
                try:
                    self.memory_graph.G.remove_node(node)
                    node_changes["removed"].append(f"{node}(空节点)")  # 标记为空节点移除
                    logger.debug(f"[遗忘] 移除了空的节点: {node}")
                except nx.NetworkXError as e:
                    logger.warning(f"[遗忘] 移除空节点 {node} 时发生错误（可能已被移除）: {e}")
                continue  # 处理下一个节点

            # --- 如果节点不为空，则执行原来的不活跃检查和随机移除逻辑 ---
            last_modified = node_data.get("last_modified", current_time)
            # 条件1：检查是否长时间未修改 (超过24小时)
            if current_time - last_modified > 3600 * 24:
                # 条件2：再次确认节点包含记忆项（理论上已确认，但作为保险）
                if memory_items:
                    current_count = len(memory_items)
                    # 如果列表非空，才进行随机选择
                    if current_count > 0:
                        removed_item = random.choice(memory_items)
                        try:
                            memory_items.remove(removed_item)

                            # 条件3：检查移除后 memory_items 是否变空
                            if memory_items:  # 如果移除后列表不为空
                                # self.memory_graph.G.nodes[node]["memory_items"] = memory_items # 直接修改列表即可
                                self.memory_graph.G.nodes[node]["last_modified"] = current_time  # 更新修改时间
                                node_changes["reduced"].append(f"{node} (数量: {current_count} -> {len(memory_items)})")
                            else:  # 如果移除后列表为空
                                # 尝试移除节点，处理可能的错误
                                try:
                                    self.memory_graph.G.remove_node(node)
                                    node_changes["removed"].append(f"{node}(遗忘清空)")  # 标记为遗忘清空
                                    logger.debug(f"[遗忘] 节点 {node} 因移除最后一项而被清空。")
                                except nx.NetworkXError as e:
                                    logger.warning(f"[遗忘] 尝试移除节点 {node} 时发生错误（可能已被移除）：{e}")
                        except ValueError:
                            # 这个错误理论上不应发生，因为 removed_item 来自 memory_items
                            logger.warning(f"[遗忘] 尝试从节点 '{node}' 移除不存在的项目 '{removed_item[:30]}...'")
        node_check_end = time.time()
        logger.info(f"[遗忘] 节点检查耗时: {node_check_end - node_check_start:.2f}秒")

        if any(edge_changes.values()) or any(node_changes.values()):
            sync_start = time.time()

            await self.hippocampus.entorhinal_cortex.resync_memory_to_db()

            sync_end = time.time()
            logger.info(f"[遗忘] 数据库同步耗时: {sync_end - sync_start:.2f}秒")

            # 汇总输出所有变化
            logger.info("[遗忘] 遗忘操作统计:")
            if edge_changes["weakened"]:
                logger.info(
                    f"[遗忘] 减弱的连接 ({len(edge_changes['weakened'])}个): {', '.join(edge_changes['weakened'])}"
                )

            if edge_changes["removed"]:
                logger.info(
                    f"[遗忘] 移除的连接 ({len(edge_changes['removed'])}个): {', '.join(edge_changes['removed'])}"
                )

            if node_changes["reduced"]:
                logger.info(
                    f"[遗忘] 减少记忆的节点 ({len(node_changes['reduced'])}个): {', '.join(node_changes['reduced'])}"
                )

            if node_changes["removed"]:
                logger.info(
                    f"[遗忘] 移除的节点 ({len(node_changes['removed'])}个): {', '.join(node_changes['removed'])}"
                )
        else:
            logger.info("[遗忘] 本次检查没有节点或连接满足遗忘条件")

        end_time = time.time()
        logger.info(f"[遗忘] 总耗时: {end_time - start_time:.2f}秒")

    async def operation_consolidate_memory(self):
        """整合记忆：合并节点内相似的记忆项"""
        start_time = time.time()
        percentage = global_config.memory.consolidate_memory_percentage
        similarity_threshold = global_config.memory.consolidation_similarity_threshold
        logger.info(f"[整合] 开始检查记忆节点... 检查比例: {percentage:.2%}, 合并阈值: {similarity_threshold}")

        # 获取所有至少有2条记忆项的节点
        eligible_nodes = []
        for node, data in self.memory_graph.G.nodes(data=True):
            memory_items = data.get("memory_items", [])
            if isinstance(memory_items, list) and len(memory_items) >= 2:
                eligible_nodes.append(node)

        if not eligible_nodes:
            logger.info("[整合] 没有找到包含多个记忆项的节点，无需整合。")
            return

        # 计算需要检查的节点数量
        check_nodes_count = max(1, min(len(eligible_nodes), int(len(eligible_nodes) * percentage)))

        # 随机抽取节点进行检查
        try:
            nodes_to_check = random.sample(eligible_nodes, check_nodes_count)
        except ValueError as e:
            logger.error(f"[整合] 抽样节点时出错: {e}")
            return

        logger.info(f"[整合] 将检查 {len(nodes_to_check)} / {len(eligible_nodes)} 个符合条件的节点。")

        merged_count = 0
        nodes_modified = set()
        current_timestamp = datetime.datetime.now().timestamp()

        for node in nodes_to_check:
            node_data = self.memory_graph.G.nodes[node]
            memory_items = node_data.get("memory_items", [])
            if not isinstance(memory_items, list) or len(memory_items) < 2:
                continue  # 双重检查，理论上不会进入

            items_copy = list(memory_items)  # 创建副本以安全迭代和修改

            # 遍历所有记忆项组合
            for item1, item2 in combinations(items_copy, 2):
                # 确保 item1 和 item2 仍然存在于原始列表中（可能已被之前的合并移除）
                if item1 not in memory_items or item2 not in memory_items:
                    continue

                similarity = self._calculate_item_similarity(item1, item2)

                if similarity >= similarity_threshold:
                    logger.debug(f"[整合] 节点 '{node}' 中发现相似项 (相似度: {similarity:.2f}):")
                    logger.trace(f"  - '{item1}'")
                    logger.trace(f"  - '{item2}'")

                    # 比较信息量
                    info1 = calculate_information_content(item1)
                    info2 = calculate_information_content(item2)

                    if info1 >= info2:
                        item_to_keep = item1
                        item_to_remove = item2
                    else:
                        item_to_keep = item2
                        item_to_remove = item1

                    # 从原始列表中移除信息量较低的项
                    try:
                        memory_items.remove(item_to_remove)
                        logger.info(
                            f"[整合] 已合并节点 '{node}' 中的记忆，保留: '{item_to_keep[:60]}...', 移除: '{item_to_remove[:60]}...'"
                        )
                        merged_count += 1
                        nodes_modified.add(node)
                        node_data["last_modified"] = current_timestamp  # 更新修改时间
                        _merged_in_this_node = True
                        break  # 每个节点每次检查只合并一对
                    except ValueError:
                        # 如果项已经被移除（例如，在之前的迭代中作为 item_to_keep），则跳过
                        logger.warning(
                            f"[整合] 尝试移除节点 '{node}' 中不存在的项 '{item_to_remove[:30]}...'，可能已被合并。"
                        )
                        continue
            # # 如果节点内发生了合并，更新节点数据 (这种方式不安全，会丢失其他属性)
            # if merged_in_this_node:
            #      self.memory_graph.G.nodes[node]["memory_items"] = memory_items

        if merged_count > 0:
            logger.info(f"[整合] 共合并了 {merged_count} 对相似记忆项，分布在 {len(nodes_modified)} 个节点中。")
            sync_start = time.time()
            logger.info("[整合] 开始将变更同步到数据库...")
            # 使用 resync 更安全地处理删除和添加
            await self.hippocampus.entorhinal_cortex.resync_memory_to_db()
            sync_end = time.time()
            logger.info(f"[整合] 数据库同步耗时: {sync_end - sync_start:.2f}秒")
        else:
            logger.info("[整合] 本次检查未发现需要合并的记忆项。")

        end_time = time.time()
        logger.info(f"[整合] 整合检查完成，总耗时: {end_time - start_time:.2f}秒")

    @staticmethod
    def _calculate_item_similarity(item1: str, item2: str) -> float:
        """计算两条记忆项文本的余弦相似度"""
        words1 = set(jieba.cut(item1))
        words2 = set(jieba.cut(item2))
        all_words = words1 | words2
        if not all_words:
            return 0.0
        v1 = [1 if word in words1 else 0 for word in all_words]
        v2 = [1 if word in words2 else 0 for word in all_words]
        return cosine_similarity(v1, v2)


class HippocampusManager:
    _instance = None
    _hippocampus = None
    _initialized = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get_hippocampus(cls):
        if not cls._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        return cls._hippocampus

    def initialize(self):
        """初始化海马体实例"""
        if self._initialized:
            return self._hippocampus

        self._hippocampus = Hippocampus()
        self._hippocampus.initialize()
        self._initialized = True

        # 输出记忆图统计信息
        memory_graph = self._hippocampus.memory_graph.G
        node_count = len(memory_graph.nodes())
        edge_count = len(memory_graph.edges())

        logger.success(f"""--------------------------------
                    记忆系统参数配置:
                    构建间隔: {global_config.memory.memory_build_interval}秒|样本数: {global_config.memory.memory_build_sample_num},长度: {global_config.memory.memory_build_sample_length}|压缩率: {global_config.memory.memory_compress_rate}
                    记忆构建分布: {global_config.memory.memory_build_distribution}
                    遗忘间隔: {global_config.memory.forget_memory_interval}秒|遗忘比例: {global_config.memory.memory_forget_percentage}|遗忘: {global_config.memory.memory_forget_time}小时之后
                    记忆图统计信息: 节点数量: {node_count}, 连接数量: {edge_count}
                    --------------------------------""")  # noqa: E501

        return self._hippocampus

    async def build_memory(self):
        """构建记忆的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        return await self._hippocampus.parahippocampal_gyrus.operation_build_memory()

    async def forget_memory(self, percentage: float = 0.005):
        """遗忘记忆的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        return await self._hippocampus.parahippocampal_gyrus.operation_forget_topic(percentage)

    async def consolidate_memory(self):
        """整合记忆的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        # 注意：目前 operation_consolidate_memory 内部直接读取配置，percentage 参数暂时无效
        # 如果需要外部控制比例，需要修改 operation_consolidate_memory
        return await self._hippocampus.parahippocampal_gyrus.operation_consolidate_memory()

    async def get_memory_from_text(
        self,
        text: str,
        max_memory_num: int = 3,
        max_memory_length: int = 2,
        max_depth: int = 3,
        fast_retrieval: bool = False,
    ) -> list:
        """从文本中获取相关记忆的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        try:
            response = await self._hippocampus.get_memory_from_text(
                text, max_memory_num, max_memory_length, max_depth, fast_retrieval
            )
        except Exception as e:
            logger.error(f"文本激活记忆失败: {e}")
            response = []
        return response

    async def get_memory_from_topic(
        self, valid_keywords: list[str], max_memory_num: int = 3, max_memory_length: int = 2, max_depth: int = 3
    ) -> list:
        """从文本中获取相关记忆的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        try:
            response = await self._hippocampus.get_memory_from_topic(
                valid_keywords, max_memory_num, max_memory_length, max_depth
            )
        except Exception as e:
            logger.error(f"文本激活记忆失败: {e}")
            response = []
        return response

    async def get_activate_from_text(self, text: str, max_depth: int = 3, fast_retrieval: bool = False) -> float:
        """从文本中获取激活值的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        try:
            response = await self._hippocampus.get_activate_from_text(text, max_depth, fast_retrieval)
        except Exception as e:
            logger.error(f"文本产生激活值失败: {e}")
            response = 0.0
        return response

    def get_memory_from_keyword(self, keyword: str, max_depth: int = 2) -> list:
        """从关键词获取相关记忆的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        return self._hippocampus.get_memory_from_keyword(keyword, max_depth)

    def get_all_node_names(self) -> list:
        """获取所有节点名称的公共接口"""
        if not self._initialized:
            raise RuntimeError("HippocampusManager 尚未初始化，请先调用 initialize 方法")
        return self._hippocampus.get_all_node_names()
