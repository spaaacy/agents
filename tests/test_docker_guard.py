import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks/docker-guard.py"

spec = importlib.util.spec_from_file_location("docker_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

BLOCKED = [
    "docker compose down",
    "cd /Users/aakifmohamed/dev/envoyai-backend && docker compose ps --format '{{.Name}}'; docker compose down 2>&1 | tail -12",
    "docker compose -f ~/dev/envoyai-backend/docker-compose.yaml down -v",
    "docker compose --project-directory ~/dev/envoyai-backend down",
    "docker-compose down --remove-orphans",
    "docker compose rm -f api",
    "docker --context default compose down",
    "COMPOSE_PROJECT_NAME=envoyai-backend docker compose down",
    "sudo docker compose down",
    "docker rm -f envoyai-backend-api-1",
    "docker container rm envoyai-backend-postgres-1",
    "docker container prune -f",
    "docker image rm envoyai-backend-api",
    "docker rmi envoyai-backend-api",
    "docker volume rm envoyai-backend_logs",
    "docker volume prune",
    "docker system prune -af",
    "docker ps -aq | xargs docker rm",
    "sh -c 'docker compose down'",
    "bash -lc \"cd ~/dev/envoyai-backend && docker compose down\"",
    "eval docker compose down",
    "echo done; (cd ~/dev/envoyai-backend; docker compose down)",
    "rm -rf dockerdata/postgres-data",
    "rm -rf ~/dev/envoyai-backend/dockerdata",
    "/usr/local/bin/docker compose down",
    "docker compose stop\ndocker compose down",
]

ALLOWED = [
    "docker compose stop",
    "docker compose start",
    "docker compose restart api",
    "docker compose up -d --no-build",
    "docker compose kill",
    "docker compose ps",
    "docker compose exec -T postgres psql -U envoy -d envoy -c 'select 1'",
    "docker compose logs --tail 50 api",
    "docker run --rm alpine true",
    "docker ps -a",
    "grep -rn 'docker compose down' skills",
    "echo 'never run docker compose down'",
    "git commit -m 'stop deleting containers on docker compose down'",
    "python3 - <<'EOF'\nprint('x')\ndocker compose down\nEOF",
    "cat <<EOF > note.md\ndocker compose down\nrm -rf dockerdata\nEOF\necho ok",
    "rm -rf node_modules",
    "docker info >/dev/null 2>&1 && echo docker-up || echo docker-down",
    "ls dockerdata",
    "unclosed 'quote docker compose down",
]


class DockerGuardTests(unittest.TestCase):
    def test_blocks_deleting_commands(self):
        for cmd in BLOCKED:
            with self.subTest(cmd=cmd):
                self.assertTrue(guard.deletes(cmd), cmd)

    def test_lets_the_rest_through(self):
        for cmd in ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertFalse(guard.deletes(cmd), cmd)

    def run_hook(self, event):
        done = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(event),
                              capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def test_claude_event_gets_a_deny_decision(self):
        out = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                             "tool_input": {"command": "docker compose down"}})
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("docker compose stop", decision["permissionDecisionReason"])

    def test_codex_argv_is_read(self):
        out = self.run_hook({"tool_name": "shell",
                             "tool_input": {"command": ["bash", "-lc", "docker compose down"]}})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_harmless_and_unreadable_input_pass_silently(self):
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "docker compose stop"}}), "")
        self.assertEqual(self.run_hook({"tool_name": "Read", "tool_input": {"file_path": "/x"}}), "")
        done = subprocess.run([sys.executable, str(SCRIPT)], input="not json", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
