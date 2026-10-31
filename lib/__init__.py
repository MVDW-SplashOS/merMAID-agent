"""mermaidclient: standalone Python library for talking to a merMAID server.

All HTTP connection logic lives here. The CLI (`client`) and the custom-tool
helper (`client.custom_tools`) are thin consumers of this library, and
external apps can use it directly:

    from lib import MermaidClient

    client = MermaidClient()                     # reads MERMAID_HOST/PORT
    client.start_server()                        # start the daemon if needed
    task = client.add_task("Add a BuildScript for lz4")
    print(client.health())
    client.register_tool(schema, "http://127.0.0.1:9000/tools/lookup")
"""

from .client import MermaidClient, MermaidServerError

__all__ = ["MermaidClient", "MermaidServerError"]
