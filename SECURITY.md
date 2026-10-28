# Security policy

## Supported versions

Security fixes go into the latest minor release (currently 0.8.x).

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

- through GitHub's **Report a vulnerability** button on the repository's
  *Security* tab (private vulnerability reporting), or
- by email to info@openpowershift.com.

Include the version (`raptor --version`), how RAPTOR Studio was installed and
started (for the MCP server, its command line and transport), and the steps or
the model file that reproduce the problem. We aim to acknowledge a report within
five working days and to agree a disclosure date with you once a fix is ready.

## Scope

Of most interest:

- the MCP server (`raptor-studio-mcp`): escaping the workspace or the
  `--allow-read`/`--allow-write` directories, getting around the HTTP bearer
  token or its lockout, or exhausting resources past the documented limits;
- model files (`.rbd`, `.rbds`, `.rbl`) that crash the application, corrupt
  a file on save, or run code when opened;
- the in-app help viewer loading content from outside the bundled documents.

The MCP server's safety model, limits and HTTP options are described in
[docs/MCP.md](docs/MCP.md). Running the HTTP transport without a token
(`--allow-unauthenticated`) or over plain HTTP on a network address
(`--allow-insecure-http`) is an explicit opt-out of those protections, not a
vulnerability.
