"""Ordered data migrations for files under user/.

Each entry is (stable id, function(user_dir)). `update` runs every entry not yet
recorded in user/migrations.json exactly once, in this order. Append new entries
when a stored format changes; never rename or reorder a released one.
"""

MIGRATIONS = []
