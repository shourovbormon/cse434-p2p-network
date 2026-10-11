"""P2P node: every peer is a TCP server (accepts) and a TCP client (connects)."""
import os
import socket
import threading
import uuid

import protocol as proto


def _close_socket(sock):
    """shutdown() first so threads blocked in recv() wake up and the remote sees EOF."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class Peer:
    def __init__(self, peer_id, name, sock, ip, listen_port):
        self.peer_id = peer_id
        self.name = name
        self.sock = sock
        self.ip = ip
        self.listen_port = listen_port
        self.send_lock = threading.Lock()  # keeps text/file bytes from interleaving

    def display(self):
        return f"{self.name} [{self.peer_id}] {self.ip}:{self.listen_port}"


class P2PNode:
    def __init__(self, name, port, on_event=print, on_peers_changed=lambda: None,
                 download_dir="downloads", on_status=lambda text: None):
        self.name = name
        self.port = port
        self.peer_id = uuid.uuid4().hex[:8]
        self.on_event = on_event
        self.on_status = on_status      # short text for the GUI status bar
        self.on_peers_changed = on_peers_changed
        self.download_dir = download_dir
        self.peers = {}                 # peer_id -> Peer
        self.peers_lock = threading.Lock()
        self.server = None
        self.running = False

    # ---------------- server role ----------------
    def start(self):
        os.makedirs(self.download_dir, exist_ok=True)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("0.0.0.0", self.port))
        self.server.listen()
        self.server.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_incoming, args=(conn, addr),
                             daemon=True).start()

    def _handle_incoming(self, conn, addr):
        try:
            conn.settimeout(10)
            hello = proto.recv_message(conn)
            if hello.get("type") != proto.HELLO:
                raise ValueError("Expected HELLO")
            peer = Peer(hello["peer_id"], hello["peer_name"], conn, addr[0], int(hello["port"]))
            if peer.peer_id == self.peer_id or not self._add_peer(peer):
                conn.close()
                return
            proto.send_message(conn, proto.make_hello(self.peer_id, self.name, self.port, ack=True))
            conn.settimeout(None)
        except (OSError, ValueError, KeyError) as e:
            self.on_event(f"[ERROR] Incoming handshake failed: {e}")
            conn.close()
            return
        self.on_event(f"[SYSTEM] Connected to {peer.name}")
        self.on_status(f"Connected to {peer.name} ({peer.ip}:{peer.listen_port})")
        self._reader_loop(peer)

    # ---------------- client role ----------------
    def connect(self, ip, port):
        """Connect to another peer. Returns True on success."""
        try:
            port = int(port)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.on_event("[ERROR] Invalid port (must be 1-65535)")
            return False
        try:
            socket.gethostbyname(ip)
        except (socket.gaierror, UnicodeError):
            self.on_event(f"[ERROR] Invalid IP address: {ip}")
            return False
        try:
            sock = socket.create_connection((ip, port), timeout=5)
        except OSError as e:
            self.on_event(f"[ERROR] Connection failed: {e}")
            return False
        try:
            proto.send_message(sock, proto.make_hello(self.peer_id, self.name, self.port))
            ack = proto.recv_message(sock)
            if ack.get("type") != proto.HELLO_ACK:
                raise ValueError("Expected HELLO_ACK")
            peer = Peer(ack["peer_id"], ack["peer_name"], sock, ip, int(ack["port"]))
            if peer.peer_id == self.peer_id:
                raise ValueError("Cannot connect to yourself")
            if not self._add_peer(peer):
                raise ValueError("Already connected to this peer")
            sock.settimeout(None)
        except (OSError, ValueError, KeyError) as e:
            self.on_event(f"[ERROR] Handshake failed: {e}")
            sock.close()
            return False
        self.on_event(f"[SYSTEM] Connected to {peer.name}")
        self.on_event(f"[SYSTEM] Connected to {peer.name} ({ip}:{port})")
        self.on_status(f"Connected to {peer.name} ({ip}:{port})")
        threading.Thread(target=self._reader_loop, args=(peer,), daemon=True).start()
        return True

    # ---------------- peer bookkeeping ----------------
    def _add_peer(self, peer):
        with self.peers_lock:
            if peer.peer_id in self.peers:
                return False
            self.peers[peer.peer_id] = peer
        self.on_peers_changed()
        return True

    def _remove_peer(self, peer):
        with self.peers_lock:
            removed = self.peers.pop(peer.peer_id, None)
        _close_socket(peer.sock)
        if removed:
            self.on_event(f"[SYSTEM] {peer.name} disconnected")
            self.on_peers_changed()

    def get_peers(self):
        with self.peers_lock:
            return [(p.peer_id, p.display()) for p in self.peers.values()]

    # ---------------- receiving ----------------
    def _reader_loop(self, peer):
        try:
            while self.running:
                msg = proto.recv_message(peer.sock)
                kind = msg.get("type")
                if kind == proto.TEXT:
                    self.on_event(f"{peer.name} -> You: {msg.get('message', '')}")
                elif kind == proto.FILE:
                    self._receive_file(peer, msg)
                else:
                    self.on_event(f"[ERROR] Unknown message type from {peer.name}: {kind}")
        except (OSError, ValueError) as e:  # ConnectionError/JSON errors are subclasses
            if self.running:
                self.on_event(f"[ERROR] Connection with {peer.name} lost: {e}")
        finally:
            self._remove_peer(peer)

    def _receive_file(self, peer, meta):
        filename = os.path.basename(str(meta.get("filename", ""))) or "received_file"
        size = meta.get("filesize")
        if not isinstance(size, int) or size < 0:
            raise ValueError("Invalid file size in metadata")
        path = self._unique_path(filename)
        self.on_status(f"Receiving {filename} ({size} bytes) from {peer.name}...")
        remaining = size
        try:
            with open(path, "wb") as f:
                while remaining > 0:
                    chunk = peer.sock.recv(min(proto.CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("Peer disconnected during file transfer")
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            if os.path.exists(path):
                os.remove(path)  # discard incomplete file
            raise
        self.on_event(f"{peer.name} -> You: File received: {os.path.basename(path)}")
        self.on_status(f"Received: {path}")

    def _unique_path(self, filename):
        path = os.path.join(self.download_dir, filename)
        base, ext = os.path.splitext(filename)
        n = 1
        while os.path.exists(path):
            path = os.path.join(self.download_dir, f"{base}_{n}{ext}")
            n += 1
        return path

    # ---------------- sending ----------------
    def _get_peer(self, peer_id):
        with self.peers_lock:
            return self.peers.get(peer_id)

    def send_text(self, peer_id, text):
        peer = self._get_peer(peer_id)
        if not peer:
            self.on_event("[ERROR] Peer is not connected")
            return False
        try:
            with peer.send_lock:
                proto.send_message(peer.sock, proto.make_text(self.peer_id, self.name, text))
        except OSError as e:
            self.on_event(f"[ERROR] Send failed: {e}")
            self._remove_peer(peer)
            return False
        self.on_event(f"You -> {peer.name}: {text}")
        return True

    def send_file(self, peer_id, path):
        peer = self._get_peer(peer_id)
        if not peer:
            self.on_event("[ERROR] Peer is not connected")
            return False
        if not os.path.isfile(path):
            self.on_event(f"[ERROR] File does not exist: {path}")
            return False
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        try:
            with peer.send_lock:
                proto.send_message(peer.sock,
                                   proto.make_file_meta(self.peer_id, self.name, filename, size))
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(proto.CHUNK_SIZE)
                        if not chunk:
                            break
                        peer.sock.sendall(chunk)
        except OSError as e:
            self.on_event(f"[ERROR] File send failed: {e}")
            self._remove_peer(peer)
            return False
        self.on_event(f"You -> {peer.name}: File sent: {filename}")
        self.on_status(f"Sent: {filename} ({size} bytes) to {peer.name}")
        return True

    # ---------------- shutdown ----------------
    def stop(self):
        self.running = False
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
        with self.peers_lock:
            peers = list(self.peers.values())
            self.peers.clear()
        for p in peers:
            _close_socket(p.sock)
        self.on_peers_changed()


if __name__ == "__main__":
    # Running this file directly opens the GUI (same as: python main.py)
    import main
    main.run()
