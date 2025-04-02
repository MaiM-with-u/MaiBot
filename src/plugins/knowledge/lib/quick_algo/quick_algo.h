#ifndef PAGERANK_H
#define PAGERANK_H

// Struct of edge
struct Edge
{
    long long src; // 边的起始节点
    long long dst; // 边的结束节点
    double weight; // 边的权重
};

double *pagerank(
    struct Edge *edges,      // 边数组
    long long num_edges,     // 边数量
    double *personalization, // 个性化向量
    long long num_nodes,     // 节点数量
    double alpha,            // 阻尼系数
    int max_iter,            // 最大迭代次数
    double tol               // 收敛阈值
);

#endif // PAGERANK_H