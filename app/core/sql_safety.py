import re

# very strict on purpose
FORBIDDEN = [
    r"\binsert\b", r"\bupdate\b", r"\bdelete\b", r"\bdrop\b", r"\balter\b",
    r"\btruncate\b", r"\bcreate\b", r"\bgrant\b", r"\brevoke\b",
    r"\bcommit\b", r"\brollback\b", r"\bvacuum\b", r"\banalyze\b"
]

def is_safe_select(sql: str) -> tuple[bool, str]:
    s = sql.strip().lower()

    if not s.startswith("select"):
        return False, "Only SELECT queries are allowed."

    # block multiple statements
    if ";" in s:
        return False, "Multiple statements are not allowed."

    for pat in FORBIDDEN:
        if re.search(pat, s):
            return False, f"Forbidden keyword detected: {pat}"

    return True, "ok"
