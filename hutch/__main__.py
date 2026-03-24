"""Hutch CLI — agent coordination server and hook handlers."""
import argparse
import logging
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="hutch", description="Agent coordination over MCP")
    sub = parser.add_subparsers(dest="command")

    # hutch serve
    serve = sub.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--host", default=os.environ.get("HUTCH_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("HUTCH_PORT", "4718")))
    serve.add_argument("--transport", default=os.environ.get("HUTCH_TRANSPORT", "streamable-http"),
                        choices=["streamable-http", "stdio", "sse"])

    # hutch hooks <subcommand>
    hooks = sub.add_parser("hooks", help="Run hook handlers")
    hooks.add_argument("hook", choices=["session-start", "pre-edit", "session-end"])

    args = parser.parse_args()

    if args.command == "serve":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        from .server import mcp
        mcp.run(transport=args.transport, host=args.host, port=args.port)

    elif args.command == "hooks":
        from .hooks import main as hooks_main
        sys.argv = ["hutch-hooks", args.hook]
        hooks_main()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
