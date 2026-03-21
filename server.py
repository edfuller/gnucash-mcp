from mcp.server.fastmcp import FastMCP
import sqlite3
import uuid
import sys
import os
import datetime
import json

# Global SQLite connection
conn = None

# Configured file path from environment
configured_file = None

# MCP server instance
mcp = FastMCP("gnucash-mcp")


def generate_guid():
    """Generate a 32-char hex GUID matching GnuCash format."""
    return uuid.uuid4().hex


def build_account_tree(db):
    """Load all accounts and compute full dot-separated names."""
    rows = db.execute(
        "SELECT guid, name, account_type, commodity_guid, parent_guid, "
        "description, code, hidden, placeholder, commodity_scu FROM accounts"
    ).fetchall()

    by_guid = {}
    for r in rows:
        by_guid[r[0]] = {
            "guid": r[0],
            "name": r[1],
            "account_type": r[2],
            "commodity_guid": r[3],
            "parent_guid": r[4],
            "description": r[5],
            "code": r[6],
            "hidden": r[7],
            "placeholder": r[8],
            "commodity_scu": r[9],
        }

    def full_name(guid):
        parts = []
        current = guid
        while current and current in by_guid:
            acc = by_guid[current]
            if acc["account_type"] == "ROOT":
                break
            parts.append(acc["name"])
            current = acc["parent_guid"]
        parts.reverse()
        return ".".join(parts)

    result = {}
    for guid, acc in by_guid.items():
        if acc["account_type"] == "ROOT":
            continue
        acc["full_name"] = full_name(guid)
        result[guid] = acc

    return result


def find_account_guid(db, name):
    """
    Find an account by name using 3-tier matching: exact, suffix, partial.
    Returns (guid, full_name) or (None, None).
    """
    tree = build_account_tree(db)

    # Exact match
    for guid, acc in tree.items():
        if acc["full_name"] == name:
            return guid, acc["full_name"]

    # Suffix match
    for guid, acc in tree.items():
        if acc["full_name"].endswith("." + name):
            return guid, acc["full_name"]

    # Case-insensitive partial match
    name_lower = name.lower()
    for guid, acc in tree.items():
        if name_lower in acc["full_name"].lower():
            return guid, acc["full_name"]

    return None, None


def get_commodity_info(db, commodity_guid):
    """Get commodity mnemonic and fraction for a given guid."""
    row = db.execute(
        "SELECT mnemonic, fraction FROM commodities WHERE guid = ?",
        (commodity_guid,)
    ).fetchone()
    if row:
        return row[0], row[1]
    return "?", 100


def list_accounts() -> str:
    """
    List all accounts in the GnuCash file with their types.
    """
    try:
        tree = build_account_tree(conn)
        accounts = [f"{acc['full_name']} ({acc['account_type']})" for acc in tree.values()]
        return "\n".join(sorted(accounts))
    except Exception as e:
        return f"Error listing accounts: {str(e)}"


def get_account_balance(account_name: str) -> str:
    """
    Get the balance of a specific account.

    Args:
        account_name: The full name of the account (e.g., "Assets.Current Assets.Checking Account")
                      or a partial name to search for.
    """
    try:
        guid, full_name = find_account_guid(conn, account_name)
        if not guid:
            return f"Error: Account '{account_name}' not found."

        tree = build_account_tree(conn)
        acc = tree[guid]
        mnemonic, fraction = get_commodity_info(conn, acc["commodity_guid"])

        row = conn.execute(
            "SELECT COALESCE(SUM(quantity_num), 0) FROM splits WHERE account_guid = ?",
            (guid,)
        ).fetchone()
        balance = row[0] / fraction

        return f"Balance of {full_name}: {balance:.2f} {mnemonic}"
    except Exception as e:
        return f"Error getting balance: {str(e)}"


