"""Shared classification of scheduled jobs that never publish dataset releases.

New or unregistered jobs retain the publisher admission and skip requirements.
"""

NON_PUBLISHING_JOBS = frozenset({"dataset_usage"})
