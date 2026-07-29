import socket
import struct
import threading

import pytest

from runtime import rcon


def _pack(request_id: int, packet_type: int, payload: str) -> bytes:
    body = payload.encode("utf-8") + b"\x00\x00"
    length = 4 + 4 + len(body)
    return struct.pack("<iii", length, request_id, packet_type) + body


def _read_packet(sock: socket.socket) -> tuple[int, int, str]:
    (length,) = struct.unpack("<i", sock.recv(4))
    body = sock.recv(length)
    request_id, packet_type = struct.unpack("<ii", body[:8])
    return request_id, packet_type, body[8:-2].decode("utf-8")


def _run_fake_server(server_sock: socket.socket, expected_password: str, response: str):
    conn, _addr = server_sock.accept()
    with conn:
        request_id, _packet_type, payload = _read_packet(conn)
        if payload != expected_password:
            conn.sendall(_pack(-1, 2, ""))
            return
        conn.sendall(_pack(request_id, 2, ""))

        _request_id, _packet_type, _command = _read_packet(conn)
        conn.sendall(_pack(2, 0, response))


@pytest.fixture
def fake_rcon_server():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    yield server_sock, port
    server_sock.close()


def test_execute_returns_command_output(fake_rcon_server):
    server_sock, port = fake_rcon_server
    thread = threading.Thread(target=_run_fake_server, args=(server_sock, "correct-pass", "There are 0 of a max of 20 players online:"))
    thread.start()

    output = rcon.execute("127.0.0.1", port, "correct-pass", "list", timeout=2.0)

    thread.join(timeout=2)
    assert output == "There are 0 of a max of 20 players online:"


def test_execute_raises_on_bad_password(fake_rcon_server):
    server_sock, port = fake_rcon_server
    thread = threading.Thread(target=_run_fake_server, args=(server_sock, "correct-pass", "irrelevant"))
    thread.start()

    with pytest.raises(rcon.RconAuthError):
        rcon.execute("127.0.0.1", port, "wrong-pass", "list", timeout=2.0)

    thread.join(timeout=2)


def test_execute_raises_on_connection_refused():
    with pytest.raises((ConnectionRefusedError, OSError)):
        rcon.execute("127.0.0.1", 1, "pw", "list", timeout=1.0)
