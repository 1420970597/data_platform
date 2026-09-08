from __future__ import annotations

import argparse

from .api import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorized, non-sensitive training data MVP")
    parser.add_argument("serve", nargs="?", default="serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dsn", default="controlled_data_mvp.sqlite3")
    args = parser.parse_args()
    serve(args.host, args.port, args.dsn)


if __name__ == "__main__":
    main()
