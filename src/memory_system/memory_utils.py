# -*- coding: utf-8 -*-
"""
记忆系统工具函数
包含模糊查找、相似度计算等工具函数
"""
import json
import re
from difflib import SequenceMatcher
from typing import List, Tuple, Optional

from src.common.database.database_model import MemoryChest as MemoryChestModel
from src.common.logger import get_logger
from json_repair import repair_json


logger = get_logger("memory_utils")

def get_all_titles(exclude_locked: bool = False) -> list[str]:
    """
    获取记忆仓库中的所有标题

    Args:
        exclude_locked: 是否排除锁定的记忆，默认为 False

    Returns:
        list: 包含所有标题的列表
    """
    try:
        # 查询所有记忆记录的标题
        titles = []
        for memory in MemoryChestModel.select():
            if memory.title:
                # 如果 exclude_locked 为 True 且记忆已锁定，则跳过
                if exclude_locked and memory.locked:
                    continue
                titles.append(memory.title)
        return titles
    except Exception as e:
        print(f"获取记忆标题时出错: {e}")
        return []

def parse_md_json(json_text: str) -> list[str]:
    """从Markdown格式的内容中提取JSON对象和推理内容"""
    json_objects = []
    reasoning_content = ""

    # 使用正则表达式查找```json包裹的JSON内容
    json_pattern = r"```json\s*(.*?)\s*```"
    matches = re.findall(json_pattern, json_text, re.DOTALL)

    # 提取JSON之前的内容作为推理文本
    if matches:
        # 找到第一个```json的位置
        first_json_pos = json_text.find("```json")
        if first_json_pos > 0:
            reasoning_content = json_text[:first_json_pos].strip()
            # 清理推理内容中的注释标记
            reasoning_content = re.sub(r"^//\s*", "", reasoning_content, flags=re.MULTILINE)
            reasoning_content = reasoning_content.strip()

    for match in matches:
        try:
            # 清理可能的注释和格式问题
            json_str = re.sub(r"//.*?\n", "\n", match)  # 移除单行注释
            json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)  # 移除多行注释
            if json_str := json_str.strip():
                json_obj = json.loads(json_str)
                if isinstance(json_obj, dict):
                    json_objects.append(json_obj)
                elif isinstance(json_obj, list):
                    for item in json_obj:
                        if isinstance(item, dict):
                            json_objects.append(item)
        except Exception as e:
            logger.warning(f"解析JSON块失败: {e}, 块内容: {match[:100]}...")
            continue

    return json_objects, reasoning_content

def calculate_similarity(text1: str, text2: str) -> float:
    """
    计算两个文本的相似度
    
    Args:
        text1: 第一个文本
        text2: 第二个文本
        
    Returns:
        float: 相似度分数 (0-1)
    """
    try:
        # 预处理文本
        text1 = preprocess_text(text1)
        text2 = preprocess_text(text2)
        
        # 使用SequenceMatcher计算相似度
        similarity = SequenceMatcher(None, text1, text2).ratio()
        
        # 如果其中一个文本包含另一个，提高相似度
        if text1 in text2 or text2 in text1:
            similarity = max(similarity, 0.8)
        
        return similarity
        
    except Exception as e:
        logger.error(f"计算相似度时出错: {e}")
        return 0.0


def preprocess_text(text: str) -> str:
    """
    预处理文本，提高匹配准确性
    
    Args:
        text: 原始文本
        
    Returns:
        str: 预处理后的文本
    """
    try:
        # 转换为小写
        text = text.lower()
        
        # 移除标点符号和特殊字符
        text = re.sub(r'[^\w\s]', '', text)
        
        # 移除多余空格
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
        
    except Exception as e:
        logger.error(f"预处理文本时出错: {e}")
        return text


def fuzzy_find_memory_by_title(target_title: str, similarity_threshold: float = 0.9) -> List[Tuple[str, str, float]]:
    """
    根据标题模糊查找记忆
    
    Args:
        target_title: 目标标题
        similarity_threshold: 相似度阈值，默认0.9
        
    Returns:
        List[Tuple[str, str, float]]: 匹配的记忆列表，每个元素为(title, content, similarity_score)
    """
    try:
        # 获取所有记忆
        all_memories = MemoryChestModel.select()
        
        matches = []
        for memory in all_memories:
            similarity = calculate_similarity(target_title, memory.title)
            if similarity >= similarity_threshold:
                matches.append((memory.title, memory.content, similarity))
        
        # 按相似度降序排序
        matches.sort(key=lambda x: x[2], reverse=True)
        
        # logger.info(f"模糊查找标题 '{target_title}' 找到 {len(matches)} 个匹配项")
        return matches
        
    except Exception as e:
        logger.error(f"模糊查找记忆时出错: {e}")
        return []


