from typing import List, Any, Tuple


def dyn_select_top_k(
    score: List[Tuple[Any, float]], jmp_factor: float, var_factor: float
) -> List[Tuple[Any, float, float]]:
    """动态TopK选择"""
    # 按照分数排序（降序）
    sorted_score = sorted(score, key=lambda x: x[1], reverse=True)

    # 归一化
    max_score = sorted_score[0][1]
    min_score = sorted_score[-1][1]
    normalized_score = []
    for score_item in sorted_score:
        normalized_score.append(
            tuple(
                [
                    score_item[0],
                    score_item[1],
                    (score_item[1] - min_score) / (max_score - min_score),
                ]
            )
        )

    # 寻找跳变点：score变化最大的位置
    jump_idx = 0
    for i in range(1, len(normalized_score)):
        if abs(normalized_score[i][2] - normalized_score[i - 1][2]) > abs(
            normalized_score[jump_idx][2] - normalized_score[jump_idx - 1][2]
        ):
            jump_idx = i
    # 跳变阈值
    jump_threshold = normalized_score[jump_idx][2]

    # 计算均值
    mean_score = sum([s[2] for s in normalized_score]) / len(normalized_score)
    # 计算方差
    var_score = sum([(s[2] - mean_score) ** 2 for s in normalized_score]) / len(normalized_score)

    # 动态阈值
    threshold = jmp_factor * jump_threshold + (1 - jmp_factor) * (mean_score + var_factor * var_score)

    # 重新过滤
    res = [s for s in normalized_score if s[2] > threshold]

    return res
