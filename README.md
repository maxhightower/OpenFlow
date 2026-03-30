# Chloe

[![CI](https://github.com/maxhightower/OpenFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/maxhightower/OpenFlow/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Resource optimization scheduler for Claude Code subscriptions.

Chloe analyzes your Claude Code usage patterns and helps you optimize token consumption, schedule work efficiently, and maximize the value of your subscription.

## Features

- **Observer** — Parses `~/.claude/projects/**/*.jsonl` usage logs, stores data in SQLite, and generates Rich-formatted baseline reports (peak hours, burn rate, cost projections, top projects)
- **Graph** — DAG dependency engine using NetworkX with terminal visualization
- **Scheduler** — Critical path + bin-packing optimizer using OR-Tools CP-SAT solver
- **MCP Server** — Lets Claude Code call Chloe directly as an MCP tool
- **Dispatcher** — Executes tasks via the `claude` CLI with output capture and token tracking
- **CLI** — Terminal UI built with Rich and Typer (10 commands)

## Installation

```bash
# Clone and install (core features)
git clone https://github.com/maxhightower/OpenFlow.git
cd OpenFlow
uv sync

# Include the OR-Tools constraint solver for optimal scheduling
uv sync --extra solver
```

## Quick Start

```bash
# Scan usage logs and print a baseline report
chloe observe

# Show live burn rate (refreshes every 30s)
chloe status

# Visualize a task dependency graph
chloe graph --file tasks.json

# Check your token budget window
chloe budget

# Get token cost estimates by task type
chloe estimate --type feature

# View the optimized execution schedule
chloe schedule --file tasks.json

# Execute the next scheduled task via claude CLI
chloe run --file tasks.json

# Add a task to the DAG
chloe queue --file tasks.json --name "Add auth middleware" --type feature

# View run history
chloe history
```

## MCP Server Setup

Chloe includes an MCP server that lets Claude Code query your budget, schedule, and execute tasks directly.

### Configure in Claude Code

Add the following to your Claude Code MCP settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "chloe": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/OpenFlow", "python", "-m", "chloe.mcp"],
      "env": {}
    }
  }
}
```

Or start the server manually:

```bash
chloe mcp-serve --file tasks.json --budget 500000
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `get_budget_status` | Current token budget window status |
| `get_schedule` | Optimized execution plan for the DAG |
| `get_next_task` | Next task to execute based on priority and dependencies |
| `queue_task` | Add a new task to the DAG |
| `run_next_task` | Execute the next task via `claude` CLI |
| `mark_task_done` | Manually mark a task as completed |
| `get_task_status` | Status and run history for a specific task |
| `list_runs` | Recent task execution history |
| `optimize_schedule` | Run OR-Tools CP-SAT solver for optimal scheduling |

## Task DAG Format

Tasks are defined in a JSON file. Example `tasks.json`:

```json
{
  "name": "My Project",
  "tasks": [
    {
      "id": "T1",
      "name": "Set up database schema",
      "task_type": "feature",
      "estimated_hours": 2.0,
      "priority": 1,
      "status": "done",
      "dependencies": []
    },
    {
      "id": "T2",
      "name": "Build API endpoints",
      "task_type": "feature",
      "estimated_hours": 3.0,
      "priority": 2,
      "status": "pending",
      "dependencies": ["T1"]
    },
    {
      "id": "T3",
      "name": "Write integration tests",
      "task_type": "test",
      "estimated_hours": 1.5,
      "priority": 3,
      "status": "pending",
      "dependencies": ["T2"]
    }
  ]
}
```

### Task Types

`feature`, `bug-fix`, `refactor`, `test`, `docs`, `release`

### Task Statuses

`pending`, `blocked`, `in-progress`, `done`

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run with verbose output
uv run pytest -v
```

## Project Structure

```
chloe/
├── observer/       # Parses ~/.claude/**/*.jsonl usage logs
├── graph/          # DAG dependency engine using networkx
├── scheduler/      # Critical path + bin-packing optimizer (OR-Tools CP-SAT)
├── dispatcher/     # Executes tasks via claude CLI
├── mcp/            # MCP server so Claude Code can call Chloe
├── cli/            # Terminal UI using Rich + Typer
└── tests/          # Tests for each module
```

## License

[MIT](LICENSE)
