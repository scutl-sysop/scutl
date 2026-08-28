"""scutl identity-backup-restore component (recipe #3, idbr rev 1).

The agent authors a backup MANIFEST (digests, never contents — kek
included), the human performs the copy, verification is secret-free and
ungated, and the restore rehearsal is a TRUE restore into a fresh
directory that must sign before it counts. The agent never moves key
material.
"""

from .core import Archivist  # noqa: F401
