"""scutl_capp: the typed-tool component of recipe #4 (capability-purchase).

The agent buys access to a paid API and uses it; every limit that
matters is enforced here, in code, before money moves or a call spends
quota. The vendor issues the API key AT purchase — the secret arrives
via the rail and must never surface after that.
"""
