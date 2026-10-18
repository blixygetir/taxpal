# Tool definitions for the agent (OpenAI format, which is what WebLLM takes).
# x-http = which endpoint the browser should call for it. x-confirm = ask the user first:
# true always, or {"when": arg} only when that argument is truthy.

from .rules import taxonomy
from .service import PROFILE_FIELDS


def _fn(name, description, properties=None, required=(), http=None, confirm=False):
    return {
        "type": "function",
        "function": {"name": name, "description": description,
                     "parameters": {"type": "object", "properties": properties or {}, "required": list(required)}},
        "x-http": http,
        "x-confirm": confirm,
    }


def tool_specs() -> list[dict]:
    categories = sorted(taxonomy())
    return [
        _fn("get_status", "Counts of imported, categorized and flagged transactions, 1099s, mileage trips and missing profile facts.",
            http={"method": "GET", "path": "/api/status"}),
        _fn("list_needs_review", "Transactions that are uncategorized, low-confidence, or missing a fact (business purpose, business-use %, gift recipient). Largest first.",
            {"limit": {"type": "integer", "description": "Max items, default 10"}},
            http={"method": "GET", "path": "/api/transactions", "query": {"status": "needs_review"}}),
        _fn("record_decision", "Save the user's answer about one transaction. Only call with facts the user actually stated.",
            {"txn_id": {"type": "integer"},
             "category": {"type": "string", "enum": categories},
             "business_purpose": {"type": "string", "description": "Who/what/why, for meals, travel, parking"},
             "business_use_pct": {"type": "number", "description": "0-100, for phone/internet"},
             "recipient": {"type": "string", "description": "Gift recipient"},
             "apply_to_merchant": {"type": "boolean", "description": "Remember this category for the same merchant"}},
            required=["txn_id", "category"], http={"method": "POST", "path": "/api/decisions"},
            confirm={"when": "apply_to_merchant"}),
        _fn("update_profile", "Save facts about the taxpayer. Only include fields the user stated.",
            {k: {"type": "string" if k in ("filing_status", "vehicle_method") else "number", "description": v}
             for k, v in PROFILE_FIELDS.items()},
            http={"method": "PUT", "path": "/api/profile"}),
        _fn("log_mileage", "Record a business trip for the mileage log (not commuting).",
            {"date": {"type": "string", "description": "YYYY-MM-DD in 2026"}, "miles": {"type": "number"},
             "purpose": {"type": "string"}},
            required=["date", "miles", "purpose"], http={"method": "POST", "path": "/api/mileage"}),
        _fn("reconcile_income", "Compare 1099-NEC/1099-K/1099-MISC amounts with bank deposits; list unexplained deposits and suggested adjustments.",
            http={"method": "GET", "path": "/api/reconcile"}),
        _fn("apply_adjustment", "Apply an adjustment suggested by reconcile_income (e.g. 1099-K gross-up). Requires user confirmation.",
            {"line": {"type": "string", "description": "Schedule C line, e.g. '1' or '10'"}, "amount": {"type": "number"},
             "note": {"type": "string"}},
            required=["line", "amount", "note"], http={"method": "POST", "path": "/api/adjustments"}, confirm=True),
        _fn("compute_schedule_c", "Deterministic Schedule C line totals, net profit, exclusions and flags. Never compute totals yourself.",
            http={"method": "GET", "path": "/api/schedule-c"}),
        _fn("compute_se_and_qbi", "Schedule SE self-employment tax and Form 8995 QBI deduction estimates.",
            http={"method": "GET", "path": "/api/se-qbi"}),
        _fn("plan_estimated_tax", "2027 Form 1040-ES quarterly plan using Pub 505 safe harbors.",
            http={"method": "GET", "path": "/api/estimated-tax"}),
        _fn("run_checks", "Everything that has to be fixed or explained before the user can sign off: unreviewed items, "
            "unconfirmed model guesses, 1099 gaps, internal math checks. Call this before telling the user they're done.",
            http={"method": "GET", "path": "/api/checks"}),
        _fn("explain_check", "Save the user's own explanation for a check they decided not to fix (e.g. a 1099 gap). "
            "Only for checks with can_acknowledge=true. Requires user confirmation.",
            {"check_id": {"type": "string"}, "note": {"type": "string", "description": "The user's explanation, in their words"}},
            required=["check_id", "note"], http={"method": "POST", "path": "/api/checks/{check_id}/acknowledge"}, confirm=True),
        _fn("search_irs_guidance", "Search IRS publications and form instructions (Schedule C/SE, Form 8995, Pubs 334, 463, 505, 560, 583, 587, 946). Returns quoted passages with citations and links. Cite them when explaining a rule.",
            {"query": {"type": "string", "description": "A specific question, e.g. 'can I deduct a client lunch'"},
             "k": {"type": "integer", "description": "Number of passages, default 3"}},
            required=["query"], http={"method": "POST", "path": "/api/irs/search"}),
        _fn("lookup_irs_rule", "IRS rule and citation for a category. Omit category to list all categories.",
            {"category": {"type": "string", "enum": categories}},
            http={"method": "GET", "path": "/api/irs-rules"}),
    ]
