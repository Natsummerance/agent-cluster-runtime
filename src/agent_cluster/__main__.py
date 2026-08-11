"""CLI 占位入口：``python -m agent_cluster`` 打印版本与用法。

完整 CLI（agent-cluster 命令）由后续任务（Task 7）实现。
"""

from agent_cluster import __version__


def main() -> None:
    """打印版本与用法占位。"""
    print(f"agent_cluster {__version__}")
    print("用法：后续任务将提供 agent-cluster 命令（run / skills / roles / proposals / metrics）。")


if __name__ == "__main__":
    main()
