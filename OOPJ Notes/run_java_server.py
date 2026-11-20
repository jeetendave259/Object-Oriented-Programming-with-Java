#!/usr/bin/env python3
"""Serve the study pages and run Java examples with the local JDK."""

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HOST = "127.0.0.1"
PORT = 8000
MAX_REQUEST_BYTES = 32 * 1024
MAX_OUTPUT_CHARS = 64 * 1024
COMPILE_TIMEOUT_SECONDS = 20
RUN_TIMEOUT_SECONDS = 5
SERVER_ORIGIN = f"http://{HOST}:{PORT}"
JAVAFX_VERSION = "26.0.1"
JAVAFX_PROCESSES = {}
JAVAFX_PROCESS_LOCK = threading.Lock()


def javafx_library_directory():
    configured_path = os.environ.get("JAVAFX_LIB")
    candidates = [Path(configured_path).expanduser()] if configured_path else []
    candidates.extend([
        Path.home() / "Library" / "Caches" / "itm-javafx" / JAVAFX_VERSION / "mac-aarch64",
        Path("/opt/homebrew/opt/openjfx/lib"),
        Path("/usr/local/opt/openjfx/lib"),
        Path("/usr/share/openjfx/lib"),
    ])
    required_modules = ("javafx-base", "javafx-graphics", "javafx-controls")
    for candidate in candidates:
        if candidate.is_dir() and all(any(candidate.glob(f"{module}*.jar")) for module in required_modules):
            return candidate
    return None


def release_javafx_process(run_id):
    with JAVAFX_PROCESS_LOCK:
        return JAVAFX_PROCESSES.pop(run_id, None)


