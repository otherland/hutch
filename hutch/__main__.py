"""Run the Hutch MCP server."""
import logging, os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from .server import mcp  # noqa: E402


def main():
    host = os.environ.get("HUTCH_HOST", "127.0.0.1")
    port = int(os.environ.get("HUTCH_PORT", "8765"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
