"""One private COBOL worker per web thread; no SQL or banking rules here."""
import atexit
import json
import os
import select
import subprocess
import threading
import time
import weakref


_local = threading.local()
_workers = weakref.WeakSet()
_CONTEXT_KEYS = ('DB2_DATABASE', 'DB2_HOST', 'DB2_USER', 'DB2_PASSWORD', 'DB2_SCHEMA')


class NativeWorker:
    def __init__(self, context):
        self.owner_pid = os.getpid()
        self.context = context
        self.process = None
        self.process = subprocess.Popen(['/usr/local/bin/bank-worker'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        _workers.add(self)

    def close(self):
        process = self.process
        if process is None:
            return
        self.process = None
        for stream in (process.stdin, process.stdout):
            stream.close()
        # A forked child must not terminate a worker owned by its parent.
        if self.owner_pid == os.getpid():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            # Resolve the inherited Popen handle without signalling its parent's child.
            process.poll()

    def __del__(self):
        self.close()

    def exchange(self, payload, timeout=25):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') + b'\n'
        if len(data) > 16384:
            return {'ok': False, 'error': 'INVALID_REQUEST'}
        deadline = time.monotonic() + timeout
        received = bytearray()
        input_fd = self.process.stdin.fileno()
        output_fd = self.process.stdout.fileno()
        try:
            while data or b'\n' not in received:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('COBOL worker timed out')
                readable, writable, _ = select.select([output_fd], [input_fd] if data else [], [], remaining)
                if writable:
                    written = os.write(input_fd, data)
                    data = data[written:]
                if readable:
                    chunk = os.read(output_fd, 65536)
                    if not chunk:
                        raise OSError('COBOL worker disconnected')
                    received.extend(chunk)
                    if len(received) > 1048576:
                        raise ValueError('COBOL response too large')
            line, remainder = received.split(b'\n', 1)
            if remainder or data:
                raise ValueError('Invalid COBOL framing')
            response = json.loads(line)
            if not isinstance(response, dict) or not isinstance(response.get('ok'), bool):
                raise ValueError('Invalid COBOL response')
            return response
        except (OSError, ValueError):
            # Never replay a request: it may already have committed.
            self.close()
            raise


def close_backend():
    worker = getattr(_local, 'worker', None)
    if worker is not None:
        worker.close()
        _local.worker = None


def exchange(payload):
    context = tuple(os.environ.get(key) for key in _CONTEXT_KEYS)
    worker = getattr(_local, 'worker', None)
    if (worker is None or worker.owner_pid != os.getpid() or worker.context != context
            or worker.process is None or worker.process.poll() is not None):
        close_backend()
        worker = NativeWorker(context)
        _local.worker = worker
    return worker.exchange(payload)


@atexit.register
def close_workers():
    for worker in list(_workers):
        worker.close()
