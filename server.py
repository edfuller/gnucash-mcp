from mcp.server.fastmcp import FastMCP
import gnucash
from gnucash import Session, SessionOpenMode, GncNumeric, Transaction, Split
import sys
import os
import argparse
import datetime
import atexit
import subprocess
import json

# Global variable to hold the current session
current_session = None

# Write mode flag - controls whether write operations are allowed
write_mode = False

# Configured file path from environment
configured_file = None

# MCP server instance - created in main() with appropriate name based on mode
mcp = None


def get_no_file_error() -> str:
    """Return error message for when no file is open, including configured path if available."""
    if configured_file:
        return f"Error: No GnuCash file is open. Use open_file with path: {configured_file}"
    return "Error: No GnuCash file is open. Use open_file to open a file first."


def is_gnucash_running() -> bool:
    """Check if GnuCash application is currently running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "gnucash"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False  # Assume not running if we can't check


def remove_stale_lock(file_path: str) -> bool:
    """
    Remove stale lock file if GnuCash is not running.
    Returns True if lock was removed or didn't exist, False if GnuCash is running.
    """
    lock_path = file_path + ".LCK"
    if not os.path.exists(lock_path):
        return True

    if is_gnucash_running():
        return False  # Don't remove lock if GnuCash is running

    try:
        os.remove(lock_path)
        print(f"Removed stale lock file: {lock_path}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Failed to remove lock file: {e}", file=sys.stderr)
        return False


def cleanup_session():
    """Save changes and clean up the GnuCash session on exit."""
    global current_session
    if current_session:
        try:
            # Auto-save if in write mode
            if write_mode:
                current_session.save()
                print("Changes saved automatically on exit.", file=sys.stderr)
            current_session.end()
            print("GnuCash session closed (lock released).", file=sys.stderr)
        except Exception as e:
            print(f"Error during cleanup: {e}", file=sys.stderr)
        current_session = None


# Register cleanup handler
atexit.register(cleanup_session)


def find_account(root, account_name: str):
    """
    Find an account by name using exact match, suffix match, or partial match.
    Returns the account object or None if not found.
    """
    # Try exact match or suffix match first
    for acc in root.get_descendants():
        full_name = acc.get_full_name()
        if full_name == account_name or full_name.endswith("." + account_name):
            return acc

    # Try case-insensitive partial match
    account_lower = account_name.lower()
    for acc in root.get_descendants():
        if account_lower in acc.get_full_name().lower():
            return acc

    return None


def get_account_type_name(type_code: int) -> str:
    """Convert GnuCash account type code to human-readable name."""
    type_map = {
        0: "BANK",
        1: "CASH",
        2: "ASSET",
        3: "CREDIT",
        4: "LIABILITY",
        5: "STOCK",
        6: "MUTUAL",
        7: "CURRENCY",
        8: "INCOME",
        9: "EXPENSE",
        10: "EQUITY",
        11: "RECEIVABLE",
        12: "PAYABLE",
        13: "ROOT",
        14: "TRADING",
    }
    return type_map.get(type_code, f"UNKNOWN({type_code})")


def open_file(file_path: str, break_lock: bool = False) -> str:
    """
    Open a GnuCash file (.gnucash). Supports both XML (compressed or uncompressed) and SQLite formats.

    Args:
        file_path: The absolute path to the GnuCash file.
        break_lock: If True, remove stale lock file before opening (only if GnuCash app is not running).
    """
    global current_session
    try:
        # Close existing session if any
        if current_session:
            current_session.end()
            current_session = None

        if not os.path.exists(file_path):
            return f"Error: File not found: {file_path}"

        # Handle lock breaking if requested
        if break_lock:
            lock_path = file_path + ".LCK"
            if os.path.exists(lock_path):
                if is_gnucash_running():
                    return "Error: Cannot break lock - GnuCash application is currently running. Close GnuCash first."
                remove_stale_lock(file_path)

        # Use SESSION_NORMAL_OPEN for write mode, SESSION_READ_ONLY otherwise
        if write_mode:
            current_session = Session(file_path, SessionOpenMode.SESSION_NORMAL_OPEN)
            return f"Successfully opened GnuCash file (read-write): {file_path}"
        else:
            current_session = Session(file_path, SessionOpenMode.SESSION_READ_ONLY)
            return f"Successfully opened GnuCash file (read-only): {file_path}"
    except Exception as e:
        error_msg = str(e)
        if "LOCKED" in error_msg.upper():
            return f"Error opening file: {error_msg}. Try with break_lock=True if GnuCash is not running."
        return f"Error opening file: {error_msg}"


def close_file() -> str:
    """
    Close the currently open GnuCash file.
    """
    global current_session
    if current_session:
        current_session.end()
        current_session = None
        return "File closed successfully."
    return "No file is currently open."


def list_accounts() -> str:
    """
    List all accounts in the currently open GnuCash file with their types.
    """
    global current_session
    if not current_session:
        return get_no_file_error()

    try:
        book = current_session.book
        root = book.get_root_account()

        accounts = []
        for acc in root.get_descendants():
            type_name = get_account_type_name(acc.GetType())
            accounts.append(f"{acc.get_full_name()} ({type_name})")

        return "\n".join(sorted(accounts))
    except Exception as e:
        return f"Error listing accounts: {str(e)}"


def get_account_balance(account_name: str) -> str:
    """
    Get the balance of a specific account.

    Args:
        account_name: The full name of the account (e.g., "Assets.Current Assets.Current Account")
                      or a partial name to search for.
    """
    global current_session
    if not current_session:
        return get_no_file_error()

    try:
        book = current_session.book
        root = book.get_root_account()

        # Search for account by full name or partial match
        target_account = None
        for acc in root.get_descendants():
            full_name = acc.get_full_name()
            if full_name == account_name or full_name.endswith(account_name):
                target_account = acc
                break

        if not target_account:
            # Try case-insensitive partial match
            account_lower = account_name.lower()
            for acc in root.get_descendants():
                if account_lower in acc.get_full_name().lower():
                    target_account = acc
                    break

        if not target_account:
            return f"Error: Account '{account_name}' not found."

        balance = target_account.GetBalance()
        commodity = target_account.GetCommodity()
        currency = commodity.get_mnemonic() if commodity else "?"

        # Convert GncNumeric to decimal
        balance_decimal = float(balance.num()) / float(balance.denom())

        return f"Balance of {target_account.get_full_name()}: {balance_decimal:.2f} {currency}"
    except Exception as e:
        return f"Error getting balance: {str(e)}"


def get_transactions(account_name: str, limit: int = 20) -> str:
    """
    Get recent transactions for a specific account.

    Args:
        account_name: The full name of the account or a partial name to search for.
        limit: Maximum number of transactions to return (default 20).
    """
    global current_session
    if not current_session:
        return get_no_file_error()

    try:
        book = current_session.book
        root = book.get_root_account()

        # Search for account
        target_account = None
        for acc in root.get_descendants():
            full_name = acc.get_full_name()
            if full_name == account_name or full_name.endswith(account_name):
                target_account = acc
                break

        if not target_account:
            account_lower = account_name.lower()
            for acc in root.get_descendants():
                if account_lower in acc.get_full_name().lower():
                    target_account = acc
                    break

        if not target_account:
            return f"Error: Account '{account_name}' not found."

        splits = target_account.GetSplitList()
        if not splits:
            return f"No transactions found for {target_account.get_full_name()}."

        transactions = []
        for split in splits[-limit:]:  # Get last N splits
            trans = split.parent
            date = trans.GetDate().strftime("%Y-%m-%d")
            desc = trans.GetDescription()
            value = split.GetValue()
            value_decimal = float(value.num()) / float(value.denom())

            commodity = target_account.GetCommodity()
            currency = commodity.get_mnemonic() if commodity else "?"

            transactions.append(f"{date} | {value_decimal:>10.2f} {currency} | {desc}")

        header = f"Transactions for {target_account.get_full_name()}:\n"
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
    global current_session
    if not current_session:
        return get_no_file_error()

    try:
        book = current_session.book
        root = book.get_root_account()

        query_lower = query.lower()
        matches = []
        for acc in root.get_descendants():
            full_name = acc.get_full_name()
            if query_lower in full_name.lower():
                type_name = get_account_type_name(acc.GetType())
                matches.append(f"{full_name} ({type_name})")

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
    global current_session
    if not current_session:
        return get_no_file_error()

    try:
        book = current_session.book
        root = book.get_root_account()

        # Search for account
        target_account = None
        for acc in root.get_descendants():
            full_name = acc.get_full_name()
            if full_name == account_name or full_name.endswith(account_name):
                target_account = acc
                break

        if not target_account:
            account_lower = account_name.lower()
            for acc in root.get_descendants():
                if account_lower in acc.get_full_name().lower():
                    target_account = acc
                    break

        if not target_account:
            return f"Error: Account '{account_name}' not found."

        # Gather account info
        full_name = target_account.get_full_name()
        type_name = get_account_type_name(target_account.GetType())
        description = target_account.GetDescription() or "(none)"
        code = target_account.GetCode() or "(none)"

        commodity = target_account.GetCommodity()
        currency = commodity.get_mnemonic() if commodity else "?"

        balance = target_account.GetBalance()
        balance_decimal = float(balance.num()) / float(balance.denom())

        cleared_balance = target_account.GetClearedBalance()
        cleared_decimal = float(cleared_balance.num()) / float(cleared_balance.denom())

        reconciled_balance = target_account.GetReconciledBalance()
        reconciled_decimal = float(reconciled_balance.num()) / float(reconciled_balance.denom())

        num_splits = len(target_account.GetSplitList())

        # Get children
        children = [child.name for child in target_account.get_children()]
        children_str = ", ".join(children) if children else "(none)"

        info = f"""Account: {full_name}