def get_transactions(account_name: str, limit: int = 20) -> str:
    """
    Get recent transactions for a specific account.

    Args:
        account_name: The full name of the account or a partial name to search for.
        limit: Maximum number of transactions to return (default 20).
    """
    try:
        guid, full_name = find_account_guid(conn, account_name)
        if not guid:
            return f"Error: Account '{account_name}' not found."

        tree = build_account_tree(conn)
        acc = tree[guid]
        mnemonic, fraction = get_commodity_info(conn, acc["commodity_guid"])

        rows = conn.execute(
            "SELECT t.post_date, s.value_num, s.value_denom, t.description "
            "FROM splits s JOIN transactions t ON s.tx_guid = t.guid "
            "WHERE s.account_guid = ? ORDER BY t.post_date DESC LIMIT ?",
            (guid, limit)
        ).fetchall()

        if not rows:
            return f"No transactions found for {full_name}."

        # Reverse so oldest first (matching original behavior)
        rows = list(reversed(rows))

        transactions = []
        for post_date, value_num, value_denom, description in rows:
            date_str = post_date[:10] if post_date else "????-??-??"
            value = value_num / value_denom if value_denom else 0
            transactions.append(f"{date_str} | {value:>10.2f} {mnemonic} | {description}")

        header = f"Transactions for {full_name}:\n"
        header += "-" * 60 + "\n"
        return header + "\n".join(transactions)
    except Exception as e:
        return f"Error getting transactions: {str(e)}"


def search_accounts(query: str) -> str:
    """
    Search for accounts by name (case-insensitive partial match).

    Args:
        query: Search string to match against account names.
    """
    try:
        tree = build_account_tree(conn)
        query_lower = query.lower()
        matches = []
        for acc in tree.values():
            if query_lower in acc["full_name"].lower():
                matches.append(f"{acc['full_name']} ({acc['account_type']})")

        if not matches:
            return f"No accounts found matching '{query}'."

        return f"Found {len(matches)} account(s):\n" + "\n".join(sorted(matches))
    except Exception as e:
        return f"Error searching accounts: {str(e)}"


def get_account_info(account_name: str) -> str:
    """
    Get detailed information about a specific account.

    Args:
        account_name: The full name of the account or a partial name to search for.
    """
    try:
        guid, full_name = find_account_guid(conn, account_name)
        if not guid:
            return f"Error: Account '{account_name}' not found."

        tree = build_account_tree(conn)
        acc = tree[guid]
        mnemonic, fraction = get_commodity_info(conn, acc["commodity_guid"])

        # Balance
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity_num), 0) FROM splits WHERE account_guid = ?",
            (guid,)
        ).fetchone()
        balance = row[0] / fraction

        # Cleared balance (reconcile_state = 'c' or 'y')
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity_num), 0) FROM splits "
            "WHERE account_guid = ? AND reconcile_state IN ('c', 'y')",
            (guid,)
        ).fetchone()
        cleared = row[0] / fraction

        # Reconciled balance (reconcile_state = 'y')
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity_num), 0) FROM splits "
            "WHERE account_guid = ? AND reconcile_state = 'y'",
            (guid,)
        ).fetchone()
        reconciled = row[0] / fraction

        # Split count
        row = conn.execute(
            "SELECT COUNT(*) FROM splits WHERE account_guid = ?", (guid,)
        ).fetchone()
        num_splits = row[0]

        # Children
        children_rows = conn.execute(
            "SELECT name FROM accounts WHERE parent_guid = ? AND account_type != 'ROOT'",
            (guid,)
        ).fetchall()
        children_str = ", ".join(r[0] for r in children_rows) if children_rows else "(none)"

        description = acc["description"] or "(none)"
        code = acc["code"] or "(none)"

        info = f"""Account: {full_name}
Type: {acc['account_type']}
Description: {description}
Code: {code}
Currency: {mnemonic}
Balance: {balance:.2f} {mnemonic}
Cleared Balance: {cleared:.2f} {mnemonic}
Reconciled Balance: {reconciled:.2f} {mnemonic}
Number of Transactions: {num_splits}
Child Accounts: {children_str}"""

        return info
    except Exception as e:
        return f"Error getting account info: {str(e)}"


VALID_ACCOUNT_TYPES = {
    "BANK", "CASH", "ASSET", "CREDIT", "LIABILITY",
    "STOCK", "MUTUAL", "CURRENCY", "INCOME", "EXPENSE",
    "EQUITY", "RECEIVABLE", "PAYABLE", "TRADING",
}


