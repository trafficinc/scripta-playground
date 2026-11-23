#!/usr/bin/env python3
"""End-to-end HTTP checks; requires loopback socket permission."""
import argparse
import concurrent.futures
import http.client
import json
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--scr", type=Path, help="Interpreter override; omit to test runtime discovery")
    parser.add_argument("--expect-interpreter", choices=("bundled", "installed"))
    args = parser.parse_args()
    command = [str(args.binary.resolve()), "--no-open"]
    if args.scr:
        command.extend(["--scr", str(args.scr.resolve())])
    process = subprocess.Popen(command,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        lines = queue.Queue()

        def read_lines():
            for line in process.stdout:
                lines.put(line.strip())
            lines.put(None)

        threading.Thread(target=read_lines, daemon=True).start()
        deadline = time.monotonic() + 10
        interpreter_line = ""
        while True:
            line = lines.get(timeout=max(.1, deadline - time.monotonic()))
            if line is None:
                raise RuntimeError("Playground did not start: " + process.stderr.read())
            if line.startswith("Interpreter:"):
                interpreter_line = line
            if line.startswith("http://"):
                address = urlsplit(line)
                break
        if args.expect_interpreter:
            assert interpreter_line.endswith(f"({args.expect_interpreter})"), interpreter_line

        def request(path, body=None, override=None):
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=20)
            headers = {"Host": address.netloc}
            if body is not None:
                headers.update({"Origin": f"http://{address.netloc}", "Content-Type": "application/json",
                                "X-Playground-Token": address.fragment})
            headers.update(override or {})
            connection.request("POST" if body is not None else "GET", path,
                               json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result

        for path in ("/", "/app.js", "/style.css"):
            status, data = request(path)
            assert status == 200 and address.fragment.encode() not in data
        # Browsers and extensions may repeat list-valued headers. Such a
        # stylesheet request must not be rejected as an ambiguous API request.
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
        connection.putrequest("GET", "/style.css", skip_host=True)
        connection.putheader("Host", address.netloc)
        connection.putheader("Accept", "text/css")
        connection.putheader("Accept", "*/*;q=0.1")
        connection.putheader("Cache-Control", "no-cache")
        connection.putheader("Cache-Control", "max-age=0")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 200 and response.getheader("Content-Type").startswith("text/css")
        assert response.read()
        connection.close()
        # Chrome may open a speculative TCP connection and send the stylesheet
        # request later. Exercise both idle time beyond the former 3s timeout
        # and headers arriving in separate writes.
        with socket.create_connection((address.hostname, address.port), timeout=10) as preconnect:
            time.sleep(4)
            preconnect.sendall(b"GET /style.css HTTP/1.1\r\n")
            time.sleep(.1)
            preconnect.sendall(f"Host: {address.netloc}\r\nAccept: text/css\r\n\r\n".encode())
            delayed_response = http.client.HTTPResponse(preconnect)
            delayed_response.begin()
            assert delayed_response.status == 200
            assert delayed_response.getheader("Content-Type").startswith("text/css")
            assert delayed_response.read()
        assert request("/", override={"Host": "attacker.example"})[0] == 403
        assert request("/run", {"source": "echo 1;"}, {"Origin": "https://attacker.example"})[0] == 403
        assert request("/run", {"source": "echo 1;"}, {"X-Playground-Token": "wrong"})[0] == 403
        status, data = request("/run", {"source": 'echo "hello\\n"; exit(7);'})
        result = json.loads(data)
        assert status == 200 and result["stdout"] == "hello\n" and result["exitCode"] == 7
        status, data = request("/run", {"source": "echo ;"})
        assert status == 200 and json.loads(data)["exitCode"] == 1
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(request, "/run", {"source": "sleep(60000);"})
            # Wait until a second run is rejected, proving execution has acquired the lock.
            deadline = time.monotonic() + 3
            while request("/run", {"source": ""})[0] != 409:
                assert time.monotonic() < deadline
                # The probe can acquire the lock before the worker request.
                # Retry that request if it lost the race to the probe.
                if future.done():
                    assert future.result()[0] == 409
                    future = executor.submit(request, "/run", {"source": "sleep(60000);"})
                time.sleep(.01)
            assert request("/stop", {})[0] == 200
            assert json.loads(future.result()[1])["reason"] == "Stopped"
        assert request("/shutdown", {})[0] == 200
        assert process.wait(timeout=5) == 0
        print("HTTP smoke passed: assets, host/origin/token protection, execution, errors, stop, quit")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
