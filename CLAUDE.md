# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GnuCash MCP Server - An MCP (Model Context Protocol) server that provides read/write access to GnuCash financial data via direct SQLite access. Uses WAL mode for concurrent access — the GnuCash GUI can stay open while the MCP server reads and writes.

## Development Setup

```bash
# Any Python 3 works — no GnuCash bindings needed
python3 -m venv .venv
source .venv/bin/activate
pip install mcp
```

## Running the Server

```bash
# GNUCASH_FILE must point to a SQLite-format .gnucash file
GNUCASH_FILE=/path/to/file.gnucash python3 server.py
```

All tools (read and write) are always available — no `--write` flag needed.

## Architecture

Single-file MCP server (`server.py`) using FastMCP framework with Python's built-in `sqlite3` module:

- **Direct SQLite**: No GnuCash Python bindings — uses `sqlite3` module directly
- **WAL mode**: `PRAGMA journal_mode=WAL` enables concurrent reads/writes with the GnuCash GUI open
- **Auto-commit**: Write operations commit immediately — no separate `commit` tool needed
- **Read tools**: `list_accounts`, `get_account_balance`, `get_transactions`, `search_accounts`, `get_account_info`, `get_account_mapping`
- **Write tools**: `create_account`, `delete_account`, `add_transaction`, `add_account_mapping`
- **Account matching**: Supports exact match, suffix match (dot notation), and case-insensitive partial match
- **Multi-currency**: `add_transaction` supports transfers between accounts with different currencies via `dest_amount` parameter
- **Account mapping**: `account_mapping.json` stores beancount→GNUCash account name mappings for sync workflows

## SQLite Schema Details

- **GUIDs**: 32-char hex strings generated via `uuid.uuid4().hex`
- **Amounts**: Stored as `num/denom` fraction pairs (e.g., `10050/100` = $100.50)
- **Dates**: `"YYYY-MM-DD HH:MM:SS"` format in `text(19)` columns
- **Account types**: Stored as strings ("BANK", "ASSET", "EXPENSE", etc.)
- **Account hierarchy**: `parent_guid` chain walked in Python to build full dot-separated names
- **gnclock table**: Do NOT touch — GnuCash GUI manages it independently
- **Reconcile state**: New splits use `'n'` (not reconciled)

## Key Constraints

- **GNUCASH_FILE required**: Server exits with error if not set
- **SQLite format only**: The .gnucash file must be SQLite format (not gzip XML)
- **WAL mode**: Set on connection — allows GnuCash GUI to remain open during MCP operations
- **No system dependencies**: Uses only Python stdlib + `mcp` package