def create_account(
    name: str,
    account_type: str,
    parent: str = None,
    currency: str = None,
    description: str = None,
) -> str:
    """
    Create a new account in the GnuCash file.

    Args:
        name: The account name (e.g., "Groceries"). Do NOT include the parent path.
        account_type: The account type. One of: BANK, CASH, ASSET, CREDIT, LIABILITY,
                      STOCK, MUTUAL, CURRENCY, INCOME, EXPENSE, EQUITY, RECEIVABLE,
                      PAYABLE, TRADING.
        parent: Full name of the parent account (e.g., "Expenses.Food"). If omitted,
                creates under the root account.
        currency: ISO 4217 currency code (e.g., "USD", "EUR"). Defaults to the parent
                  account's currency, or USD if no parent.
        description: Optional description for the account.
    """
    account_type_upper = account_type.upper()
    if account_type_upper not in VALID_ACCOUNT_TYPES:
        return f"Error: Invalid account type '{account_type}'. Must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}"

    try:
        # Find parent
        if parent:
            parent_guid, parent_full = find_account_guid(conn, parent)
            if not parent_guid:
                return f"Error: Parent account '{parent}' not found."
        else:
            # Root account
            row = conn.execute(
                "SELECT guid FROM accounts WHERE account_type = 'ROOT'"
            ).fetchone()
            if not row:
                return "Error: Root account not found."
            parent_guid = row[0]

        # Check if account already exists under this parent
        existing = conn.execute(
            "SELECT name FROM accounts WHERE parent_guid = ? AND name = ?",
            (parent_guid, name)
        ).fetchone()
        if existing:
            return f"Error: Account '{name}' already exists under the specified parent."

        # Determine commodity
        if currency:
            commodity_row = conn.execute(
                "SELECT guid, fraction FROM commodities WHERE namespace = 'CURRENCY' AND mnemonic = ?",
                (currency.upper(),)
            ).fetchone()
            if not commodity_row:
                return f"Error: Currency '{currency}' not found. Use an ISO 4217 code (e.g., USD, EUR)."
            commodity_guid = commodity_row[0]
            commodity_scu = commodity_row[1]
        else:
            # Inherit from parent
            parent_row = conn.execute(
                "SELECT commodity_guid, commodity_scu FROM accounts WHERE guid = ?",
                (parent_guid,)
            ).fetchone()
            if parent_row and parent_row[0]:
                commodity_guid = parent_row[0]
                commodity_scu = parent_row[1]
            else:
                # Fallback to USD
                usd_row = conn.execute(
                    "SELECT guid, fraction FROM commodities WHERE namespace = 'CURRENCY' AND mnemonic = 'USD'"
                ).fetchone()
                if not usd_row:
                    return "Error: Could not determine currency. Specify one explicitly."
                commodity_guid = usd_row[0]
                commodity_scu = usd_row[1]

        new_guid = generate_guid()
        conn.execute(
            "INSERT INTO accounts (guid, name, account_type, commodity_guid, commodity_scu, "
            "non_std_scu, parent_guid, code, description, hidden, placeholder) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, '', ?, 0, 0)",
            (new_guid, name, account_type_upper, commodity_guid, commodity_scu,
             parent_guid, description or "")
        )
        conn.commit()

        # Build full name for display
        tree = build_account_tree(conn)
        full_name = tree[new_guid]["full_name"] if new_guid in tree else name
        mnemonic, _ = get_commodity_info(conn, commodity_guid)

        return (
            f"Account created successfully:\n"
            f"  Name: {full_name}\n"
            f"  Type: {account_type_upper}\n"
            f"  Currency: {mnemonic}"
        )

    except Exception as e:
        return f"Error creating account: {str(e)}"


def delete_account(account_name: str) -> str:
    """
    Delete an account from the GnuCash file.
    The account must have no transactions and no child accounts.

    Args:
        account_name: The full name of the account (e.g., "Expenses.Food.Groceries")
                      or a partial name to search for.
    """
    try:
        guid, full_name = find_account_guid(conn, account_name)
        if not guid:
            return f"Error: Account '{account_name}' not found."

        # Check for children
        children = conn.execute(
            "SELECT name FROM accounts WHERE parent_guid = ?", (guid,)
        ).fetchall()
        if children:
            child_names = [r[0] for r in children]
            return f"Error: Cannot delete '{full_name}' — it has child accounts: {', '.join(child_names)}"

        # Check for splits
        row = conn.execute(
            "SELECT COUNT(*) FROM splits WHERE account_guid = ?", (guid,)
        ).fetchone()
        if row[0] > 0:
            return f"Error: Cannot delete '{full_name}' — it has {row[0]} transaction(s). Remove them first."

        # Delete any slots associated with this account
        conn.execute("DELETE FROM slots WHERE obj_guid = ?", (guid,))
        conn.execute("DELETE FROM accounts WHERE guid = ?", (guid,))
        conn.commit()

        return f"Account deleted: {full_name}"

    except Exception as e:
        return f"Error deleting account: {str(e)}"


