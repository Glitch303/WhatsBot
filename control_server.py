"""
Headless service control server.

Listens on a Unix socket and handles control commands (status, start/stop,
settings toggles, away mode, model changes) and log streaming for the CLI.
Follows the hermes-agent pattern: local Unix socket, one JSON request per
connection (except log_stream), filesystem ACL as auth boundary.
"""

import json
import logging
import os
import socket
import socketserver
import threading
import time
from queue import Queue

logger = logging.getLogger("whatsbot.control")

# Socket path (in db/ directory) — use absolute path
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SOCKET_PATH = os.path.join(_PROJECT_DIR, "db", "whatsbot.sock")


class LogStreamHandler(logging.Handler):
    """Logging handler that broadcasts formatted records to connected log_stream clients."""

    def __init__(self, clients, lock):
        super().__init__()
        self.clients = clients  # list of socket objects
        self.lock = lock

    def emit(self, record):
        try:
            msg = self.format(record)
            payload = json.dumps({
                "ts": record.created,
                "level": record.levelname,
                "msg": msg,
            })
            with self.lock:
                for sock in list(self.clients):
                    try:
                        sock.send((payload + "\n").encode())
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
        except Exception:
            pass  # Never crash the logging thread


class ControlServer(socketserver.ThreadingUnixStreamServer):
    """Threading Unix socket server for headless service control."""

    allow_reuse_address = True
    request_timeout = 30

    def __init__(self, socket_path, callbacks, log_queue):
        self.callbacks = callbacks  # {"connect": fn, "disconnect": fn, ...}
        self.log_queue = log_queue  # Queue for log records
        self.log_clients = []
        self.log_lock = threading.Lock()
        self.cmd_lock = threading.Lock()
        self.running = True
        # Remove stale socket file
        import os
        if os.path.exists(socket_path):
            os.remove(socket_path)
        super().__init__(socket_path, self._handler_factory())

    def _handler_factory(self):
        server = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    data = self.request.recv(4096).decode()
                    if not data:
                        return
                    req = json.loads(data)
                    verb = req.get("verb")

                    if verb == "command":
                        cmd = req.get("cmd", "")
                        args = req.get("args", {})
                        with server.cmd_lock:
                            result = server._execute_command(cmd, args)
                        self.request.send(json.dumps(result).encode())
                    elif verb == "log_stream":
                        server._handle_log_stream(self.request)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    try:
                        self.request.send(json.dumps({"ok": False, "error": f"bad request: {e}"}).encode())
                    except OSError:
                        pass
                except Exception as e:
                    logger.error(f"Control server error: {e}")
                    try:
                        self.request.send(json.dumps({"ok": False, "error": str(e)}).encode())
                    except OSError:
                        pass
                finally:
                    try:
                        self.request.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.request.close()

        return Handler

    def _execute_command(self, cmd, args):
        cb = self.callbacks
        try:
            if cmd == "status":
                return {"ok": True, "data": cb["status"]()}
            elif cmd == "connect":
                ok = cb["connect"]()
                return {"ok": True, "data": {"connected": ok}}
            elif cmd == "disconnect":
                cb["disconnect"]()
                return {"ok": True, "data": {"disconnected": True}}
            elif cmd == "start":
                cb["start"]()
                return {"ok": True, "data": {"started": True}}
            elif cmd == "stop":
                cb["stop"]()
                return {"ok": True, "data": {"stopped": True}}
            elif cmd == "quit":
                cb["quit"]()
                return {"ok": True, "data": {"quit": True}}
            elif cmd == "toggle":
                key = args.get("setting", "")
                value = args.get("value", None)
                if value is not None:
                    ok, msg = cb["toggle"](key, value)
                else:
                    # Toggle (flip) the current value
                    current = cb["get_setting"](key)
                    ok, msg = cb["toggle"](key, not current)
                return {"ok": ok, "data": {"message": msg}}
            elif cmd == "away":
                st = cb["away"](args.get("state", "auto"))
                return {"ok": True, "data": st}
            elif cmd == "away_status":
                return {"ok": True, "data": cb["away_status"]()}
            elif cmd == "model_provider":
                cb["model_provider"](args.get("provider", ""))
                return {"ok": True, "data": {"provider": args.get("provider", "")}}
            elif cmd == "model_name":
                cb["model_name"](args.get("model", ""))
                return {"ok": True, "data": {"model": args.get("model", "")}}
            else:
                return {"ok": False, "error": f"unknown command: {cmd}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _handle_log_stream(self, request):
        """Persistent connection for log streaming until client disconnects."""
        with self.log_lock:
            self.log_clients.append(request)
        try:
            # Send heartbeat to confirm stream started
            request.send(json.dumps({"ts": time.time(), "level": "INFO", "msg": "[log stream started]"}).encode() + b"\n")
            # Keep connection open; client disconnects when done
            while self.running:
                try:
                    request.settimeout(1.0)
                    data = request.recv(1)
                    if not data:
                        break
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            with self.log_lock:
                if request in self.log_clients:
                    self.log_clients.remove(request)

    def broadcast_log(self, record):
        """Push a log record to all connected log_stream clients."""
        try:
            msg = record.getMessage()
            payload = json.dumps({
                "ts": record.created,
                "level": record.levelname,
                "msg": msg,
            })
            with self.log_lock:
                for sock in list(self.log_clients):
                    try:
                        sock.send((payload + "\n").encode())
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
        except Exception:
            pass

    def serve(self):
        """Run the server in a background thread."""
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        logger.info(f"Control server listening on {self.server_address}")

    def shutdown(self):
        """Stop the server."""
        self.running = False
        self.server_close()
        logger.info("Control server stopped")


def start_control_server(socket_path, callbacks, log_queue=None):
    """Start the control server. Returns the server instance."""
    server = ControlServer(socket_path, callbacks, log_queue)
    server.serve()
    return server


def stop_control_server(server):
    """Stop the control server."""
    if server:
        server.shutdown()


def send_command(socket_path, verb, cmd=None, args=None):
    """Send a one-off command to the control server. Returns response dict."""
    req = {"verb": verb}
    if cmd is not None:
        req["cmd"] = cmd
    if args is not None:
        req["args"] = args
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(socket_path)
        s.send(json.dumps(req).encode())
        data = s.recv(65536).decode()
        return json.loads(data)


def stream_logs(socket_path):
    """Generator that yields log records from the control server."""
    req = {"verb": "log_stream"}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(None)
        s.connect(socket_path)
        s.send(json.dumps(req).encode())
        buffer = ""
        while True:
            try:
                data = s.recv(4096).decode()
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            pass
            except (socket.timeout, OSError):
                break
