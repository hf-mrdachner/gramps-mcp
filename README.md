# Gramps MCP - AI-Powered Genealogy Research & Management

[![License](https://img.shields.io/badge/License-AGPL--3.0-blue)](./LICENSE) [![Python](https://img.shields.io/badge/Python-3.9+-brightgreen)](https://python.org) [![MCP](https://img.shields.io/badge/MCP-1.2.0+-orange)](https://modelcontextprotocol.io)

## Without Gramps MCP

Genealogy research with AI assistants is limited and frustrating:

- No direct access to your family tree data
- Manual data entry and research across multiple platforms
- Generic genealogy advice without context of your specific family
- No ability to automatically update or maintain your research

## With Gramps MCP

Gramps MCP provides AI assistants with direct access to your Gramps genealogy database through a comprehensive set of tools. Your AI assistant can now:

- **Smart Search**: Find people, families, events, places, and sources across your entire database
- **Data Management**: Create and update genealogy records with proper validation
- **Tree Analysis**: Trace descendants, ancestors, and family connections
- **Relationship Discovery**: Explore family connections and research gaps
- **Tree Information**: Get comprehensive tree statistics and track changes

Add Gramps MCP to your AI assistant and transform how you research family history:

```txt
Search for all descendants of John Smith born in Ireland before 1850
```

```txt
Create a new person record for Mary O'Connor with birth date 1823 in County Cork
```

```txt
Find all families missing marriage dates and suggest research priorities
```

No more manual data entry, no context switching between apps, no generic genealogy advice.

- Connect to your Gramps Web API
- Install Gramps MCP in your AI assistant
- Start intelligent genealogy research with natural language

## Features

### 16 Genealogy Tools

#### Search & Retrieval (3 tools)
- **find_type** - Universal search for any entity type (person, family, event, place, source, citation, media, repository) using Gramps Query Language
- **find_anything** - Text search across all genealogy data (matches literal text, not logical combinations)
- **get_type** - Get comprehensive information about specific persons or families by ID

#### Data Management (9 tools)
- **create_person** - Create or update person records
- **create_family** - Create or update family units
- **create_event** - Create or update life events
- **create_place** - Create or update geographic locations
- **create_source** - Create or update source documents
- **create_citation** - Create or update citations
- **create_note** - Create or update textual notes
- **create_media** - Create or update media files
- **create_repository** - Create or update repository records

#### Analysis Tools (4 tools)
- **tree_stats** - Get tree statistics and information
- **get_descendants** - Find all descendants of a person
- **get_ancestors** - Find all ancestors of a person
- **recent_changes** - Track recent modifications to your data

## Installation

### Requirements

- MCP-compatible AI assistant (Claude Desktop, Cursor, Claude Code, etc.)
- **One of the two backends** (see [Direct Backend](#direct-backend-local-file-access) below):
  - **Gramps Web server** — [Setup Guide](https://www.grampsweb.org/install_setup/setup/) — for full read/write access
  - **Local `.gpkg` / `.gramps` file** — no server or Docker required, read-only

### Quick Start

1. **Ensure Gramps Web is Running**:
   - Follow the [Gramps Web setup guide](https://www.grampsweb.org/install_setup/setup/) to get your family tree online
   - Note your Gramps Web URL, username, and password
   - Find your tree ID under System Information in your Gramps Web interface

2. **Start the Server**:

```bash
# Download the configuration
curl -O https://raw.githubusercontent.com/cabout-me/gramps-mcp/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/cabout-me/gramps-mcp/main/.env.example
cp .env.example .env
# Edit .env with your Gramps Web API credentials

# Start the server
docker-compose up -d
```

That's it! The MCP server will be running at `http://localhost:8000/mcp`

### Alternative: Run Without Docker

If you prefer to run the server directly with Python:

1. **Setup Python Environment**:
```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

2. **Run the Server**:
```bash
# HTTP transport (for web-based MCP clients)
uv run python -m src.gramps_mcp.server

# Stdio transport (for CLI-based MCP clients)
uv run python -m src.gramps_mcp.server stdio
```

The HTTP server will be available at `http://localhost:8000/mcp`, while stdio runs directly in the terminal.

### Environment Configuration

Create a `.env` file and choose **one** of the two backends:

**Web backend** (full read/write access via Gramps Web):
```bash
GRAMPS_API_URL=https://your-gramps-web-domain.com  # Without /api suffix
GRAMPS_USERNAME=your-gramps-web-username
GRAMPS_PASSWORD=your-gramps-web-password
GRAMPS_TREE_ID=your-tree-id  # System Information in Gramps Web
```

**Direct backend** (read-only, no server required):
```bash
GRAMPS_DB_PATH=/path/to/your/family-tree.gpkg  # or .gramps file
```

When `GRAMPS_DB_PATH` is set it takes precedence over the web backend variables.
The direct backend reads `.gpkg` archives (gzip-compressed Gramps XML) and plain
`.gramps` XML files using only Python's standard library — no GTK or Gramps
installation required.

## MCP Client Configuration

### Claude Desktop

Add to your Claude Desktop MCP configuration file (`claude_desktop_config.json`):

**Using Docker** (works with both pre-built and local images):
```json
{
  "mcpServers": {
    "gramps": {
      "command": "docker",
      "args": ["exec", "-i", "gramps-mcp-gramps-mcp-1", "python", "-m", "src.gramps_mcp.server", "stdio"]
    }
  }
}
```

**Using uv directly** (if running without Docker):
```json
{
  "mcpServers": {
    "gramps": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.gramps_mcp.server", "stdio"],
      "cwd": "/path/to/gramps-mcp"
    }
  }
}
```

### OpenWebUI

OpenWebUI recommends using the [mcpo proxy](https://docs.openwebui.com/openapi-servers/mcp/) to expose MCP servers as OpenAPI endpoints.

**With uv:**
```bash
uvx mcpo --port 8000 -- uv run python -m src.gramps_mcp.server stdio
```

**With Docker:**
```bash
uvx mcpo --port 8000 -- docker exec -i gramps-mcp-gramps-mcp-1 uv run python -m src.gramps_mcp.server stdio
```

### Claude Code

**HTTP Transport:**
```bash
claude mcp add --transport http gramps http://localhost:8000/mcp
```

**Stdio Transport** (direct connection, more efficient):
```bash
# Using Docker
claude mcp add --transport stdio gramps "docker exec -i gramps-mcp-gramps-mcp-1 sh -c 'cd /app && python -m src.gramps_mcp.server stdio'"

# Using uv directly (requires local setup)
claude mcp add --transport stdio gramps "uv run python -m src.gramps_mcp.server stdio"
```

> **Transport Choice:** Use **stdio** for better performance and direct integration with CLI tools like Claude Code. Use **HTTP** when you need the server to handle multiple clients or prefer web-based access.

### Other MCP Clients

For any other MCP client, use the HTTP transport endpoint:

```json
{
  "mcpServers": {
    "gramps": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Direct Backend (Local File Access)

The direct backend lets you use Gramps MCP without a running Gramps Web server.
It reads `.gpkg` or `.gramps` files directly from disk — useful on Windows, in
offline environments, or anywhere you want zero-infrastructure setup.

### Capabilities

| Feature | Direct backend | Web backend |
|---|---|---|
| Search people, families, events, places, … | Yes | Yes |
| GQL filtering (`find_type` with `gql=`) | Yes (subset) | Yes (full) |
| Ancestors / descendants traversal | Yes (BFS) | Yes (report) |
| Create / update records | No (read-only) | Yes |
| Recent changes | No | Yes |

### GQL Support

The direct backend implements the most common GQL operators:

- Property paths: `primary_name.first_name`
- Array indexing: `surname_list[0].surname`
- `.length` pseudo-property: `media_list.length > 0`
- Operators: `=` `!=` `~` `!~` `>` `>=` `<` `<=`
- Boolean (truthy check): `media_list`
- Conjunctions: `and` / `or`

Unsupported pseudo-properties (`any`, `all`, `get_person`, …) are treated as
non-matching — users see fewer results rather than incorrect ones.

### Setup

```bash
# Point the server at your .gpkg export from Gramps
export GRAMPS_DB_PATH=/path/to/family-tree.gpkg

# Run as usual — no web server, no Docker needed
uv run python -m src.gramps_mcp.server stdio
```

## Architecture

### Core Components

```
src/gramps_mcp/
|-- server.py             # MCP server with HTTP transport
|-- tools.py              # Tool registry and exports
|-- client.py             # Backend factory + Gramps Web API client
|-- direct_client.py      # Direct backend (reads .gpkg/.gramps locally)
|-- _gramps_parsers.py    # Gramps XML element parsers
|-- _gramps_db.py         # In-memory database built from parsed XML
|-- _gql.py               # GQL filter engine (subset of Gramps Query Language)
|-- models.py             # Pydantic data models
|-- auth.py               # JWT authentication
|-- config.py             # Configuration management
|-- tools/                # Modular tool implementations
|   |-- search_basic.py
|   |-- search_details.py
|   |-- data_management.py
|   |-- tree_management.py
|   `-- analysis.py
|-- handlers/             # Data formatting handlers
`-- client/               # API client modules
```

### Technology Stack

- **MCP Python SDK**: Model Context Protocol implementation
- **FastAPI**: HTTP server for MCP transport
- **Pydantic**: Data validation and serialization
- **httpx**: Async HTTP client for API communication
- **PyJWT**: JWT token authentication
- **python-dotenv**: Environment configuration


## Usage Examples

### Basic Search Operations

```txt
Find all people with the surname "Smith" born in Ireland
```

```txt
Show me recent changes to the family tree in the last 30 days
```

### Data Creation and Updates

```txt
Create a new person record for Patrick O'Brien, born 1845 in Cork, Ireland
```

```txt
Add a marriage event for John and Mary Smith on June 15, 1870 in Boston
```

### Genealogy Analysis

```txt
Find all descendants of Margaret Kelly and show their birth locations
```


### Tree Information & Statistics

```txt
Show me statistics about my family tree - how many people, families, and events
```

```txt
What recent changes have been made to my family tree in the last week?
```

## Security

- JWT token authentication with automatic refresh
- Environment-based credential management
- Input validation using Pydantic models
- Secure HTTP transport with proper error handling
- No sensitive data exposed in tool responses


## Troubleshooting

### Common Issues

**Connection refused errors**: Ensure your Gramps Web API server is running and accessible at the configured URL.

**Authentication failures**: Verify your username and password are correct and the user has appropriate permissions.

**Tool timeout errors**: Check your network connection and consider increasing timeout values for large datasets.

**Docker issues**: Ensure Docker and Docker Compose are installed and running.

### Debug Mode

To enable debug logging, check your application logs with:

```bash
docker-compose logs -f
```

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Related Projects

- [Gramps](https://gramps-project.org/) - Free genealogy software
- [Gramps Web API](https://github.com/gramps-project/gramps-web-api) - Web API for Gramps
- [Model Context Protocol](https://modelcontextprotocol.io/) - Standard for AI tool integration

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details on:

- Setting up the development environment
- Running tests and maintaining code quality
- Submitting pull requests
- Reporting issues and requesting features

### Community & Support

- **Bug Reports & Feature Requests**: [GitHub Issues](https://github.com/cabout/gramps-mcp/issues)
- **Questions & Discussions**: [GitHub Discussions](https://github.com/cabout/gramps-mcp/discussions)
- **Documentation**: [Project Wiki](https://github.com/cabout/gramps-mcp/wiki)

## Acknowledgments

- The Gramps Project team for creating excellent genealogy software
- Anthropic for developing the Model Context Protocol
- The genealogy research community for inspiration and feedback