from typing import Tuple, List, Dict

import networkx as nx

# from .pagerank import run_personalized_pagerank
from .pagerank_py import pagerank_py


def _nx_graph_to_lists(
    graph: nx.Graph,
) -> Tuple[List[Tuple[str, str, float]], List[str]]:
    """
    Convert a NetworkX graph to lists of edges and nodes.

    Parameters
    ----------
    graph : NetworkX graph
        The input graph.

    Returns
    -------
    tuple
        A tuple containing the list of edges and the list of nodes.
    """
    nodes = [node for node in graph.nodes()]
    edges = [
        (u, v, graph.get_edge_data(u, v).get("weight", 0.0)) for u, v in graph.edges()
    ]

    return edges, nodes


def pagerank(
    graph: nx.Graph,
    personalized: None | Dict[str, float] = None,
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Dict[str, float]:
    """
    Compute the PageRank of a graph.

    Parameters
    ----------
    graph : NetworkX graph
        The input graph.
    personalized : dict, optional
        The personalization vector.
    alpha : float, optional
        The teleport probability.
    max_iter : int, optional
        The maximum number of iterations.
    tol : float, optional
        The tolerance for convergence.
    return_type : str, optional
        The return type. Can be 'numpy' or 'list'.

    Returns
    -------
    numpy.ndarray or list
        The PageRank vector.
    """
    edges, nodes = _nx_graph_to_lists(graph)

    return pagerank_py(nodes, edges, personalized, alpha, max_iter, tol)
