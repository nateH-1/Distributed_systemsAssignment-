import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import socket
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import Store, StorageServer
from config import Settings, Site
from frontend import FrontendApplication


class StorageTests(unittest.TestCase):
    def test_durable_outage_catchup_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [str(Path(directory)/f'{n}.db') for n in ('a','b')]
            a,b = Store(paths[0],'a'), Store(paths[1],'b')
            def send(store, mid):
                return store.mutate('store','alice',mid,dict(id=mid,to='alice',**{'from':'bob'},subject='hi',body='saved',read=False))
            try:
                send(a,'first')
                b.merge(a.operations())
                a.close()
                send(b,'second')
                b.mutate('read','alice','first',True)
                a = Store(paths[0],'a')
                a.merge(b.operations())
                self.assertEqual(a.mailbox('alice'),b.mailbox('alice'))
                b.close()
                a.mutate('delete','alice','first',None)
                send(a,'third')
                b = Store(paths[1],'b')
                b.merge(a.operations())
                a.merge(b.operations())
                self.assertEqual(set(b.mailbox('alice')),{'second','third'})
                self.assertEqual(a.mailbox('alice'),b.mailbox('alice'))
            finally:
                a.close()
                b.close()

    def test_restart_conflict_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'a.db')
            a, b = Store(path, 'a'), Store(str(Path(directory)/'b.db'), 'b')
            message = dict(id='m', to='alice', **{'from':'bob'}, subject='hi', body='hello', read=False)
            a.mutate('store','alice','m',message)
            b.merge(a.operations())
            a.mutate('read','alice','m',True)
            b.mutate('read','alice','m',False)
            left, right = a.operations(), b.operations()
            a.merge(right)
            b.merge(left)
            self.assertEqual(a.mailbox('alice'),b.mailbox('alice'))
            a.mutate('delete','alice','m',None)
            b.merge(a.operations())
            b.merge(left)  # replay cannot resurrect a delete
            self.assertEqual(b.mailbox('alice'),{})
            a.close()
            a = Store(path,'a')
            self.assertEqual(a.mailbox('alice'),{})
            a.close()
            b.close()

    def test_real_http_failover_and_catchup(self):
        # Probe the host's loopback permissions before starting the scenario.
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        probe.listen()
        try:
            with socket.create_connection(probe.getsockname(), timeout=1):
                pass
        except PermissionError as exc:
            self.skipTest(f'host blocks loopback connections: {exc}')
        finally:
            probe.close()
        with tempfile.TemporaryDirectory() as directory:
            stores = [Store(str(Path(directory)/f'{n}.db'),n) for n in ('a','b')]
            servers = [StorageServer(('127.0.0.1',0),s) for s in stores]
            ports = [s.server_port for s in servers]
            for i,s in enumerate(servers):
                s.peer = f'http://127.0.0.1:{ports[1-i]}'
                threading.Thread(target=s.serve_forever,daemon=True).start()
            app = FrontendApplication(Settings('local',(Site('local','127.0.0.1'),),('127.0.0.1',),storage_port=ports[0]))
            def wait_for(predicate):
                deadline = time.monotonic()+5
                while time.monotonic()<deadline:
                    if predicate():
                        return
                    time.sleep(.05)
                self.fail('replication did not converge')
            try:
                result = app.dispatch('POST','/mail',{'from':'bob','to':'alice','subject':'hello','body':'persistent'})
                self.assertEqual(result.status,200)
                mid = result.body['id']
                wait_for(lambda: mid in stores[1].mailbox('alice'))
                servers[0].shutdown()
                servers[0].server_close()
                app = FrontendApplication(Settings('local',(Site('local','127.0.0.1'),),('127.0.0.1',),storage_port=ports[1]))
                self.assertEqual(app.dispatch('PUT',f'/mailbox/alice/{mid}',{'read':True}).status,200)
                second = app.dispatch('POST','/mail',{'from':'bob','to':'alice','subject':'outage','body':'saved'})
                self.assertEqual(second.status,200)
                servers[0] = StorageServer(('127.0.0.1',ports[0]),stores[0],f'http://127.0.0.1:{ports[1]}')
                threading.Thread(target=servers[0].serve_forever,daemon=True).start()
                wait_for(lambda: stores[0].mailbox('alice') == stores[1].mailbox('alice'))
                self.assertTrue(stores[0].mailbox('alice')[mid]['read'])
                servers[1].shutdown()
                servers[1].server_close()
                app = FrontendApplication(Settings('local',(Site('local','127.0.0.1'),),('127.0.0.1',),storage_port=ports[0]))
                self.assertEqual(len(app.dispatch('GET','/mailbox/alice').body['messages']),2)
                self.assertEqual(app.dispatch('DELETE',f'/mailbox/alice/{mid}').status,200)
                self.assertEqual(app.dispatch('GET',f'/mailbox/alice/{mid}').status,404)
            finally:
                for server in servers:
                    if not server.stopping.is_set():
                        server.shutdown()
                        server.server_close()
                for store in stores:
                    store.close()


if __name__ == '__main__':
    unittest.main()