def find_best_matching_memory(target_title: str, similarity_threshold: float = 0.9) -> Optional[Tuple[str, str, float]]:
    """
    查找最佳匹配的记忆
    
    Args:
        target_title: 目标标题
        similarity_threshold: 相似度阈值
        
    Returns:
        Optional[Tuple[str, str, float]]: 最佳匹配的记忆(title, content, similarity)或None
    """
    try:
        matches = fuzzy_find_memory_by_title(target_title, similarity_threshold)
        
        if matches:
            best_match = matches[0]  # 已经按相似度排序，第一个是最佳匹配
            # logger.info(f"找到最佳匹配: '{best_match[0]}' (相似度: {best_match[2]:.3f})")
            return best_match
        else:
            logger.info(f"未找到相似度 >= {similarity_threshold} 的记忆")
            return None
            
    except Exception as e:
        logger.error(f"查找最佳匹配记忆时出错: {e}")
        return None


def check_title_exists_fuzzy(target_title: str, similarity_threshold: float = 0.9) -> bool:
    """
    检查标题是否已存在（模糊匹配）
    
    Args:
        target_title: 目标标题
        similarity_threshold: 相似度阈值，默认0.9（较高阈值避免误判）
        
    Returns:
        bool: 是否存在相似标题
    """
    try:
        matches = fuzzy_find_memory_by_title(target_title, similarity_threshold)
        exists = len(matches) > 0
        
        if exists:
            logger.info(f"发现相似标题: '{matches[0][0]}' (相似度: {matches[0][2]:.3f})")
        else:
            logger.debug("未发现相似标题")
            
        return exists
        
    except Exception as e:
        logger.error(f"检查标题是否存在时出错: {e}")
        return False


def get_memories_by_chat_id_weighted(target_chat_id: str, same_chat_weight: float = 0.95, other_chat_weight: float = 0.05) -> List[Tuple[str, str, str]]:
    """
    根据chat_id进行加权抽样获取记忆列表
    
    Args:
        target_chat_id: 目标聊天ID
        same_chat_weight: 同chat_id记忆的权重，默认0.95（95%概率）
        other_chat_weight: 其他chat_id记忆的权重，默认0.05（5%概率）
        
    Returns:
        List[Tuple[str, str, str]]: 选中的记忆列表，每个元素为(title, content, chat_id)
    """
    try:
        # 获取所有记忆
        all_memories = MemoryChestModel.select()
        
        # 按chat_id分组
        same_chat_memories = []
        other_chat_memories = []
        
        for memory in all_memories:
            if memory.title and not memory.locked:  # 排除锁定的记忆
                if memory.chat_id == target_chat_id:
                    same_chat_memories.append((memory.title, memory.content, memory.chat_id))
                else:
                    other_chat_memories.append((memory.title, memory.content, memory.chat_id))
        
        # 如果没有同chat_id的记忆，返回空列表
        if not same_chat_memories:
            logger.warning(f"未找到chat_id为 '{target_chat_id}' 的记忆")
            return []
        
        # 计算抽样数量
        total_same = len(same_chat_memories)
        total_other = len(other_chat_memories)
        
        # 根据权重计算抽样数量
        if total_other > 0:
            # 计算其他chat_id记忆的抽样数量（至少1个，最多不超过总数的10%）
            other_sample_count = max(1, min(total_other, int(total_same * other_chat_weight / same_chat_weight)))
        else:
            other_sample_count = 0
        
        # 随机抽样
        selected_memories = []
        
        # 选择同chat_id的记忆（全部选择，因为权重很高）
        selected_memories.extend(same_chat_memories)
        
        # 随机选择其他chat_id的记忆
        if other_sample_count > 0 and total_other > 0:
            import random
            other_selected = random.sample(other_chat_memories, min(other_sample_count, total_other))
            selected_memories.extend(other_selected)
        
        logger.info(f"加权抽样结果: 同chat_id记忆 {len(same_chat_memories)} 条，其他chat_id记忆 {min(other_sample_count, total_other)} 条")
        
        return selected_memories
        
    except Exception as e:
        logger.error(f"按chat_id加权抽样记忆时出错: {e}")
        return []


def get_memory_titles_by_chat_id_weighted(target_chat_id: str, same_chat_weight: float = 0.95, other_chat_weight: float = 0.05) -> List[str]:
    """
    根据chat_id进行加权抽样获取记忆标题列表（用于合并选择）
    
    Args:
        target_chat_id: 目标聊天ID
        same_chat_weight: 同chat_id记忆的权重，默认0.95（95%概率）
        other_chat_weight: 其他chat_id记忆的权重，默认0.05（5%概率）
        
    Returns:
        List[str]: 选中的记忆标题列表
    """
    try:
        memories = get_memories_by_chat_id_weighted(target_chat_id, same_chat_weight, other_chat_weight)
        titles = [memory[0] for memory in memories]  # 提取标题
        return titles
        
    except Exception as e:
        logger.error(f"按chat_id加权抽样记忆标题时出错: {e}")
        return []