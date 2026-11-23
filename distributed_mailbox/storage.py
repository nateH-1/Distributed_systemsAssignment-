"""Durable two-replica mailbox storage (Python standard library only).

Each mutation is an immutable operation. Replicas union their operation logs;
Lamport counters plus node IDs define a deterministic order. Deletes are retained
as tombstones. A write is acknowledged after the local SQLite transaction commits.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError


class Store:
    def __init__(self, path, node_id):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.node_id = node_id
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS ops (id TEXT PRIMARY KEY, counter INTEGER, node TEXT, payload TEXT)')
        self.db.commit()

    def operations(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM ops ORDER BY counter,node,id')]

    @staticmethod
    def validate(op):
        if not isinstance(op, dict) or set(op) != {'id', 'counter', 'node', 'kind', 'user', 'message_id', 'value'}:
            raise ValueError('invalid operation')
        if any(not isinstance(op[k], str) or not op[k] for k in ('id','node','user','message_id')):
            raise ValueError('invalid operation identifiers')
        if type(op['counter']) is not int or not 0 < op['counter'] < 2**63:
            raise ValueError('invalid counter')
        if op['kind'] == 'store':
            m = op['value']
            if not isinstance(m, dict) or any(not isinstance(m.get(k), str) for k in ('id','from','to','subject','body')):
                raise ValueError('invalid message')
            if m['id'] != op['message_id'] or m['to'] != op['user'] or not m['from'] or type(m.get('read')) is not bool:
                raise ValueError('invalid message identity/read flag')
        elif op['kind'] == 'read':
            if type(op['value']) is not bool:
                raise ValueError('read must be boolean')
        elif op['kind'] != 'delete' or op['value'] is not None:
            raise ValueError('invalid operation kind/value')

    def merge(self, operations):
        if not isinstance(operations, list):
            raise ValueError('operations must be a list')
        for op in operations:
            self.validate(op)
        with self.lock, self.db:
            for op in operations:
                encoded = json.dumps(op, sort_keys=True)
                old = self.db.execute('SELECT payload FROM ops WHERE id=?', (op['id'],)).fetchone()
                if old and old[0] != encoded:
                    raise ValueError('conflicting operation ID')
                self.db.execute('INSERT OR IGNORE INTO ops VALUES (?,?,?,?)', (op['id'], op['counter'], op['node'], encoded))

    def mailbox(self, user):
        messages, deleted = {}, set()
        for op in self.operations():
            if op['user'] != user:
                continue
            mid = op['message_id']
            if op['kind'] == 'delete':
                deleted.add(mid)
                messages.pop(mid, None)
            elif mid not in deleted:
                if op['kind'] == 'store':
                    messages.setdefault(mid, dict(op['value']))
                elif mid in messages:
                    messages[mid]['read'] = op['value']
        return messages

    def mutate(self, kind, user, mid, value):
        with self.lock:
            messages = self.mailbox(user)
            if kind != 'store' and mid not in messages:
                return 404, {'error': 'message absent'}
            if kind == 'store':
                if mid in messages:
                    original = messages[mid]
                    if any(original[k] != value[k] for k in ('id','from','to','subject','body')):
                        return 409, {'error': 'message ID already used'}
                    return 200, original
                if any(o['message_id'] == mid and o['user'] == user and o['kind'] == 'delete' for o in self.operations()):
                    return 409, {'error': 'message ID was deleted'}
            counter = self.db.execute('SELECT COALESCE(MAX(counter),0)+1 FROM ops').fetchone()[0]
            op = dict(id=uuid.uuid4().hex, counter=counter, node=self.node_id, kind=kind, user=user, message_id=mid, value=value)
            self.merge([op])
            if kind == 'delete':
                return 200, {}
            return 200, self.mailbox(user)[mid]

    def close(self):
        with self.lock:
            self.db.close()


def summary(message):
    return {k: message[k] for k in ('id','from','subject','read')}


class StorageServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, peer=None, timeout=0.7, interval=0.5):
        self.store, self.peer = store, peer
        self.timeout_seconds, self.interval = timeout, interval
        self.stopping = threading.Event()
        self.sync_lock = threading.Lock()
        super().__init__(address, Handler)
        self.worker = threading.Thread(target=self._replicate, daemon=True)
        self.worker.start()

    def sync(self):
        if not self.peer:
            return
        with self.sync_lock:
            request = Request(self.peer.rstrip('/') + '/internal/sync',
                              data=json.dumps({'operations': self.store.operations()}).encode(),
                              headers={'Content-Type':'application/json'}, method='POST')
            with urlopen(request, timeout=self.timeout_seconds) as response:
                self.store.merge(json.load(response)['operations'])

    def _replicate(self):
        while not self.stopping.is_set():
            try:
                self.sync()
            except (OSError, URLError, ValueError, KeyError):
                logging.debug('peer unavailable; retrying', exc_info=True)
            self.stopping.wait(self.interval)

    def server_close(self):
        self.stopping.set()
        self.worker.join(timeout=self.timeout_seconds + 2)
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def dispatch(self):
        path = urlsplit(self.path).path
        parts = [unquote(p) for p in path.strip('/').split('/')]
        body = None
        if self.command in ('POST','PUT'):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16 * 1024 * 1024:
                raise ValueError('invalid body size')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('body must be an object')
        store = self.server.store
        if path == '/internal/status' and self.command == 'GET':
            return 200, {'node': store.node_id, 'operations': len(store.operations())}
        if path == '/internal/sync' and self.command == 'POST':
            store.merge(body.get('operations'))
            return 200, {'operations': store.operations()}
        if parts == ['internal','mail'] and self.command == 'POST':
            probe = dict(id='validate', counter=1, node=store.node_id, kind='store', user=body.get('to'), message_id=body.get('id'), value=body)
            Store.validate(probe)
            return store.mutate('store', body['to'], body['id'], body)
        if len(parts) in (3,4) and parts[:2] == ['internal','mailbox'] and parts[2]:
            user = parts[2]
            if len(parts) == 3 and self.command == 'GET':
                return 200, {'messages': [summary(m) for _,m in sorted(store.mailbox(user).items())]}
            if len(parts) == 4:
                mid = parts[3]
                if self.command == 'GET':
                    message = store.mailbox(user).get(mid)
                    return (200, message) if message else (404, {'error':'message absent'})
                if self.command == 'DELETE':
                    return store.mutate('delete', user, mid, None)
                if self.command == 'PUT':
                    if set(body) != {'read'} or type(body['read']) is not bool:
                        raise ValueError('expected a boolean read field')
                    status, message = store.mutate('read', user, mid, body['read'])
                    return status, summary(message) if status == 200 else message
        return 404, {'error':'unknown endpoint'}

    def handle_request(self):
        self.connection.settimeout(3)
        try:
            status, body = self.dispatch()
        except (ValueError, UnicodeError, TypeError) as exc:
            status, body = 400, {'error': str(exc)}
        except (OSError, sqlite3.Error):
            logging.exception('storage request failed')
            status, body = 503, {'error': 'storage unavailable', 'node': self.server.store.node_id}
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except OSError:
            pass

    do_GET = do_POST = do_PUT = do_DELETE = handle_request


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-id', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--peer', required=True, help='other local storage node, e.g. http://node-3:9000')
    args = parser.parse_args()
    store = Store(args.db, args.node_id)
    server = StorageServer((args.host,args.port), store, args.peer)
    print(f'{args.node_id} storage listening on {args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
