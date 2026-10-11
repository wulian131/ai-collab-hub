"""Small, bounded WebSocket transport for authenticated room notifications."""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from urllib.parse import urlsplit

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_FRAME = 8192


def accept_key(key):
    if len(base64.b64decode(key, validate=True)) != 16:
        raise ValueError('Invalid WebSocket key')
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def read_exact(stream, count):
    chunks = bytearray()
    while len(chunks) < count:
        part = stream.read(count - len(chunks))
        if not part:
            raise EOFError('Notification connection closed')
        chunks.extend(part)
    return bytes(chunks)


def read_frame(stream, masked):
    header = read_exact(stream, 2)
    if not header[0] & 0x80 or header[0] & 0x70:
        raise ValueError('Fragmented or extended frames unsupported')
    opcode = header[0] & 15
    if bool(header[1] & 0x80) != masked:
        raise ValueError('Invalid frame masking')
    length = header[1] & 127
    if length == 126:
        length = struct.unpack('!H', read_exact(stream, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', read_exact(stream, 8))[0]
    if length > MAX_FRAME or (opcode >= 8 and length > 125):
        raise ValueError('Notification frame too large')
    mask = read_exact(stream, 4) if masked else b''
    data = read_exact(stream, length)
    if masked:
        data = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
    return opcode, data


def frame(data, opcode=1, masked=False):
    if isinstance(data, str):
        data = data.encode()
    if len(data) > MAX_FRAME:
        raise ValueError('Notification frame too large')
    size = len(data)
    header = bytes([0x80 | opcode, (0x80 if masked else 0) | min(size, 126)])
    if size >= 126:
        header += struct.pack('!H', size)
    if masked:
        mask = os.urandom(4)
        header += mask
        data = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
    return header + data


class NotificationClient:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Invalid notification URL')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Remote notification connections require HTTPS')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        connection = socket.create_connection((parsed.hostname, port), timeout=20)
        self.socket = connection
        try:
            if parsed.scheme == 'https':
                self.socket = ssl.create_default_context().wrap_socket(connection, server_hostname=parsed.hostname)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.stream = self.socket.makefile('rb', buffering=0)
            key = base64.b64encode(os.urandom(16)).decode()
            host = parsed.netloc
            path = parsed.path.rstrip('/') + '/api/notifications'
            if any(ord(c) < 33 for c in path):
                raise ValueError('Invalid notification path')
            self.socket.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: ' + host + '\r\n'
                'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                'Sec-WebSocket-Key: ' + key + '\r\n\r\n').encode())
            status = self.stream.readline(4096)
            if b' 101 ' not in status:
                raise OSError('Notification upgrade refused')
            headers = {}
            total = len(status)
            while True:
                line = self.stream.readline(4096)
                total += len(line)
                if total > 16384 or not line:
                    raise ValueError('Invalid notification upgrade')
                if line == b'\r\n':
                    break
                field, value = line.decode().split(':', 1)
                headers[field.lower()] = value.strip()
            if headers.get('sec-websocket-accept') != accept_key(key) or headers.get('upgrade', '').lower() != 'websocket':
                raise ValueError('Invalid notification upgrade response')
            self.socket.sendall(frame(json.dumps({'token': token}), masked=True))
            initial = self.receive()
            if initial.get('type') == 'error':
                raise PermissionError(initial.get('error', 'Notification authentication refused'))
            if initial.get('type') != 'state':
                raise ValueError('Missing initial notification state')
            self.initial = initial
            self.socket.settimeout(None)
        except BaseException:
            self.close()
            raise

    def receive(self):
        while True:
            opcode, data = read_frame(self.stream, masked=False)
            if opcode == 8:
                raise EOFError('Notification connection closed')
            if opcode == 9:
                self.socket.sendall(frame(data, opcode=10, masked=True))
                continue
            if opcode == 10:
                continue
            if opcode != 1:
                raise ValueError('Unexpected notification frame')
            result = json.loads(data.decode())
            if not isinstance(result, dict):
                raise ValueError('Invalid notification payload')
            return result

    def close(self):
        if hasattr(self, 'stream'):
            self.stream.close()
        if hasattr(self, 'socket'):
            self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
