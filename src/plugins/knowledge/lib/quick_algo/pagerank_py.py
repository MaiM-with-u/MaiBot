import numpy as np
import scipy.sparse as sp
from typing import List, Tuple, Dict, Optional


def pagerank_py(
    nodes: List[str],
    edges: List[Tuple[str, str, float]],
    personalization: Optional[Dict[str, float]] = None,
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Dict[str, float]:
    """使用 Python、NumPy 和 SciPy 计算个性化 PageRank。

    Args:
        nodes: 节点标识符列表。
        edges: 边列表，其中每条边是一个元组 (source_node, target_node, weight)。
               注意：权重在此实现中用于确定出度，但标准 PageRank 转移概率为 1/out_degree。
        personalization: 将节点标识符映射到其个性化分数的字典。
                           如果为 None 或为空，则使用统一的个性化设置。
        alpha: 阻尼因子（瞬移概率为 1 - alpha）。
        max_iter: 最大迭代次数。
        tol: 收敛容差。如果迭代之间的分数差异的 L1 范数小于 tol，则停止迭代。

    Returns:
        一个将节点标识符映射到其 PageRank 分数的字典。
    """
    num_nodes = len(nodes)
    if num_nodes == 0:
        return {}

    node_to_index = {node: i for i, node in enumerate(nodes)}
    index_to_node = {i: node for i, node in enumerate(nodes)}

    # --- 个性化向量归一化 ---
    personalization_vec = np.zeros(num_nodes, dtype=np.float64)
    if personalization is None or not personalization:
        # 默认：均匀分布
        personalization_vec.fill(1.0 / num_nodes)
    else:
        raw_values = np.array([personalization.get(node, 0.0) for node in nodes], dtype=np.float64)
        # 确保值非负
        raw_values = np.maximum(raw_values, 0)
        norm_sum = np.sum(raw_values)

        if norm_sum > 1e-9:  # 避免除以零
            personalization_vec = raw_values / norm_sum
        else:
            # 如果所有提供的个性化值都为零或负数，则回退到均匀分布
            print("警告：个性化值总和为零或所有值均为非正数。回退到均匀个性化设置。")
            personalization_vec.fill(1.0 / num_nodes)

    # --- 构建稀疏邻接矩阵 ---
    # 标准 PageRank 需要基于出度的归一化
    row_ind = []
    col_ind = []
    data = []
    out_degree = {i: 0.0 for i in range(num_nodes)}
    for u, v in edges:
        src_idx = node_to_index.get(u)
        dst_idx = node_to_index.get(v)
        if src_idx is not None and dst_idx is not None:
            # 仅存储连接信息，权重稍后根据出度计算
            row_ind.append(dst_idx)
            col_ind.append(src_idx)
            # 暂存原始权重，如果需要加权 PageRank，可以在此使用 w
            # 对于标准 PageRank，我们只需要知道连接存在
            data.append(1.0)  # 初始数据设为 1，之后归一化
            # 标准 PageRank 的出度是边的数量，加权 PageRank 可以用 w
            out_degree[src_idx] += 1

    # 归一化权重（构建转移矩阵 M 的转置 M.T）
    # M[j, i] 是从 i 到 j 的概率
    # 我们构建 M.T，其中 M.T[i, j] 是从 i 到 j 的概率
    # 这样可以直接与 scores 列向量相乘: M.T @ scores
    normalized_data = []
    new_row_ind = []
    new_col_ind = []
    for r, c, d in zip(row_ind, col_ind, data):
        # r = dst_idx, c = src_idx
        if out_degree[c] > 0:
            # 标准 PageRank: 1.0 / out_degree[c]
            # 如果要用原始权重 w 作为转移概率（需确保它们已归一化），则用 w / sum(w for edges from c)
            normalized_data.append(d / out_degree[c])
            new_row_ind.append(c)  # M.T 的行索引是 src_idx
            new_col_ind.append(r)  # M.T 的列索引是 dst_idx

    # 创建稀疏矩阵 (M.T)
    # 注意：scipy.sparse 期望 (data, (row_ind, col_ind)) 格式
    # 这里构建的是 M 的转置，方便后续计算 scores = alpha * M.T @ scores + ...
    if len(normalized_data) > 0:
        # 使用 csc_matrix 以便高效地进行列操作（矩阵向量乘法）
        M_T = sp.csc_matrix((normalized_data, (new_row_ind, new_col_ind)), shape=(num_nodes, num_nodes))
    else:
        M_T = sp.csc_matrix((num_nodes, num_nodes))

    # 识别悬挂节点 (没有出链的节点)
    dangling_weights = np.zeros(num_nodes, dtype=np.float64)
    is_dangling = np.ones(num_nodes, dtype=bool)
    # 有出链的节点不是悬挂节点
    is_dangling[np.unique(new_row_ind)] = False
    # 将悬挂节点的权重设置为个性化向量（或均匀分布，取决于 PageRank 变体）
    # 标准做法是将悬挂节点的 PageRank 质量均匀或按个性化向量分布到所有节点
    dangling_weights[is_dangling] = personalization_vec[is_dangling]
    # 另一种常见做法是均匀分配给所有节点：
    # dangling_weights[is_dangling] = 1.0 / num_nodes
    # 还有一种做法是仅分配给个性化向量中非零的节点

    # --- PageRank 迭代 ---
    scores = personalization_vec.copy()  # 从个性化向量开始

    for iteration in range(max_iter):
        prev_scores = scores.copy()

        # 计算来自链接的贡献
        linked_scores = M_T @ scores

        # 计算来自悬挂节点的贡献
        # 悬挂节点的总分数 * 悬挂权重向量
        dangling_sum = np.sum(scores[is_dangling])
        dangling_contribution = dangling_sum * dangling_weights

        # 结合瞬移、链接贡献和悬挂节点贡献
        scores = alpha * (linked_scores + dangling_contribution) + (1 - alpha) * personalization_vec

        # 检查收敛性 (L1 范数)
        diff = np.sum(np.abs(scores - prev_scores))
        if diff < tol:
            print(f"在 {iteration + 1} 次迭代后收敛。")
            break
    else:  # 循环完成但未中断
        print(f"达到最大迭代次数 ({max_iter}) 但未收敛。")

    # --- 格式化输出 ---
    result_dict = {index_to_node[i]: scores[i] for i in range(num_nodes)}
    return result_dict


# --- 示例用法（类似于 pr.c 中的 main）---
if __name__ == "__main__":
    nodes_test = ["0", "1", "2", "3", "4"]
    edges_test = [
        ("0", "1", 0.5),  # 权重在此实现中仅用于确定出度
        ("1", "2", 0.3),
        ("2", "0", 0.2),
        ("1", "3", 0.4),
        ("3", "4", 0.6),
        ("4", "1", 0.7),
    ]
    # 添加一个悬挂节点示例
    nodes_test.append("5")
    edges_test.append(("0", "5", 0.1))
    # 节点 "5" 没有出链

    personalization_test = {"0": 1.0, "1": 2.0, "2": 3.0, "3": 4.0, "4": 5.0, "5": 0.1}
    num_nodes_test = len(nodes_test)
    alpha_test = 0.85
    max_iter_test = 100
    tol_test = 1e-6

    print("运行优化的 Python PageRank 实现...")
    result = pagerank_py(
        nodes_test, edges_test, personalization_test, alpha=alpha_test, max_iter=max_iter_test, tol=tol_test
    )

    print("\nPageRank 分数:")
    # 按节点索引排序以获得一致的输出
    sorted_nodes = sorted(result.keys(), key=lambda x: int(x))
    for node_id in sorted_nodes:
        print(f"节点 {node_id}: {result[node_id]:.6f}")

    print("\n使用默认个性化设置运行...")
    result_default_pers = pagerank_py(
        nodes_test,
        edges_test,
        personalization=None,  # 使用默认的统一性化设置
        alpha=alpha_test,
        max_iter=max_iter_test,
        tol=tol_test,
    )
    print("\nPageRank 分数（默认个性化）:")
    sorted_nodes_default = sorted(result_default_pers.keys(), key=lambda x: int(x))
    for node_id in sorted_nodes_default:
        print(f"节点 {node_id}: {result_default_pers[node_id]:.6f}")

    # 与 NetworkX 对比 (如果安装了)
    try:
        import networkx as nx

        print("\n与 NetworkX PageRank 对比 (个性化)...")
        G = nx.DiGraph()
        G.add_nodes_from(nodes_test)
        # NetworkX PageRank 使用权重作为转移概率的一部分，如果提供了权重
        # 但标准 PageRank 通常不直接使用边权重，而是 1/out_degree
        # 为了更接近我们的实现，我们不传递权重给 add_edges_from
        edges_for_nx = [(u, v) for u, v, w in edges_test]
        G.add_edges_from(edges_for_nx)

        # 归一化 NetworkX 的个性化向量
        nx_pers = {node: personalization_test.get(node, 0.0) for node in nodes_test}
        pers_sum = sum(nx_pers.values())
        if pers_sum > 0:
            nx_pers = {k: v / pers_sum for k, v in nx_pers.items()}
        else:  # 如果全为0，NetworkX 会报错或行为未定义，我们设为 None
            nx_pers = None

        nx_result = nx.pagerank(
            G, alpha=alpha_test, personalization=nx_pers, max_iter=max_iter_test, tol=tol_test, weight=None
        )  # weight=None 强制标准 PageRank
        for node_id in sorted_nodes:
            print(f"节点 {node_id}: {nx_result.get(node_id, 0.0):.6f}")

        print("\n与 NetworkX PageRank 对比 (默认)...")
        nx_result_default = nx.pagerank(
            G, alpha=alpha_test, personalization=None, max_iter=max_iter_test, tol=tol_test, weight=None
        )
        for node_id in sorted_nodes_default:
            print(f"节点 {node_id}: {nx_result_default.get(node_id, 0.0):.6f}")

    except ImportError:
        print("\n未安装 NetworkX，跳过对比。")
    except Exception as e:
        print(f"\n运行 NetworkX PageRank 时出错: {e}")
