import random
from typing import List, Dict, Tuple
from src.common.logger_manager import get_logger
from src.config.config import global_config

# 这个文件现在只包含纯粹的工具函数，与状态和流程无关

logger = get_logger("nickname_utils")


def select_nicknames_for_prompt(all_nicknames_info: Dict[str, List[Dict[str, int]]]) -> List[Tuple[str, str, int]]:
    """
    从给定的绰号信息中，根据映射次数加权随机选择最多 N 个绰号用于 Prompt。

    Args:
        all_nicknames_info: 包含用户及其绰号信息的字典，格式为
                        { "用户名1": [{"绰号A": 次数}, {"绰号B": 次数}], ... }
                        注意：这里的用户名是 person_name。

    Returns:
        List[Tuple[str, str, int]]: 选中的绰号列表，每个元素为 (用户名, 绰号, 次数)。
                                    按次数降序排序。
    """
    if not all_nicknames_info:
        return []

    candidates = []  # 存储 (用户名, 绰号, 次数, 权重)
    smoothing_factor = getattr(global_config, "nickname_probability_smoothing", 1.0)  # 平滑因子，避免权重为0

    for user_name, nicknames in all_nicknames_info.items():
        if nicknames and isinstance(nicknames, list):
            for nickname_entry in nicknames:
                # 确保条目是字典且只有一个键值对
                if isinstance(nickname_entry, dict) and len(nickname_entry) == 1:
                    nickname, count = list(nickname_entry.items())[0]
                    # 确保次数是正整数
                    if isinstance(count, int) and count > 0 and isinstance(nickname, str) and nickname:
                        weight = count + smoothing_factor  # 计算权重
                        candidates.append((user_name, nickname, count, weight))
                    else:
                        logger.warning(
                            f"用户 '{user_name}' 的绰号条目无效: {nickname_entry} (次数非正整数或绰号为空)。已跳过。"
                        )
                else:
                    logger.warning(f"用户 '{user_name}' 的绰号条目格式无效: {nickname_entry}。已跳过。")

    if not candidates:
        return []

    # 确定需要选择的数量
    max_nicknames = getattr(global_config, "max_nicknames_in_prompt", 5)
    num_to_select = min(max_nicknames, len(candidates))

    try:
        # 调用加权随机抽样（不重复）
        selected_candidates_with_weight = weighted_sample_without_replacement(candidates, num_to_select)

        # 如果抽样结果数量不足（例如权重问题导致提前退出），可以考虑是否需要补充
        if len(selected_candidates_with_weight) < num_to_select:
            logger.debug(
                f"加权随机选择后数量不足 ({len(selected_candidates_with_weight)}/{num_to_select})，尝试补充选择次数最多的。"
            )
            # 筛选出未被选中的候选
            selected_ids = set(
                (c[0], c[1]) for c in selected_candidates_with_weight
            )  # 使用 (用户名, 绰号) 作为唯一标识
            remaining_candidates = [c for c in candidates if (c[0], c[1]) not in selected_ids]
            remaining_candidates.sort(key=lambda x: x[2], reverse=True)  # 按原始次数排序
            needed = num_to_select - len(selected_candidates_with_weight)
            selected_candidates_with_weight.extend(remaining_candidates[:needed])

    except Exception as e:
        # 日志：记录加权随机选择时发生的错误，并回退到简单选择
        logger.error(f"绰号加权随机选择时出错: {e}。将回退到选择次数最多的 Top N。", exc_info=True)
        # 出错时回退到选择次数最多的 N 个
        candidates.sort(key=lambda x: x[2], reverse=True)  # 按原始次数排序
        selected_candidates_with_weight = candidates[:num_to_select]

    # 格式化输出结果为 (用户名, 绰号, 次数)，移除权重
    result = [(user, nick, count) for user, nick, count, _weight in selected_candidates_with_weight]

    # 按次数降序排序最终结果
    result.sort(key=lambda x: x[2], reverse=True)

    logger.debug(f"为 Prompt 选择的绰号: {result}")
    return result


def format_nickname_prompt_injection(selected_nicknames: List[Tuple[str, str, int]]) -> str:
    """
    将选中的绰号信息格式化为注入 Prompt 的字符串。

    Args:
        selected_nicknames: 选中的绰号列表 (用户名, 绰号, 次数)。

    Returns:
        str: 格式化后的字符串，如果列表为空则返回空字符串。
    """
    if not selected_nicknames:
        return ""

    # Prompt 注入部分的标题
    prompt_lines = ["以下是聊天记录中一些成员在本群的绰号信息（按常用度排序），供你参考："]
    grouped_by_user: Dict[str, List[str]] = {}  # 用于按用户分组

    # 按用户分组绰号
    for user_name, nickname, _count in selected_nicknames:
        if user_name not in grouped_by_user:
            grouped_by_user[user_name] = []
        # 添加中文引号以区分绰号
        grouped_by_user[user_name].append(f"“{nickname}”")

    # 构建每个用户的绰号字符串
    for user_name, nicknames in grouped_by_user.items():
        nicknames_str = "、".join(nicknames)  # 使用中文顿号连接
        # 格式化输出，例如: "- 张三，ta 可能被称为：“三儿”、“张哥”"
        prompt_lines.append(f"- {user_name}，ta 可能被称为：{nicknames_str}")

    # 如果只有标题行，返回空字符串，避免注入无意义的标题
    if len(prompt_lines) > 1:
        # 末尾加换行符，以便在 Prompt 中正确分隔
        return "\n".join(prompt_lines) + "\n"
    else:
        return ""


def weighted_sample_without_replacement(
    candidates: List[Tuple[str, str, int, float]], k: int
) -> List[Tuple[str, str, int, float]]:
    """
    执行不重复的加权随机抽样。使用 A-ExpJ 算法思想的简化实现。

    Args:
        candidates: 候选列表，每个元素为 (用户名, 绰号, 次数, 权重)。
        k: 需要选择的数量。

    Returns:
        List[Tuple[str, str, int, float]]: 选中的元素列表（包含权重）。
    """
    if k <= 0:
        return []
    n = len(candidates)
    if k >= n:
        return candidates[:]  # 返回副本

    # 计算每个元素的 key = U^(1/weight)，其中 U 是 (0, 1) 之间的随机数
    # 为了数值稳定性，计算 log(key) = log(U) / weight
    # log(U) 可以用 -Exponential(1) 来生成
    weighted_keys = []
    for i in range(n):
        weight = candidates[i][3]
        if weight <= 0:
            # 处理权重为0或负数的情况，赋予一个极小的概率（或极大负数的log_key）
            log_key = float("-inf")  # 或者一个非常大的负数
            logger.warning(f"候选者 {candidates[i][:2]} 的权重为非正数 ({weight})，抽中概率极低。")
        else:
            log_u = -random.expovariate(1.0)  # 生成 -Exponential(1) 随机数
            log_key = log_u / weight
        weighted_keys.append((log_key, i))  # 存储 (log_key, 原始索引)

    # 按 log_key 降序排序 (相当于按 key 升序排序)
    weighted_keys.sort(key=lambda x: x[0], reverse=True)

    # 选择 log_key 最大的 k 个元素的原始索引
    selected_indices = [index for _log_key, index in weighted_keys[:k]]

    # 根据选中的索引从原始 candidates 列表中获取元素
    selected_items = [candidates[i] for i in selected_indices]

    return selected_items


# 移除旧的流程函数
# get_nickname_injection_for_prompt 和 trigger_nickname_analysis_if_needed
# 的逻辑现在由 NicknameManager 处理
