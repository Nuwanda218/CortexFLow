from collections import deque
from copy import deepcopy


class FakeAdapter:
    def __init__(self, sessions=None, snapshots=None):
        self.sessions = list(sessions or [])
        self.snapshots = dict(snapshots or {})
        self.list_script = deque()
        self.snapshot_script = deque()
        self.list_calls = 0
        self.snapshot_calls = []
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self):
        self.connect_calls += 1

    async def close(self):
        self.close_calls += 1

    async def list_sessions(self):
        self.list_calls += 1
        result = self.list_script.popleft() if self.list_script else self.sessions
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)
    async def get_snapshot(self, session_id, tab_id=None):
        self.snapshot_calls.append((session_id, tab_id))
        if self.snapshot_script:
            result = self.snapshot_script.popleft()
        else:
            result = self.snapshots[(session_id, tab_id)]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)
