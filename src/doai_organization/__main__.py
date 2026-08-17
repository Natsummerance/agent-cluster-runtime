from __future__ import annotations

import asyncio

from .rpc import serve_stdio


def main() -> None:
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()
