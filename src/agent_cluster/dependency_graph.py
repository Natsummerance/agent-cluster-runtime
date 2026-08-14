from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

__all__ = [
    "CycleError",
    "DependencyEdge",
    "DependencyGraph",
]


class CycleError(RuntimeError):
    """依赖环（fail loud；serve 层映射 409 cycle_detected）。"""


@dataclass
class DependencyEdge:
    """跨项目依赖边：from_project 依赖 to_project。"""

    id: str
    from_project: str
    to_project: str
    from_task: str = ""
    to_task: str = ""
    type: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class DependencyGraph:
    """跨项目依赖图（进程内记录）。"""

    def __init__(self, root: str | Path | None = None, edges: Iterable[DependencyEdge] | None = None) -> None:
        self._root = Path(root).expanduser().resolve() if root is not None else Path.home() / ".agent-cluster"
        self._edges: dict[str, DependencyEdge] = {}
        # 注入边用于恢复/内部构造路径（add_edge 之外的唯一入口，不重复做环校验——
        # schedule_order 仍会 fail loud 兜底）
        if edges:
            for edge in edges:
                self._edges[edge.id] = edge

    # ------------------------------------------------------------------
    # CRUD（fail loud）
    # ------------------------------------------------------------------

    def add_edge(
        self,
        *,
        from_project: str,
        to_project: str,
        from_task: str = "",
        to_task: str = "",
        type: str = "",
    ) -> DependencyEdge:
        """新建依赖边；校验端点并做环检测（成环抛 CycleError 且不写入）。"""
        from_project = from_project.strip()
        to_project = to_project.strip()
        if not from_project:
            raise ValueError("from_project 不能为空")
        if not to_project:
            raise ValueError("to_project 不能为空")
        if from_project == to_project:
            raise CycleError(f"依赖环：{from_project} -> {to_project}（自环）")
        for existing in self._edges.values():
            if (
                existing.from_project == from_project
                and existing.to_project == to_project
                and existing.from_task == (from_task or "")
                and existing.to_task == (to_task or "")
                and existing.type == (type or "")
            ):
                raise ValueError(
                    f"依赖边已存在：{from_project} -> {to_project}"
                    + (f"（{existing.type}）" if existing.type else "")
                )
        # 环检测：to 能到达 from 则新增边成环；给出环路径说明
        cycle_path = self._find_path(to_project, from_project)
        if cycle_path is not None:
            path_text = " -> ".join([from_project, *cycle_path])
            raise CycleError(f"依赖环：{path_text}")
        edge = DependencyEdge(
            id=uuid.uuid4().hex[:12],
            from_project=from_project,
            to_project=to_project,
            from_task=from_task or "",
            to_task=to_task or "",
            type=type or "",
        )
        self._edges[edge.id] = edge
        return edge

    def get_edge(self, edge_id: str) -> DependencyEdge:
        if edge_id not in self._edges:
            raise KeyError(f"未找到依赖边：{edge_id!r}")
        return self._edges[edge_id]

    def list_edges(self) -> list[DependencyEdge]:
        """列出全部依赖边（按写入顺序）。"""
        return list(self._edges.values())

    def remove_edge(self, edge_id: str) -> None:
        """删除依赖边；缺失抛 KeyError（fail loud）。"""
        self.get_edge(edge_id)
        del self._edges[edge_id]

    # ------------------------------------------------------------------
    # 影响分析：BFS 下游闭包
    # ------------------------------------------------------------------

    def impact_of(self, project_id: str) -> set[str]:
        """返回受影响项目集合：直接/传递依赖该项目的下游项目（BFS 闭包）。"""
        downstream: set[str] = set()
        queue = [project_id]
        while queue:
            current = queue.pop(0)
            for edge in self._edges.values():
                if edge.to_project == current and edge.from_project not in downstream:
                    downstream.add(edge.from_project)
                    queue.append(edge.from_project)
        return downstream

    # ------------------------------------------------------------------
    # 依赖感知调度：拓扑序（依赖先行）
    # ------------------------------------------------------------------

    def schedule_order(self) -> list[str]:
        """返回拓扑序：每个依赖边中 to_project（依赖方）先于 from_project（依赖者）。

        确定性：同层按项目名升序；遇环抛 CycleError 并给出环路径。
        """
        indegree: dict[str, int] = {}
        dependents: dict[str, set[str]] = {}
        for edge in self._edges.values():
            indegree[edge.from_project] = indegree.get(edge.from_project, 0) + 1
            indegree.setdefault(edge.to_project, 0)
            dependents.setdefault(edge.to_project, set()).add(edge.from_project)
        # 循环中可变的入度（Kahn）
        work = {node: degree for node, degree in indegree.items()}
        ready = sorted(node for node, degree in work.items() if degree == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for dependent in sorted(dependents.get(node, ())):
                work[dependent] -= 1
                if work[dependent] == 0:
                    ready.append(dependent)
                    ready.sort()
        if len(order) != len(indegree):
            remaining = [node for node in indegree if node not in order]
            cycle = self._find_cycle(remaining)
            if cycle is not None:
                raise CycleError(f"依赖环：{' -> '.join([*cycle, cycle[0]])}")
            raise CycleError(f"依赖环：{' -> '.join(remaining)}")
        return order

    # ------------------------------------------------------------------
    # 内部：路径/环查找（BFS 沿 from -> to 方向）
    # ------------------------------------------------------------------

    def _find_path(self, start: str, target: str) -> list[str] | None:
        """沿边方向（from -> to）从 start 到 target 的路径；不存在返回 None。"""
        if start == target:
            return [start]
        queue: list[tuple[str, list[str]]] = [(start, [start])]
        visited: set[str] = set()
        while queue:
            node, path = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for edge in self._edges.values():
                if edge.from_project == node:
                    next_node = edge.to_project
                    if next_node == target:
                        return [*path, next_node]
                    if next_node not in visited:
                        queue.append((next_node, [*path, next_node]))
        return None

    def _find_cycle(self, nodes: Iterable[str]) -> list[str] | None:
        """在给定节点集合内找一个环（节点序列，首尾相连）。"""
        nodes = set(nodes)
        for start in sorted(nodes):
            stack: list[tuple[str, list[str]]] = [(start, [start])]
            seen: set[str] = set()
            while stack:
                node, path = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                for edge in self._edges.values():
                    if edge.from_project == node and edge.to_project in nodes:
                        if edge.to_project == start:
                            return path
                        stack.append((edge.to_project, [*path, edge.to_project]))
        return None