def stop_all_javafx_processes():
    with JAVAFX_PROCESS_LOCK:
        run_ids = list(JAVAFX_PROCESSES)
    for run_id in run_ids:
        record = release_javafx_process(run_id)
        if not record:
            continue
        process = record["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        record["temporary_directory"].cleanup()


atexit.register(stop_all_javafx_processes)


class JavaRunnerHandler(SimpleHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/execute-javafx":
            self._execute_javafx()
            return
        if path == "/api/stop-javafx":
            self._stop_javafx()
            return
        if path != "/api/execute":
            self._send_json(404, {"error": "Not found."})
            return

        if not self._valid_local_post():
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"error": "A valid Content-Length header is required."})
            return

        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Java source exceeds the 32 KB request limit."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Request body must be valid JSON."})
            return

        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not code.strip():
            self._send_json(400, {"error": "Provide non-empty Java source in the code field."})
            return

        with tempfile.TemporaryDirectory(prefix="java-playground-") as temp_dir:
            source_path = Path(temp_dir) / "Main.java"
            source_path.write_text(code, encoding="utf-8")

            try:
                compile_result = subprocess.run(
                    ["javac", "-proc:none", "-d", temp_dir, str(source_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=COMPILE_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self._send_json(200, {
                    "success": False,
                    "output": "Compilation timed out after 20 seconds.",
                })
                return

            compile_output = (compile_result.stdout + compile_result.stderr).strip()
            if compile_result.returncode:
                self._send_json(200, {
                    "success": False,
                    "output": compile_output[:MAX_OUTPUT_CHARS],
                })
                return

            try:
                run_result = subprocess.run(
                    [
                        "java",
                        "-Xmx128m",
                        "-XX:MaxMetaspaceSize=64m",
                        "-XX:ActiveProcessorCount=1",
                        "-cp",
                        temp_dir,
                        "Main",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=RUN_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                output = error.stdout or ""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                output += "\nProgram timed out after 5 seconds."
                self._send_json(200, {
                    "success": False,
                    "output": output[:MAX_OUTPUT_CHARS],
                })
                return

            output = run_result.stdout + run_result.stderr
            self._send_json(200, {
                "success": run_result.returncode == 0,
                "output": output[:MAX_OUTPUT_CHARS],
            })

    def do_GET(self):
        if urlsplit(self.path).path == "/api/javafx-status":
            self._javafx_status()
            return
        super().do_GET()

    def _valid_local_post(self):
        if self.headers.get("Host") != f"{HOST}:{PORT}":
            self._send_json(403, {"error": "Requests must use the local runner address."})
            return False
        if self.headers.get("Origin") != SERVER_ORIGIN:
            self._send_json(403, {"error": "Only pages served by this local runner may execute code."})
            return False
        return True

    def _read_javafx_code(self):
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"error": "A valid Content-Length header is required."})
            return None
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Java source exceeds the 32 KB request limit."})
            return None
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Request body must be valid JSON."})
            return None
        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not code.strip():
            self._send_json(400, {"error": "Provide non-empty Java source in the code field."})
            return None
        return code

    def _execute_javafx(self):
        if not self._valid_local_post():
            return
        code = self._read_javafx_code()
        if code is None:
            return
        javafx_lib = javafx_library_directory()
        if javafx_lib is None:
            self._send_json(503, {
                "error": "JavaFX libraries were not found. Install the JavaFX SDK or set JAVAFX_LIB to its lib directory."
            })
            return

        code, replacements = re.subn(
            r"\bpublic\s+class\s+[A-Za-z_$][\w$]*(?=\s+extends\s+Application\b)",
            "public class Main",
            code,
            count=1,
        )
        if replacements != 1:
            self._send_json(400, {
                "error": "Provide a JavaFX application with a public class that extends Application."
            })
            return

        temporary_directory = tempfile.TemporaryDirectory(prefix="javafx-playground-")
        work_dir = Path(temporary_directory.name)
        source_path = work_dir / "Main.java"
        source_path.write_text(code, encoding="utf-8")
        compile_command = [
            "javac",
            "--module-path",
            str(javafx_lib),
            "--add-modules",
            "javafx.controls",
            "-proc:none",
            "-d",
            str(work_dir),
            str(source_path),
        ]
        try:
            compile_result = subprocess.run(
                compile_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=COMPILE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            temporary_directory.cleanup()
            self._send_json(200, {"success": False, "error": "JavaFX compilation timed out after 20 seconds."})
            return
        if compile_result.returncode:
            temporary_directory.cleanup()
            output = (compile_result.stdout + compile_result.stderr).strip()
            self._send_json(200, {"success": False, "error": output[:MAX_OUTPUT_CHARS]})
            return

        run_id = uuid.uuid4().hex
        log_path = work_dir / "javafx.log"
        command = [
            "java",
            "-Xmx128m",
            "-XX:MaxMetaspaceSize=64m",
            "-XX:ActiveProcessorCount=1",
            "--module-path",
            str(javafx_lib),
            "--add-modules",
            "javafx.controls",
            "--enable-native-access=javafx.graphics",
            "-cp",
            str(work_dir),
            "Main",
        ]
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=work_dir,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as error:
            temporary_directory.cleanup()
            self._send_json(500, {"success": False, "error": f"Could not start JavaFX: {error}"})
            return

        with JAVAFX_PROCESS_LOCK:
            JAVAFX_PROCESSES[run_id] = {
                "process": process,
                "temporary_directory": temporary_directory,
                "log_path": log_path,
            }
        try:
            process.wait(timeout=0.8)
        except subprocess.TimeoutExpired:
            self._send_json(200, {
                "success": True,
                "running": True,
                "runId": run_id,
                "output": "JavaFX application launched in a desktop window.",
            })
            return

        record = release_javafx_process(run_id)
        output = record["log_path"].read_text(encoding="utf-8", errors="replace").strip()
        record["temporary_directory"].cleanup()
        self._send_json(200, {
            "success": process.returncode == 0,
            "running": False,
            "output": output or "The JavaFX application exited before its window could be confirmed.",
        })

    def _javafx_status(self):
        if self.headers.get("Host") != f"{HOST}:{PORT}":
            self._send_json(403, {"error": "Requests must use the local runner address."})
            return
        run_id = parse_qs(urlsplit(self.path).query).get("runId", [""])[0]
        with JAVAFX_PROCESS_LOCK:
            record = JAVAFX_PROCESSES.get(run_id)
        if not run_id or record is None:
            self._send_json(404, {"running": False, "error": "JavaFX run was not found or has already finished."})
            return
        process = record["process"]
        if process.poll() is None:
            self._send_json(200, {"running": True, "runId": run_id})
            return
        record = release_javafx_process(run_id)
        if record is None:
            self._send_json(404, {"running": False, "error": "JavaFX run has already finished."})
            return
        output = record["log_path"].read_text(encoding="utf-8", errors="replace").strip()
        record["temporary_directory"].cleanup()
        self._send_json(200, {
            "running": False,
            "success": process.returncode == 0,
            "output": output or "JavaFX application closed.",
        })

    def _stop_javafx(self):
        if not self._valid_local_post():
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
            if content_length <= 0 or content_length > 1024:
                self._send_json(400, {"error": "Provide a valid JSON runId."})
                return
            payload = json.loads(self.rfile.read(content_length))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Provide a valid JSON runId."})
            return
        run_id = payload.get("runId") if isinstance(payload, dict) else None
        with JAVAFX_PROCESS_LOCK:
            record = JAVAFX_PROCESSES.get(run_id)
        if record is None:
            self._send_json(404, {"error": "JavaFX run was not found or has already finished."})
            return

        process = record["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        record = release_javafx_process(run_id)
        if record is None:
            self._send_json(404, {"error": "JavaFX run has already finished."})
            return
        output = record["log_path"].read_text(encoding="utf-8", errors="replace").strip()
        record["temporary_directory"].cleanup()
        self._send_json(200, {"success": True, "running": False, "output": output or "JavaFX application stopped."})


if __name__ == "__main__":
    if shutil.which("javac") is None or shutil.which("java") is None:
        raise SystemExit("A JDK with both javac and java must be installed and available on PATH.")

    try:
        with ThreadingHTTPServer((HOST, PORT), JavaRunnerHandler) as server:
            print(f"Serving this folder at {SERVER_ORIGIN}")
            print("Open http://127.0.0.1:8000/Unit4JAVAFXUI.html to run JavaFX examples.")
            print("Press Ctrl+C to stop the local Java runner.")
            server.serve_forever()
    except OSError as error:
        raise SystemExit(f"Could not start the local server on {SERVER_ORIGIN}: {error}") from error
