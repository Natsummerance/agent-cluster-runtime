"""CLI 入口：``python -m agent_cluster`` 等价于 ``agent-cluster`` 命令。"""

import sys

from agent_cluster.cli import main

if __name__ == "__main__":
    sys.exit(main())