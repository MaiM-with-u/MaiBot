#define __USE_MINGW_ANSI_STDIO 1
#include "stdio.h"
#include "malloc.h"
#include "string.h"
#include "math.h"
#include "immintrin.h"
#include "quick_algo.h"

// 以下头文件用于多线程优化
#include "omp.h"

// Comparison function for qsort
int compare_edges(const void *a, const void *b)
{
    struct Edge *edge_a = (struct Edge *)a;
    struct Edge *edge_b = (struct Edge *)b;
    // 异或操作符用于比较两个边的起始节点和结束节点
    if (edge_a->src ^ edge_b->src)
        return edge_a->src - edge_b->src;
    else
        return edge_a->dst - edge_b->dst;
}

/**
 * 个性化PageRank算法
 */
double *pagerank(
    struct Edge *edges,      // 边数组
    long long num_edges,     // 边数量
    double *personalization, // 个性化向量
    long long num_nodes,     // 节点数量
    double alpha,            // 阻尼系数
    int max_iter,            // 最大迭代次数
    double tol               // 收敛阈值
)
{
    int num_threads = omp_get_max_threads(); // 获取最大线程数

    // 重新排列边顺序，按照先起始节点，后结束节点的顺序排列
    // 该操作将相同源点的边放在一起，减少跨越内存页的访问
    qsort(edges, num_edges, sizeof(struct Edge), compare_edges);

    {
        // 将同源边根据权重转化为概率分布
        // 转化后，同源边的权重之和为1
        double sum_weight;
        long long now_src;
        for (long long i_start = 0; i_start < num_edges; i_start++)
        {
            now_src = edges[i_start].src;       // 当前源点
            sum_weight = edges[i_start].weight; // 初始化权重之和为当前边的权重
            // 寻找其它同源边
            for (long long i_end = i_start + 1; i_end <= num_edges; i_end++)
            {
                if (i_end == num_edges || edges[i_end].src != now_src)
                {
                    // 若结束指针指向了不同的源点，或者已经到达了最后一条边
                    // 则将区间内的边的权重进行归一化
                    // 归一化后的权重为：weight[i] = weight[i] / sum_weight
                    for (long long i = i_start; i < i_end; i++)
                    {
                        edges[i].weight /= sum_weight;
                    }
                    // 更新起始指针到结束指针
                    i_start = i_end;
                }
                else
                {
                    // 否则，继续累加权重
                    sum_weight += edges[i_end].weight;
                }
            }
        }
    }

    {
        // 个性化向量归一化
        double max_value = 0.0L;
        double min_value = 1.0L;
        for (long long i = 0; i < num_nodes; i++)
        {
            if (personalization[i] > max_value)
            {
                max_value = personalization[i];
            }
            if (personalization[i] < min_value)
            {
                min_value = personalization[i];
            }
        }
        if (max_value == min_value)
        {
            // 如果所有值相同，则将所有值设置为1.0/num_nodes
            for (long long i = 0; i < num_nodes; i++)
            {
                personalization[i] = 1.0L / num_nodes;
            }
        }
        else
        {
            for (long long i = 0; i < num_nodes; i++)
            {
                personalization[i] = (personalization[i] - min_value) / (max_value - min_value);
            }
        }
    }

    // 初始化Score向量
    double *score = (double *)calloc(num_nodes, sizeof(double)); // 初始化Score向量为0
    for (long long i = 0; i < num_nodes; i++)
    {
        score[i] = personalization[i];
    }

    // 迭代计算PageRank
    // 对于每轮迭代：
    // 1. 计算新的Score向量：即新的Score[i] = (1 - alpha) * personalization[i] + alpha * sum(Score[j] / weight[j])
    //    其中，j是所有指向i的节点，weight[j]是边的权重
    // 2. 检查收敛条件
    // 3. 更新Score向量
    double *tmp_score = (double *)malloc(4 * sizeof(double)); // 临时Score向量

    for (int iter = 0; iter < max_iter; iter++)
    {
        double *new_score = (double *)calloc(num_nodes, sizeof(double)); // 初始化新Score向量为0

        // 多线程优化
        // 将边初始化过程交给多个线程
        // 原始算法：
        // for (long long i = 0; i < num_nodes; i++)
        //     new_score[i] = (1 - alpha) * personalization[i];
        // 这里使用了OpenMP的并行化方法

#pragma omp parallel for
        for (long long i = 0; i < num_nodes; i++)
        {
            new_score[i] = (1 - alpha) * personalization[i];
        }

        // 计算新的Score向量
        // 原始算法：
        // for (long long i = 0; i < num_edges; i++)
        //     new_score[edges[i].dst] += alpha * score[edges[i].src] * edges[i].weight;
        // 这里应用SIMD指令进行向量化计算
        {
            long long i;
            __m256d alpha_val = _mm256_set1_pd(alpha);

            for (i = 0; i < num_edges - 3; i += 4)
            {
                // 使用SIMD指令进行向量化计算
                __m256d src_score = _mm256_set_pd(score[edges[i].src], score[edges[i + 1].src], score[edges[i + 2].src], score[edges[i + 3].src]);
                __m256d weights = _mm256_set_pd(edges[i].weight, edges[i + 1].weight, edges[i + 2].weight, edges[i + 3].weight);
                __m256d new_score_val = _mm256_mul_pd(alpha_val, src_score);
                new_score_val = _mm256_mul_pd(new_score_val, weights);
                // 将结果存储到临时变量中
                _mm256_store_pd(tmp_score, new_score_val); // 存储结果

                // 更新新Score向量
                for (int j = 0; j < 4; j++)
                {
                    // tmp_score里的数据是反向存储的
                    new_score[edges[i + j].dst] += tmp_score[3 - j];
                }
            }
            // 处理剩余的边
            for (; i < num_edges; i++)
            {
                new_score[edges[i].dst] += alpha * score[edges[i].src] * edges[i].weight;
            }
        }

        // 检查收敛
        double diff = 0.0L;
        for (long long i = 0; i < num_nodes; i++)
        {
            diff += fabs(new_score[i] - score[i]);
        }

        // 更新Score向量
        free(score);
        score = new_score;

        if (diff < tol)
            break;
    }

    // 释放临时Score向量
    free(tmp_score);

    return score;
}

int main()
{
    // 测试代码
    struct Edge edges[] = {
        {0, 1, 0.5},
        {1, 2, 0.3},
        {2, 0, 0.2},
        {1, 3, 0.4},
        {3, 4, 0.6},
        {4, 1, 0.7}};
    long long num_edges = sizeof(edges) / sizeof(edges[0]);
    double personalization[] = {1.0, 2.0, 3.0, 4.0, 5.0};
    long long num_nodes = sizeof(personalization) / sizeof(personalization[0]);
    double alpha = 0.85;
    int max_iter = 100;
    double tol = 1e-6;

    double *result = pagerank(edges, num_edges, personalization, num_nodes, alpha, max_iter, tol);

    for (long long i = 0; i < num_nodes; i++)
    {
        printf("Node %lld: %f\n", i + 1, result[i]);
    }

    free(result);
    return 0;
}