def add_transaction(
    from_account: str,
    to_account: str,
    amount: float,
    description: str,
    date: str = None,
    memo: str = None,
    dest_amount: float = None
) -> str:
    """
    Create a new transaction transferring money between two accounts.
    This creates a balanced double-entry transaction with two splits.

    Supports multi-currency transactions: when the source and destination accounts
    use different currencies, provide dest_amount to specify the amount in the
    destination account's currency.

    Args:
        from_account: The source account name (money flows out of this account).
        to_account: The destination account name (money flows into this account).
        amount: The amount to transfer in the source account's currency (positive number).
        description: The transaction description/payee.
        date: Optional date in YYYY-MM-DD format (defaults to today).
        memo: Optional memo for the splits.
        dest_amount: The amount in the destination account's currency (required when
                     accounts use different currencies, e.g., transferring 100 USD
                     to a EUR account where dest_amount=92.50 means 92.50 EUR received).
    """
    if amount <= 0:
        return "Error: Amount must be a positive number."

    if dest_amount is not None and dest_amount <= 0:
        return "Error: dest_amount must be a positive number."

    try:
        src_guid, src_full = find_account_guid(conn, from_account)
        if not src_guid:
            return f"Error: Source account '{from_account}' not found."

        dst_guid, dst_full = find_account_guid(conn, to_account)
        if not dst_guid:
            return f"Error: Destination account '{to_account}' not found."

        tree = build_account_tree(conn)
        src_acc = tree[src_guid]
        dst_acc = tree[dst_guid]

        src_mnemonic, src_fraction = get_commodity_info(conn, src_acc["commodity_guid"])
        dst_mnemonic, dst_fraction = get_commodity_info(conn, dst_acc["commodity_guid"])

        multi_currency = src_acc["commodity_guid"] != dst_acc["commodity_guid"]

        if multi_currency and dest_amount is None:
            return (
                f"Error: Accounts use different currencies ({src_mnemonic} vs {dst_mnemonic}). "
                f"Provide dest_amount to specify the amount in {dst_mnemonic}."
            )

        # Parse date
        if date:
            try:
                datetime.datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return "Error: Invalid date format. Use YYYY-MM-DD."
            post_date = date + " 00:00:00"
        else:
            post_date = datetime.datetime.now().strftime("%Y-%m-%d") + " 00:00:00"

        enter_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Compute amounts as num/denom
        src_value_num = round(amount * src_fraction)

        tx_guid = generate_guid()

        # Transaction currency is always the source account's currency
        conn.execute(
            "INSERT INTO transactions (guid, currency_guid, num, post_date, enter_date, description) "
            "VALUES (?, ?, '', ?, ?, ?)",
            (tx_guid, src_acc["commodity_guid"], post_date, enter_date, description)
        )

        # Source split (money out — negative)
        src_split_guid = generate_guid()
        if multi_currency:
            # value in transaction currency, quantity in account currency (same for source)
            conn.execute(
                "INSERT INTO splits (guid, tx_guid, account_guid, memo, action, "
                "reconcile_state, reconcile_date, value_num, value_denom, "
                "quantity_num, quantity_denom, lot_guid) "
                "VALUES (?, ?, ?, ?, '', 'n', NULL, ?, ?, ?, ?, NULL)",
                (src_split_guid, tx_guid, src_guid, memo or "",
                 -src_value_num, src_fraction, -src_value_num, src_fraction)
            )

            # Dest split: value in transaction currency (source), quantity in dest currency
            dst_quantity_num = round(dest_amount * dst_fraction)
            dst_split_guid = generate_guid()
            conn.execute(
                "INSERT INTO splits (guid, tx_guid, account_guid, memo, action, "
                "reconcile_state, reconcile_date, value_num, value_denom, "
                "quantity_num, quantity_denom, lot_guid) "
                "VALUES (?, ?, ?, ?, '', 'n', NULL, ?, ?, ?, ?, NULL)",
                (dst_split_guid, tx_guid, dst_guid, memo or "",
                 src_value_num, src_fraction, dst_quantity_num, dst_fraction)
            )
        else:
            # Same currency: value == quantity on both splits
            conn.execute(
                "INSERT INTO splits (guid, tx_guid, account_guid, memo, action, "
                "reconcile_state, reconcile_date, value_num, value_denom, "
                "quantity_num, quantity_denom, lot_guid) "
                "VALUES (?, ?, ?, ?, '', 'n', NULL, ?, ?, ?, ?, NULL)",
                (src_split_guid, tx_guid, src_guid, memo or "",
                 -src_value_num, src_fraction, -src_value_num, src_fraction)
            )

            dst_split_guid = generate_guid()
            conn.execute(
                "INSERT INTO splits (guid, tx_guid, account_guid, memo, action, "
                "reconcile_state, reconcile_date, value_num, value_denom, "
                "quantity_num, quantity_denom, lot_guid) "
                "VALUES (?, ?, ?, ?, '', 'n', NULL, ?, ?, ?, ?, NULL)",
                (dst_split_guid, tx_guid, dst_guid, memo or "",
                 src_value_num, src_fraction, src_value_num, src_fraction)
            )

        conn.commit()

        date_display = date or datetime.datetime.now().strftime("%Y-%m-%d")
        if multi_currency:
            return (
                f"Transaction created successfully:\n"
                f"  {amount:.2f} {src_mnemonic} from {src_full}\n"
                f"  {dest_amount:.2f} {dst_mnemonic} to {dst_full}\n"
                f"  Description: {description}\n"
                f"  Date: {date_display}"
            )
        else:
            return (
                f"Transaction created successfully:\n"
                f"  {amount:.2f} {src_mnemonic} from {src_full} to {dst_full}\n"
                f"  Description: {description}\n"
                f"  Date: {date_display}"
            )

    except Exception as e:
        return f"Error creating transaction: {str(e)}"


