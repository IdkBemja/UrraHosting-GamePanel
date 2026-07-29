"""Minimal Source RCON client (dependency-free), used internally by
game_control_agent.py to talk to a Minecraft Java/Bedrock process listening
on 127.0.0.1 inside the SAME container - never across the network, never by
the dashboard directly. Direct port of
UrraHosting-MinecraftServer/dashboard/app/services/rcon.py.

Never logs the password; callers must also avoid logging it.
"""

from __future__ import annotations

import socket
import struct

_TYPE_AUTH = 3
_TYPE_COMMAND = 2


class RconError(RuntimeError):
    pass


class RconAuthError(RconError):
    pass


def _pack(request_id: int, packet_type: int, payload: str) -> bytes:
    body = payload.encode("utf-8") + b"\x00\x00"
    length = 4 + 4 + len(body)
    return struct.pack("<iii", length, request_id, packet_type) + body


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RconError("Conexion RCON cerrada inesperadamente")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_packet(sock: socket.socket) -> tuple[int, int, str]:
    (length,) = struct.unpack("<i", _recv_exact(sock, 4))
    body = _recv_exact(sock, length)
    request_id, packet_type = struct.unpack("<ii", body[:8])
    payload = body[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, payload


def execute(host: str, port: int, password: str, command: str, *, timeout: float = 5.0) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(_pack(1, _TYPE_AUTH, password))
        request_id, _packet_type, _payload = _read_packet(sock)
        if request_id == -1:
            raise RconAuthError("Autenticacion RCON fallida")

        sock.sendall(_pack(2, _TYPE_COMMAND, command))
        _request_id, _packet_type, payload = _read_packet(sock)
        return payload
