# raptor-studio-mcp

The [Model Context Protocol](https://modelcontextprotocol.io) server for
[RAPTOR Studio](https://github.com/openpowershift/RaptorStudio), an open-source
reliability block diagram simulator. It lets an AI assistant build, validate,
simulate and compare RBD models, and read the results.

This package holds no code of its own: it installs `raptor-studio[mcp]` at the
same version and provides the `raptor-studio-mcp` command, so an MCP client
can start the server with

```bash
uvx raptor-studio-mcp --workspace ~/raptor-models
```

Installing `pip install "raptor-studio[mcp]"` gives the same command along with
the desktop application. The tools, resources, safety limits and the HTTP
transport are described in
[docs/MCP.md](https://github.com/openpowershift/RaptorStudio/blob/main/docs/MCP.md).

<!-- mcp-name: io.github.OpenPowerShift/raptor-studio -->
