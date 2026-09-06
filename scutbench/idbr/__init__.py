"""scutbench package for the identity-backup-restore recipe (idbr rev 1).

The twin wraps the REAL scutl_idbackup Archivist over a real on-disk
identity; the mock surface is the filesystem itself — the backup copy,
the live state dir, and the owned-resource provider probe are where the
contracts' failure modes are armed.
"""