def _get_mapping_path() -> str:
    """Return the path to account_mapping.json next to server.py."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "account_mapping.json")


def _load_mappings() -> dict:
    """Load account mappings from JSON file."""
    path = _get_mapping_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        data = json.load(f)
    return data.get("mappings", {})


def _save_mappings(mappings: dict) -> None:
    """Save account mappings to JSON file."""
    path = _get_mapping_path()
    data = {"mappings": mappings}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def get_account_mapping() -> str:
    """
    Get the beancount-to-GNUCash account name mapping.
    Returns all configured mappings from beancount account names (colon-separated,
    e.g., "Expenses:Food:Groceries") to GNUCash account names (dot-separated,
    e.g., "Expenses.Food.Groceries").
    """
    mappings = _load_mappings()
    if not mappings:
        return "No account mappings configured. Use add_account_mapping to add mappings."
    lines = [f"  {bc} -> {gc}" for bc, gc in sorted(mappings.items())]
    return f"Account mappings ({len(mappings)}):\n" + "\n".join(lines)


def add_account_mapping(beancount_name: str, gnucash_name: str) -> str:
    """
    Add or update a beancount-to-GNUCash account name mapping.

    Args:
        beancount_name: The beancount account name (colon-separated, e.g., "Expenses:Food:Groceries").
        gnucash_name: The GNUCash account name (dot-separated, e.g., "Expenses.Food.Groceries").
    """
    mappings = _load_mappings()
    existed = beancount_name in mappings
    mappings[beancount_name] = gnucash_name
    _save_mappings(mappings)
    action = "Updated" if existed else "Added"
    return f"{action} mapping: {beancount_name} -> {gnucash_name}"


def main():
    global conn, configured_file

    env_file = os.environ.get("GNUCASH_FILE")
    if not env_file:
        print("Error: GNUCASH_FILE environment variable not set.", file=sys.stderr)
        sys.exit(1)

    configured_file = env_file

    if not os.path.exists(env_file):
        print(f"Error: GnuCash file not found: {env_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Opening GnuCash file (SQLite): {env_file}", file=sys.stderr)

    conn = sqlite3.connect(env_file)
    conn.execute("PRAGMA journal_mode=WAL")
    print("SQLite WAL mode enabled — concurrent access with GnuCash GUI is supported.", file=sys.stderr)

    # Register all tools
    mcp.tool()(list_accounts)
    mcp.tool()(get_account_balance)
    mcp.tool()(get_transactions)
    mcp.tool()(search_accounts)
    mcp.tool()(get_account_info)
    mcp.tool()(get_account_mapping)
    mcp.tool()(add_account_mapping)
    mcp.tool()(create_account)
    mcp.tool()(delete_account)
    mcp.tool()(add_transaction)

    mcp.run()


if __name__ == "__main__":
    main()
