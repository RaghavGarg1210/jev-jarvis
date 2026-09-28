from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from jarvis.actions import ActionExecutor, load_config
from jarvis.engine import Engine
from jarvis.planner import Planner
from jarvis.providers import Decider
from jarvis.server import JarvisServer
from jarvis.speech import Speech


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = load_config()
        engine = Engine(Planner(config, Decider()), ActionExecutor(root, config, False), root)
        self.server = JarvisServer(0, engine, Speech())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.port, timeout=3)
        connection.request(method, path, json.dumps(body) if body is not None else None, headers or {})
        response = connection.getresponse()
        data = response.read()
        result = response.status, json.loads(data), dict(response.getheaders())
        connection.close()
        return result

    def auth(self):
        return {'X-Jarvis-Token':self.server.token,'Content-Type':'application/json'}

    def test_bootstrap_and_roundtrip(self):
        status, bootstrap, headers = self.request('GET','/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(bootstrap['mode'], 'rehearsal')
        self.assertEqual(len(bootstrap['scenes']), 3)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        status, plan, _ = self.request('POST','/api/plan',{'text':'open Safari'}, self.auth())
        self.assertEqual(status, 200)
        status, receipt, _ = self.request('POST','/api/execute',{'id':plan['id']}, self.auth())
        self.assertEqual(receipt['status'], 'simulated')
        status, _, _ = self.request('POST','/api/execute',{'id':plan['id']}, self.auth())
        self.assertEqual(status, 400)

    def test_cross_origin_missing_token_and_dns_rebinding(self):
        for headers in [{}, {'Host':'evil.example'}, {**self.auth(),'Origin':'https://evil.example'}, {**self.auth(),'Origin':'null'}]:
            status, _, _ = self.request('POST','/api/plan',{'text':'open Safari'}, headers)
            self.assertEqual(status, 403)
        status, _, _ = self.request('GET','/api/bootstrap',headers={'Host':'evil.example'})
        self.assertEqual(status, 403)

    def test_rejects_injected_confirmation_actions(self):
        status, _, _ = self.request('POST','/api/execute',{'id':'abc','actions':[]},self.auth())
        self.assertEqual(status, 400)

    def test_invalid_json_types_and_private_paths(self):
        for body in [[], 'test', {'text':123}, {'text':''}, {'text':'x'*2001}]:
            status, _, _ = self.request('POST','/api/plan',body,self.auth())
            self.assertEqual(status, 400)
        for path in ['/../../.env', '/history.json', '/api/nope']:
            status, _, _ = self.request('GET', path)
            self.assertEqual(status, 404)


if __name__ == '__main__':
    unittest.main()
