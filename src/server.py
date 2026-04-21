"""Customer Data MCP Server.

Exposes CRUD and search tools over a SQLite database of synthetic customer records.

Transport:
  - stdio  (default): launched by MCP clients such as Claude Desktop
  - http   (HTTP/SSE): set MCP_TRANSPORT=http and optionally MCP_PORT (default 8080)
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

import src.db as db

mcp = FastMCP(
    "customer-data",
    instructions=(
        "Tools for searching, reading, and managing synthetic customer records. "
        "All data is fictional and for demonstration purposes only."
    ),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_customer(id: int) -> dict:
    """Fetch a single customer record by its internal numeric ID."""
    record = db.get_customer(id)
    if record is None:
        raise ValueError(f"No customer found with id={id}")
    return record


@mcp.tool()
def find_by_ssn(ssn: str) -> dict:
    """Look up a customer by their exact Social Security Number (format NNN-NN-NNNN)."""
    record = db.find_by_ssn(ssn)
    if record is None:
        raise ValueError(f"No customer found with ssn={ssn}")
    return record


@mcp.tool()
def search_customers(
    query: str = "",
    first_name: str = "",
    last_name: str = "",
    state: str = "",
    zip_code: str = "",
    dob: str = "",
    limit: int = 20,
) -> list[dict]:
    """Search for customers by any combination of fields.

    Parameters
    ----------
    query     : Free-text search across first_name, last_name, and city (FTS5).
    first_name: Prefix match on first name.
    last_name : Prefix match on last name.
    state     : Exact 2-letter state abbreviation (e.g. "VA").
    zip_code  : Exact ZIP code.
    dob       : Exact date of birth in MM/DD/YYYY format.
    limit     : Max results to return (1–100, default 20).
    """
    return db.search_customers(
        query=query or None,
        first_name=first_name or None,
        last_name=last_name or None,
        state=state or None,
        zip_code=zip_code or None,
        dob=dob or None,
        limit=limit,
    )


@mcp.tool()
def count_customers(
    query: str = "",
    first_name: str = "",
    last_name: str = "",
    state: str = "",
    zip_code: str = "",
    dob: str = "",
) -> dict:
    """Return a count of customers matching the given filters.

    Accepts the same filter parameters as search_customers but returns
    {"count": N} instead of a list of records.
    """
    n = db.count_customers(
        query=query or None,
        first_name=first_name or None,
        last_name=last_name or None,
        state=state or None,
        zip_code=zip_code or None,
        dob=dob or None,
    )
    return {"count": n}


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

@mcp.tool()
def create_customer(
    last_name: str,
    first_name: str,
    ssn: str,
    dob: str,
    city: str,
    state: str,
    zip_code: str,
    ccn: str,
    cc_exp: str,
) -> dict:
    """Create a new customer record.

    Parameters
    ----------
    last_name : Customer last name.
    first_name: Customer first name.
    ssn       : Social Security Number in NNN-NN-NNNN format (must be unique).
    dob       : Date of birth in MM/DD/YYYY format.
    city      : City of residence.
    state     : 2-letter state abbreviation.
    zip_code  : ZIP code.
    ccn       : Credit card number.
    cc_exp    : Credit card expiration in MM/YY format.
    """
    try:
        return db.create_customer({
            "last_name": last_name,
            "first_name": first_name,
            "ssn": ssn,
            "dob": dob,
            "city": city,
            "state": state,
            "zip": zip_code,
            "ccn": ccn,
            "cc_exp": cc_exp,
        })
    except Exception as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def update_customer(
    id: int,
    last_name: str = "",
    first_name: str = "",
    ssn: str = "",
    dob: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    ccn: str = "",
    cc_exp: str = "",
) -> dict:
    """Update one or more fields on an existing customer record.

    Only non-empty parameters are applied. The id field is required.
    """
    updates: dict = {}
    if last_name:
        updates["last_name"] = last_name
    if first_name:
        updates["first_name"] = first_name
    if ssn:
        updates["ssn"] = ssn
    if dob:
        updates["dob"] = dob
    if city:
        updates["city"] = city
    if state:
        updates["state"] = state
    if zip_code:
        updates["zip"] = zip_code
    if ccn:
        updates["ccn"] = ccn
    if cc_exp:
        updates["cc_exp"] = cc_exp

    if not updates:
        raise ValueError("At least one field must be provided to update")

    record = db.update_customer(id, updates)
    if record is None:
        raise ValueError(f"No customer found with id={id}")
    return record


@mcp.tool()
def delete_customer(id: int) -> dict:
    """Delete a customer record by its internal numeric ID.

    Returns {"deleted": true, "id": N} on success.
    """
    if not db.delete_customer(id):
        raise ValueError(f"No customer found with id={id}")
    return {"deleted": True, "id": id}


# ---------------------------------------------------------------------------
# Health endpoint (HTTP transport only)
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.environ.get("MCP_PORT", "8080"))
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
