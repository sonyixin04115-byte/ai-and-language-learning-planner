import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location("launcher", Path(__file__).parents[1] / "start.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class Response(io.BytesIO):
    status = 200


class LauncherTests(unittest.TestCase):
    def test_health_requires_app_and_new_version(self):
        for payload, expected in [
            ({"app": "zhixu-planner", "version": "2"}, True),
            ({"app": "other", "version": "2"}, False),
            ({"app": "zhixu-planner", "version": "1"}, False),
        ]:
            with patch.object(launcher.urllib.request, "urlopen", return_value=Response(json.dumps(payload).encode())):
                self.assertEqual(launcher.backend_ready(), expected)

    def test_reuses_healthy_existing_without_spawning(self):
        with patch.object(launcher, "port_in_use", return_value=True), patch.object(launcher, "backend_ready", return_value=True), patch.object(launcher, "frontend_ready", return_value=True), patch.object(launcher.subprocess, "Popen") as spawn, patch.object(launcher.webbrowser, "open") as browser:
            self.assertEqual(launcher.run(no_browser=True), 0)
            spawn.assert_not_called()
            browser.assert_not_called()

    def test_partial_or_stale_service_does_not_spawn_or_stop(self):
        with patch.object(launcher, "port_in_use", side_effect=[True, False]), patch.object(launcher, "backend_ready", return_value=False), patch.object(launcher.subprocess, "Popen") as spawn:
            self.assertEqual(launcher.run(no_browser=True), 1)
            spawn.assert_not_called()

    def test_keyboard_interrupt_cleans_up_only_owned_children(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(launcher, "port_in_use", return_value=False), patch.object(launcher.subprocess, "Popen", return_value=child), patch.object(launcher, "backend_ready", side_effect=KeyboardInterrupt), patch.object(launcher.webbrowser, "open") as browser:
            self.assertEqual(launcher.run(no_browser=True), 0)
            self.assertEqual(child.terminate.call_count, 2)
            self.assertEqual(child.wait.call_count, 2)
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
