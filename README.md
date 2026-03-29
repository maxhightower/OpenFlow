# ClaudeFlow

Resource optimization scheduler for Claude Code subscriptions.

ClaudeFlow analyzes your Claude Code usage patterns and helps you optimize token consumption, schedule work efficiently, and maximize the value of your subscription.

## Features

- **Observer** — Parses `~/.claude/projects/**/*.jsonl` usage logs, stores data in SQLite, and generates Rich-formatted baseline reports (peak hours, burn rate, cost projections, top projects)
- **Graph** — DAG dependency engine using NetworkX (coming soon)
- **Scheduler** — Critical path + bin-packing optimizer using OR-Tools (coming soon)
- **MCP Server** — Lets Claude Code call ClaudeFlow directly (coming soon)
- **CLI** — Terminal UI built with Rich and Typer

## Installation

```bash
uv sync
```

## Usage

```bash
# Scan usage logs and print a baseline report
claudeflow observe

# Show live burn rate (refreshes every 30s)
claudeflow status

# Custom .claude directory
claudeflow observe --claude-dir /path/to/.claude
```

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest
```

## Project Structure

```
claude_flow/
├── observer/       # Parses ~/.claude/**/*.jsonl usage logs
├── graph/          # DAG dependency engine using networkx
├── scheduler/      # Critical path + bin-packing optimizer
├── mcp/            # MCP server so Claude Code can call ClaudeFlow
├── cli/            # Terminal UI using Rich
└── tests/          # Tests for each module
```
