import time

from cpython.mem cimport PyMem_Malloc, PyMem_Free

def run_personalized_pagerank(
    node_list: list[str],
    edge_list: list[tuple[str, str, float]],
    personalization: dict[str, float] = None,
    double alpha=0.85,
    int max_iter=100,
    double tol=1e-6
) -> list[tuple[str, double]]:
    cdef long long num_nodes = len(node_list)
    cdef long long num_edges = len(edge_list)
    cdef Edge* edges = <Edge*>PyMem_Malloc(num_edges * sizeof(Edge))
    cdef double* personalization_array = <double*>PyMem_Malloc(num_nodes * sizeof(double))
    cdef double* result_items
    cdef long long i

    # 映射结构：节点name到索引
    node_to_index = {node: i for i, node in enumerate(node_list)}

    # 将图的边数据转化为 C 结构
    for i, (u, v, w) in enumerate(edge_list):
        edges[i].src = node_to_index[u]
        edges[i].dst = node_to_index[v]
        edges[i].weight = w

    # 将个性化参数转化为 C 数组
    if personalization is None or len(personalization) == 0:
        for i in range(num_nodes):
            personalization_array[i] = 1.0 / num_nodes
    else:
        for node, i in node_to_index.items():
            personalization_array[i] = personalization.get(node, 0.0)

    ppr_start_time = time.perf_counter()
    # 调用C语言实现的PageRank算法
    result_items = pagerank(edges, num_edges, personalization_array, num_nodes, alpha, max_iter, tol)
    ppr_end_time = time.perf_counter()
    print(f"PageRank计算耗时: {ppr_end_time - ppr_start_time:.8f}秒")

    # 将返回结果转化为Python列表
    result_list = []
    for node, i in node_to_index.items():
        result_list.append((node, result_items[i]))

    # 释放分配的内存
    PyMem_Free(edges)
    PyMem_Free(personalization_array)
    PyMem_Free(result_items)

    return result_list