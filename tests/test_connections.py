import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_connections", ROOT / "codex/check_connections.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
CONNECTIONS = {"slack": {"name": "Slack", "plugin": "slack@example", "app_id": "test-slack"}}


class ConnectionStateTests(unittest.TestCase):
    def result(self, apps=None, config=None, servers=None):
        return checks.assess(CONNECTIONS, config or {}, {"apps": apps or [], "servers": servers or []})[0]

    def test_local_enabled_flag_is_not_a_connection(self):
        result = self.result(config={"plugins": {"slack@example": {"enabled": True}}})
        self.assertEqual(result[0], "ACTION REQUIRED")
        self.assertIn("/plugins", result[2])

    def test_installed_without_callable_tools_needs_action(self):
        result = self.result(apps=[{"id": "test-slack", "enabled": True, "callable": False}])
        self.assertEqual(result[0], "ACTION REQUIRED")

    def test_effective_disabled_access_is_reported(self):
        result = self.result(apps=[{"id": "test-slack", "enabled": False, "callable": False}])
        self.assertEqual(result[0], "ACTION REQUIRED")
        self.assertIn("disabled", result[2])

    def test_enabled_callable_tools_pass(self):
        self.assertEqual(self.result(apps=[{"id": "test-slack", "enabled": True, "callable": True}])[0], "TOOLS AVAILABLE")

    def test_direct_server_credentials_without_live_tools_do_not_pass(self):
        for state in [None, "starting", "failed"]:
            with self.subTest(state=state):
                result = self.result(config={"mcp_servers": {"slack": {}}}, servers=[{
                    "name": "slack", "authStatus": "oAuth", "runtimeStatus": state, "tools": {"search": {}}
                }])
                self.assertEqual(result[0], "UNVERIFIED")

    def test_direct_login_has_specific_command(self):
        result = self.result(config={"mcp_servers": {"slack": {}}}, servers=[{
            "name": "slack", "authStatus": "notLoggedIn", "tools": {}
        }])
        self.assertEqual(result[0], "ACTION REQUIRED")
        self.assertIn("codex mcp login slack", result[2])

    def test_connected_direct_tools_pass(self):
        result = self.result(config={"mcp_servers": {"slack": {}}}, servers=[{
            "name": "slack", "authStatus": "oAuth", "runtimeStatus": "connected", "tools": {"search": {}}
        }])
        self.assertEqual(result[0], "TOOLS AVAILABLE")


class RuntimeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_handshake_refresh_and_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake_codex.py"
            script.write_text('''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialize":
        print(json.dumps({"id": request["id"], "result": {}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "app/installed":
        assert request["params"] == {"forceRefresh": True}
        print(json.dumps({"method": "notification", "params": {}}), flush=True)
        print(json.dumps({"id": request["id"], "result": {"apps": [
            {"id": "test-slack", "enabled": True, "callable": True}
        ]}}), flush=True)
    else:
        raise RuntimeError("Unexpected method: " + method)
''')
            snapshot = await asyncio.wait_for(checks.runtime_snapshot([sys.executable, str(script)]), timeout=3)
            self.assertTrue(snapshot["apps"][0]["callable"])

    async def test_unresponsive_runtime_times_out(self):
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(
                checks.runtime_snapshot([sys.executable, "-c", "import time; time.sleep(5)"]), timeout=0.1
            )

    async def test_server_errors_do_not_expose_the_error_body(self):
        script = '''import json,sys
r=json.loads(sys.stdin.readline())
print(json.dumps({"id":r["id"],"error":{"code":-32601,"message":"secret-placeholder"}}),flush=True)
sys.stdin.read()
'''
        with self.assertRaises(checks.CheckError) as error:
            await asyncio.wait_for(checks.runtime_snapshot([sys.executable, "-c", script]), timeout=3)
        self.assertIn("-32601", str(error.exception))
        self.assertNotIn("secret-placeholder", str(error.exception))


if __name__ == "__main__":
    unittest.main()
