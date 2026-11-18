"""Gateway scope headers for API tests: a caller in exactly one group, so
new rows are auto-assigned to it (see app/core/group_scope.py)."""
TEST_GROUP = "00000000-0000-0000-0000-0000000000a1"
ONE_GROUP = {"X-Group-Ids": TEST_GROUP, "X-Is-Superuser": "0"}
