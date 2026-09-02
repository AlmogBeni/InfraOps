"""Shared validation grammar for references resolved by secret providers."""

from __future__ import annotations

import re

# Lowercase, slash-separated provider paths. Every segment begins with an
# alphanumeric character, excluding empty, "." and ".." traversal segments
# while retaining dots, underscores and hyphens inside safe names.
SECRET_REFERENCE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_.-]*(?:/[a-z0-9][a-z0-9_.-]*)*$"
)