Type: {type_name}
Description: {description}
Code: {code}
Currency: {currency}
Balance: {balance_decimal:.2f} {currency}
Cleared Balance: {cleared_decimal:.2f} {currency}
Reconciled Balance: {reconciled_decimal:.2f} {currency}
Number of Transactions: {num_splits}
Child Accounts: {children_str}"""

        return info
    except Exception as e:
        return f"Error getting account info: {str(e)}"


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
    destination account's currency. The transaction currency is the source account's
    currency. SetValue on both splits balances in the transaction currency, while
    SetAmount on each split reflects the account's native currency.

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
    global current_session

    if not current_session:
        return get_no_file_error()

    if amount <= 0:
        return "Error: Amount must be a positive number."

    if dest_amount is not None and dest_amount <= 0:
        return "Error: dest_amount must be a positive number."

    try:
        book = current_session.book
        root = book.get_root_account()

        # Find both accounts
        source_account = find_account(root, from_account)
        if not source_account:
            return f"Error: Source account '{from_account}' not found."

        dest_account = find_account(root, to_account)
        if not dest_account:
            return f"Error: Destination account '{to_account}' not found."

        # Get the currency from the source account
        commodity = source_account.GetCommodity()
        if not commodity:
            return "Error: Source account has no currency/commodity set."

        dest_commodity = dest_account.GetCommodity()
        if not dest_commodity:
            return "Error: Destination account has no currency/commodity set."

        src_currency = commodity.get_mnemonic()
        dst_currency = dest_commodity.get_mnemonic()
        multi_currency = src_currency != dst_currency

        # Validate multi-currency parameters
        if multi_currency and dest_amount is None:
            return (
                f"Error: Accounts use different currencies ({src_currency} vs {dst_currency}). "
                f"Provide dest_amount to specify the amount in {dst_currency}."
            )

        # Parse the date
        if date:
            try:
                tx_date = datetime.datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return "Error: Invalid date format. Use YYYY-MM-DD."
        else:
            tx_date = datetime.datetime.now()

        # Convert amounts to GncNumeric
        # Transaction currency is always the source account's currency
        src_fraction = commodity.get_fraction()
        src_amount_int = round(amount * src_fraction)

        # Create the transaction
        tx = Transaction(book)
        tx.BeginEdit()

        tx.SetCurrency(commodity)
        tx.SetDescription(description)
        tx.SetDateEnteredSecs(datetime.datetime.now())
        tx.SetDatePostedSecs(tx_date)

        if multi_currency:
            # Multi-currency: SetValue uses transaction currency (source),
            # SetAmount uses each account's native currency
            dst_fraction = dest_commodity.get_fraction()
            dst_amount_int = round(dest_amount * dst_fraction)

            # Source split: value and amount both in source currency
            split_from = Split(book)
            split_from.SetParent(tx)
            split_from.SetAccount(source_account)
            split_from.SetValue(GncNumeric(-src_amount_int, src_fraction))
            split_from.SetAmount(GncNumeric(-src_amount_int, src_fraction))
            if memo:
                split_from.SetMemo(memo)

            # Dest split: value in transaction currency (source), amount in dest currency
            split_to = Split(book)
            split_to.SetParent(tx)
            split_to.SetAccount(dest_account)
            split_to.SetValue(GncNumeric(src_amount_int, src_fraction))
            split_to.SetAmount(GncNumeric(dst_amount_int, dst_fraction))
            if memo:
                split_to.SetMemo(memo)
        else:
            # Same currency: value == amount on both splits
            split_from = Split(book)
            split_from.SetParent(tx)
            split_from.SetAccount(source_account)
            split_from.SetValue(GncNumeric(-src_amount_int, src_fraction))
            split_from.SetAmount(GncNumeric(-src_amount_int, src_fraction))
            if memo:
                split_from.SetMemo(memo)

            split_to = Split(book)
            split_to.SetParent(tx)
            split_to.SetAccount(dest_account)
            split_to.SetValue(GncNumeric(src_amount_int, src_fraction))
            split_to.SetAmount(GncNumeric(src_amount_int, src_fraction))
            if memo:
                split_to.SetMemo(memo)

        # Commit the transaction - GnuCash will validate it's balanced
        tx.CommitEdit()

        if multi_currency:
            return (
                f"Transaction created successfully:\n"
                f"  {amount:.2f} {src_currency} from {source_account.get_full_name()}\n"
                f"  {dest_amount:.2f} {dst_currency} to {dest_account.get_full_name()}\n"
                f"  Description: {description}\n"
                f"  Date: {tx_date.strftime('%Y-%m-%d')}\n\n"
                f"Note: Use commit to persist changes to disk."
            )
        else:
            return (
                f"Transaction created successfully:\n"
                f"  {amount:.2f} {src_currency} from {source_account.get_full_name()} "
                f"to {dest_account.get_full_name()}\n"
                f"  Description: {description}\n"
                f"  Date: {tx_date.strftime('%Y-%m-%d')}\n\n"
                f"Note: Use commit to persist changes to disk."
            )

    except Exception as e:
        return f"Error creating transaction: {str(e)}"


def commit() -> str:
    """
    Save all pending changes to the GnuCash file.
    Call this after making modifications to persist them immediately.
    Changes are also auto-saved when the session ends.
    """
    global current_session

    if not current_session:
        return get_no_file_error()

    try:
        current_session.save()
        return "Changes committed successfully."
    except Exception as e:
        return f"Error committing changes: {str(e)}"


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
    global write_mode, current_session, mcp, configured_file

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="GnuCash MCP Server")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Enable write mode (allows creating transactions and saving files)"
    )
    args = parser.parse_args()

    write_mode = args.write

    # Create MCP server with mode-aware name so clients know the server's capabilities
    if write_mode:
        mcp = FastMCP("gnucash-mcp (read-write)")
        print("GnuCash MCP Server running on stdio (WRITE MODE ENABLED)...", file=sys.stderr)
    else:
        mcp = FastMCP("gnucash-mcp (read-only)")
        print("GnuCash MCP Server running on stdio (read-only)...", file=sys.stderr)

    # Auto-open file from GNUCASH_FILE environment variable
    env_file = os.environ.get("GNUCASH_FILE")

    if not env_file:
        print("Error: GNUCASH_FILE environment variable not set.", file=sys.stderr)
        print("Configure with: claude mcp add gnucash ... -e GNUCASH_FILE=/path/to/file.gnucash", file=sys.stderr)
        sys.exit(1)

    configured_file = env_file  # Store for error messages

    if not os.path.exists(env_file):
        print(f"Error: GnuCash file not found: {env_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Opening GnuCash file: {env_file}", file=sys.stderr)

    # Auto-break stale locks if GnuCash is not running
    lock_path = env_file + ".LCK"
    if os.path.exists(lock_path):
        if is_gnucash_running():
            print("Error: Lock file exists and GnuCash is running. Close GnuCash first.", file=sys.stderr)
            sys.exit(1)
        else:
            print("Removing stale lock file...", file=sys.stderr)
            remove_stale_lock(env_file)

    try:
        if write_mode:
            current_session = Session(env_file, SessionOpenMode.SESSION_NORMAL_OPEN)
            print("File opened successfully (read-write).", file=sys.stderr)
        else:
            current_session = Session(env_file, SessionOpenMode.SESSION_READ_ONLY)
            print("File opened successfully (read-only).", file=sys.stderr)
    except Exception as e:
        print(f"Error opening file: {e}", file=sys.stderr)
        sys.exit(1)

    # Register read tools - these are always available
    mcp.tool()(list_accounts)
    mcp.tool()(get_account_balance)
    mcp.tool()(get_transactions)
    mcp.tool()(search_accounts)
    mcp.tool()(get_account_info)
    mcp.tool()(get_account_mapping)

    # Register write tools only in write mode
    if write_mode:
        mcp.tool()(add_account_mapping)
        mcp.tool(
            description="[WRITE MODE ENABLED] Create a new transaction transferring money between two accounts. "
            "This creates a balanced double-entry transaction with two splits. "
            "Supports multi-currency: provide dest_amount when source and destination use different currencies. "
            "Args: from_account (source account), to_account (destination account), "
            "amount (positive number in source currency), description (payee), "
            "date (optional, YYYY-MM-DD), memo (optional), "
            "dest_amount (optional, amount in destination currency - required for multi-currency)."
        )(add_transaction)
        mcp.tool(
            description="[WRITE MODE ENABLED] Save pending changes to the GnuCash file immediately. "
            "Changes are also auto-saved when the session ends."
        )(commit)

    mcp.run()


if __name__ == "__main__":
    main()
