# demo/ui/mcpserver/providers/echo_mcp.py
from mcp.server.fastmcp import FastMCP

app = FastMCP(
    "EchoMCP",
    version="0.1.0",
    description="A minimal MCP server with echo and add tools."
)

@app.tool()
def echo(text: str) -> str:
    """Echo back whatever you send in."""
    return text

@app.tool()
def add(a: float, b: float) -> float:
    """Return a + b."""
    return a + b

if __name__ == "__main__":
    # Run over stdio (Naga 会用 stdio 连接这个 MCP 服务)
    app.run_stdio()
