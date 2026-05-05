"""
图算法
"""

from typing import Any


def VertexElimination(graph: set[tuple[Any, Any]], elimination: set[Any]):
    """
    删除一个图中的一些顶点, 并保持拓扑

    graph : 有向图, 每个元素形如(a,b), 表示a->b的有向边
    elimination : 需要删除的顶点

    TODO 未审阅
    """

    # 拷贝一份
    new_graph = set(graph)

    # 提取所有顶点
    all_vertices = set()
    for u, v in graph:
        all_vertices.add(u)
        all_vertices.add(v)

    # 确保 elimination 中的顶点在图中存在（至少作为节点）
    elim = elimination & all_vertices  # 交集，防止无效操作

    # 存储每个节点的入边和出边（使用集合）
    incoming = {v: set() for v in all_vertices}
    outgoing = {v: set() for v in all_vertices}

    for u, v in graph:
        outgoing[u].add(v)
        incoming[v].add(u)

    # 逐个删除 elimination 中的顶点
    for v in elim:
        # 获取前驱和后继
        preds = incoming[v]
        succs = outgoing[v]

        # 添加绕过边：pred -> succ
        for u in preds:
            for w in succs:
                if u != w:  # 可选：避免自环
                    new_graph.add((u, w))

        # 移除所有与 v 相关的边
        # 先收集需要移除的边
        edges_to_remove = set()
        for u in incoming[v]:
            edges_to_remove.add((u, v))
        for w in outgoing[v]:
            edges_to_remove.add((v, w))
        # 特别地，如果图中存在自环 (v, v)，也应删除
        if (v, v) in new_graph:
            edges_to_remove.add((v, v))

        new_graph -= edges_to_remove

    return new_graph
