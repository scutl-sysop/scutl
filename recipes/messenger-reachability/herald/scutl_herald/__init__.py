"""scutl recipe component: messenger-reachability (herald).

Be reachable on the human's terms. The load-bearing facts about this
package are two confinements: there is exactly one recipient — the
configured owner peer id — and no code path from any input to a
destination; and every send is counted in code against per-hour and
per-day ceilings, so the flood failure mode (the OpenClaw 500-messages
overnight) is structurally impossible, not merely discouraged.
"""
