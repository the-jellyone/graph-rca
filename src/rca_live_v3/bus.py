"""Local request files, one controller, append-only public events."""
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from rca_investigation.dataset import atomic_json
from .display import render


def now():
    return datetime.now(timezone.utc).isoformat()


def request(root, command, **values):
    root = Path(root)
    identifier = str(uuid4())
    atomic_json(root / 'pending' / (identifier + '.json'), {'id': identifier, 'command': command, **values})
    return identifier


def pending(root):
    for path in sorted((Path(root) / 'pending').glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
        if path.stat().st_size > 4096:
            raise ValueError('oversized command')
        payload = json.loads(path.read_text())
        if not re.fullmatch(r'[a-f0-9-]{36}', payload.get('id', '')) or path.stem != payload['id']:
            raise ValueError('invalid request ID')
        yield path, payload


@contextmanager
def server_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'server.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            owner = json.loads(path.read_text()).get('pid', 'unknown')
        except (ValueError, OSError):
            owner = 'unknown'
        raise FileExistsError(f'V3 controller lock exists, owner PID {owner}. Inspect the owner, then use scripts/live_v3.py recover with the matching bundle and registry. Do not delete a live lock.') from exc
    with os.fdopen(descriptor, 'w') as stream:
        json.dump({'pid': os.getpid(), 'started': now()}, stream)
    try:
        yield
    finally:
        path.unlink()


def emit(root, kind, payload):
    row = {'time': now(), 'kind': kind, 'payload': payload}
    path = Path(root) / 'events.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(render(kind, payload), flush=True)
    return row


def read_events(root, offset):
    path = Path(root) / 'events.jsonl'
    if not path.exists():
        return [], offset
    rows = []
    with path.open('rb') as stream:
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b'\n'):
                break
            rows.append(json.loads(line))
            offset = stream.tell()
    return rows, offset
