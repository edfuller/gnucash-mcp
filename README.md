# GnuCash MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server for [GnuCash](https://gnucash.org/), allowing AI assistants to read and query your financial data.

This server uses the **official GnuCash Python bindings**, providing full compatibility with all GnuCash file formats including gzip-compressed XML and SQLite databases.

## Prerequisites

The GnuCash Python bindings must be installed on your system. These are part of GnuCash itself and **cannot be installed via pip**.

### Ubuntu/Debian

```bash
sudo apt install gnucash python3-gnucash
```

### Fedora

```bash
sudo dnf install gnucash python3-gnucash
```

### Arch Linux

```bash
sudo pacman -S gnucash
```

### Verify Installation

```bash
python3 -c "import gnucash; print('GnuCash bindings OK')"
```

## Quick Start

```bash
# Clone the repository
git clone https://github.com/jmceleney/gnucash-mcp.git
cd gnucash-mcp

# Create a virtual environment with access to system packages
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install mcp

# Run the server (GNUCASH_FILE is required)
GNUCASH_FILE=/path/to/your/finances.gnucash python3 server.py
```

## Configuration

### Claude Code

```bash
claude mcp add gnucash \
  -e GNUCASH_FILE=/path/to/your/finances.gnucash \
  -- /path/to/gnucash-mcp/.venv/bin/python3 /path/to/gnucash-mcp/server.py
```

To enable write mode (allows creating transactions):

```bash
claude mcp add gnucash \
  -e GNUCASH_FILE=/path/to/your/finances.gnucash \
  -- /path/to/gnucash-mcp/.venv/bin/python3 /path/to/gnucash-mcp/server.py --write
```

### Claude Desktop

Add to your configuration file:

- **Linux**: `~/.config/claude/claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "/path/to/gnucash-mcp/.venv/bin/python3",
      "args": ["/path/to/gnucash-mcp/server.py"],
      "env": {
        "GNUCASH_FILE": "/path/to/your/finances.gnucash"
      }
    }
  }
}
```

For write mode, add `"--write"` to the args array.

## Available Tools

### Read Tools (always available)

| Tool | Description |
|------|-------------|
| `list_accounts()` | List all accounts with their types. |
| `get_account_balance(account_name)` | Get balance (supports partial name matching). |
| `get_transactions(account_name, limit)` | Get recent transactions (default: 20). |
| `search_accounts(query)` | Search accounts by name (case-insensitive). |
| `get_account_info(account_name)` | Get detailed account information. |
| `get_account_mapping()` | Get the beancount→GNUCash account name mapping. |

### Write Tools (require `--write` flag)

| Tool | Description |
|------|-------------|
| `add_transaction(from_account, to_account, amount, description, date, memo, dest_amount)` | Create a transaction between two accounts. Supports multi-currency via `dest_amount`. |
| `commit()` | Save pending changes to disk (also auto-saves on exit). |
| `add_account_mapping(beancount_name, gnucash_name)` | Add/update a beancount→GNUCash account name mapping. |

### Account Names

Use dot notation: `Assets.Current Assets.Checking Account`

Partial matching works:
- `Checking Account` matches `Assets.Current Assets.Checking Account`
- `electric` matches `Expenses.Utilities.Electric`

## Example Queries

- "What's my checking account balance?"
- "Show recent transactions from savings"
- "List all expense accounts"
- "Search for accounts containing 'utilities'"
- "Transfer $50 from checking to savings" (write mode)
- "Transfer $100 USD to my EUR savings account" (multi-currency, write mode)

## Multi-Currency Transactions

When transferring between accounts with different currencies, provide `dest_amount`:

```
add_transaction(
    from_account="Assets.Current Assets.Checking",
    to_account="Assets.Savings.EUR Savings",
    amount=100.00,        # 100 USD leaving source
    dest_amount=92.50,    # 92.50 EUR arriving at destination
    description="Transfer to EUR savings"
)
```

The transaction currency is the source account's currency. `SetValue` on both splits balances in the transaction currency, while `SetAmount` reflects each account's native currency.

## Account Mapping

For beancount-bot-tg sync workflows, account mappings translate beancount names (colon-separated) to GNUCash names (dot-separated). Mappings are stored in `account_mapping.json`.

## Troubleshooting

### "No module named 'gnucash'"

Install GnuCash Python bindings via your system package manager.

### "No module named 'mcp'"

Activate the venv and install: `pip install mcp`

### File locked

The server automatically removes stale lock files if GnuCash desktop app isn't running. If GnuCash is open, close it first.

### "GNUCASH_FILE environment variable not set"

The `GNUCASH_FILE` environment variable is required. Set it when running the server or in your MCP configuration.

## Why Not uvx/pipx?

These tools create isolated environments without access to system packages. The GnuCash Python bindings are only available as system packages, so we must use `--system-site-packages`.

## Limitations

- **System dependency**: Requires GnuCash installed system-wide
- **Single file**: One file open at a time
- **Multi-currency requires dest_amount**: When accounts have different currencies, you must provide `dest_amount`

## License

MIT
