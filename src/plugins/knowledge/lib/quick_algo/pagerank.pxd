cdef extern from "quick_algo.h":
    struct Edge:
        long long src
        long long dst
        double weight

    double *pagerank(
        Edge *edges,
        long long num_edges,
        double *personalization,
        long long num_nodes,
        double alpha,
        int max_iter,
        double tol
    )