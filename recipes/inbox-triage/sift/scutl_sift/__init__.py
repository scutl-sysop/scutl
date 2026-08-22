"""scutl recipe component: inbox-triage (sift).

Work an inbox without the power to act on it. The load-bearing fact
about this package is an absence: there is no send operation anywhere
in it — no SMTP, no submission client, no provider send call. Drafts
are files; the human sends from their own mail client.
"""
