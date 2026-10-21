"""One owned Docker stats stream avoids repeated CLI startup in v3 windows."""
from datetime import datetime, timezone
from queue import Queue, Empty
from threading import Thread
import json
import re
import subprocess
from .container_stats import memory_bytes
from ..contracts import real


class StreamingStatsCollector:
    def __init__(self, registry):
        self.registry = registry
        self.by_id = {v['container_id']: r for r, v in registry.items()}
        self.queue = Queue(maxsize=2048)
        self.process = self.thread = None
        self.error = None

    def decode(self, row):
        cid = row.get('ID', row.get('Container'))
        if cid not in self.by_id: raise ValueError('streamed stats container identity mismatch')
        resource, item = self.by_id[cid], self.registry[self.by_id[cid]]
        cpu = float(row['CPUPerc'].removesuffix('%'))
        real(cpu)
        usage, limit = map(memory_bytes, row['MemUsage'].split('/'))
        common = {'resource_id': resource, 'container_id': cid,
                  'sample_time': datetime.now(timezone.utc).isoformat(), 'units': 'docker_cpu_percent'}
        if limit == 0 and usage == 0 and cpu == 0:
            return {**common, 'availability': 'NO_DATA', 'reason': 'container_not_running'}
        if abs(limit-item['memory_limit_bytes']) > max(1024, item['memory_limit_bytes']*.005):
            raise ValueError('streamed stats memory limit changed')
        return {**common, 'availability': 'OK', 'cpu_percent': cpu, 'quota_cores': item['cpu_quota_cores'],
                'memory_bytes': usage, 'memory_limit_bytes': item['memory_limit_bytes'],
                'memory_usage_original': row['MemUsage'], 'memory_source': 'docker_cli_stats_linux_v2',
                'collection_duration_ms': 0}

    def __enter__(self):
        self.process = subprocess.Popen(['docker', 'stats', '--no-trunc', '--format', '{{json .}}', *self.by_id],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, shell=False)
        def read():
            try:
                for line in self.process.stdout:
                    if len(line.encode()) > 8192: raise ValueError('streamed stats exceeded row bound')
                    line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line).strip()
                    if line: self.queue.put_nowait(self.decode(json.loads(line)))
            except BaseException as exc:
                self.error = exc
        self.thread = Thread(target=read, daemon=True)
        self.thread.start()
        return self

    def poll(self):
        if self.error is not None:
            raise RuntimeError('streamed stats collection failed') from self.error
        rows = []
        while True:
            try: item = self.queue.get_nowait()
            except Empty: break
            if isinstance(item, BaseException): raise RuntimeError('streamed stats collection failed') from item
            rows.append(item)
        if self.process.poll() is not None:
            raise RuntimeError('owned Docker stats stream exited during collection')
        return tuple(rows)

    def __exit__(self, *args):
        if self.process is not None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.thread.join(timeout=5)
            self.process.stdout.close()
