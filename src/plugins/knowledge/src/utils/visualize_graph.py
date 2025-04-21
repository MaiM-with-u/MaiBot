import networkx as nx
from matplotlib import pyplot as plt


def draw_graph_and_show(graph):
    """绘制图并显示，画布大小1280*1280"""
    fig = plt.figure(1, figsize=(12.8, 12.8), dpi=100)
    nx.draw_networkx(
        graph,
        node_size=100,
        width=0.5,
        with_labels=True,
        labels=nx.get_node_attributes(graph, "content"),
        font_family="Sarasa Mono SC",
        font_size=8,
    )
    fig.show()
