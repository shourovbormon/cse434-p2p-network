"""Application-level protocol: [4-byte big-endian length][JSON payload]."""
import json
import struct

HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MB cap on JSON messages (files are sent raw, not as JSON)
CHUNK_SIZE = 64 * 1024

# Message types
HELLO = "hello"
HELLO_ACK = "hello_ack"
TEXT = "text"
FILE = "file"


def recv_exact(sock, n):
    """Read exactly n bytes from the socket (recv may return fewer)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by remote peer")
        buf.extend(chunk)
    return bytes(buf)


def send_message(sock, obj):
    """Encode a dict as JSON and send it with a 4-byte length prefix."""
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_message(sock):
    """Read one framed JSON message and return it as a dict."""
    length = struct.unpack("!I", recv_exact(sock, HEADER_SIZE))[0]
    if length == 0 or length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Invalid message length: {length}")
    return json.loads(recv_exact(sock, length).decode("utf-8"))


def make_hello(peer_id, name, port, ack=False):
    return {"type": HELLO_ACK if ack else HELLO,
            "peer_id": peer_id, "peer_name": name, "port": port}


def make_text(peer_id, name, message):
    return {"type": TEXT, "sender_id": peer_id, "sender_name": name, "message": message}


def make_file_meta(peer_id, name, filename, filesize):
    return {"type": FILE, "sender_id": peer_id, "sender_name": name,
            "filename": filename, "filesize": filesize}
