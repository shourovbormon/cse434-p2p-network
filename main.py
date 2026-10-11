"""Tkinter GUI for the UAP P2P network assignment. Run: python main.py"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from p2p_node import P2PNode


class App:
    def __init__(self, root):
        self.root = root
        root.title("UAP P2P Network")
        self.node = None
        self.events = queue.Queue()
        self.peer_ids = []
        self._build_ui()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI (layout follows Figures 3-5 of the assignment) ----------
    def _build_ui(self):
        pad = {"padx": 5, "pady": 3}

        # --- My Peer ---
        f = ttk.LabelFrame(self.root, text="My Peer")
        f.pack(fill="x", padx=8, pady=(8, 3))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Name:").pack(side="left", **pad)
        self.name_var = tk.StringVar(value="Alice")
        self.name_entry = ttk.Entry(row, textvariable=self.name_var, width=14)
        self.name_entry.pack(side="left", **pad)
        ttk.Label(row, text="Port:").pack(side="left", **pad)
        self.port_var = tk.StringVar(value="5000")
        self.port_entry = ttk.Entry(row, textvariable=self.port_var, width=8)
        self.port_entry.pack(side="left", **pad)
        self.start_btn = ttk.Button(row, text="Start Peer", command=self.start_peer)
        self.start_btn.pack(side="left", **pad)
        self.stop_btn = ttk.Button(row, text="Stop", command=self.stop_peer, state="disabled")
        self.stop_btn.pack(side="left", **pad)
        self.info_var = tk.StringVar(value="Peer not started")
        ttk.Label(f, textvariable=self.info_var).pack(anchor="w", **pad)

        # --- Connect to Another Peer ---
        c = ttk.LabelFrame(self.root, text="Connect to Another Peer")
        c.pack(fill="x", padx=8, pady=3)
        ttk.Label(c, text="IP:").pack(side="left", **pad)
        self.ip_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(c, textvariable=self.ip_var, width=16).pack(side="left", **pad)
        ttk.Label(c, text="Port:").pack(side="left", **pad)
        self.rport_var = tk.StringVar(value="5001")
        ttk.Entry(c, textvariable=self.rport_var, width=8).pack(side="left", **pad)
        ttk.Button(c, text="Connect", command=self.connect_peer).pack(side="left", **pad)

        # --- Status bar ---
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w"
                  ).pack(fill="x", side="bottom")

        # --- Send File ---
        fl = ttk.LabelFrame(self.root, text="Send File")
        fl.pack(fill="x", side="bottom", padx=8, pady=3)
        ttk.Label(fl, text="Text, image, audio, video, PDF, ZIP, etc.").pack(side="left", **pad)
        ttk.Button(fl, text="Choose File & Send", command=self.send_file).pack(side="right", **pad)

        # --- Send Text ---
        t = ttk.LabelFrame(self.root, text="Send Text")
        t.pack(fill="x", side="bottom", padx=8, pady=3)
        self.msg_var = tk.StringVar()
        e = ttk.Entry(t, textvariable=self.msg_var)
        e.grid(row=0, column=0, sticky="ew", padx=5, pady=4)
        e.bind("<Return>", lambda _e: self.send_text())
        ttk.Button(t, text="Send", command=self.send_text).grid(row=0, column=1, padx=5, pady=4)
        t.columnconfigure(0, weight=1)

        # --- Connected Peers | Messages / Events ---
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=True, padx=8, pady=3)
        pf = ttk.LabelFrame(mid, text="Connected Peers")
        pf.pack(side="left", fill="y")
        self.peer_list = tk.Listbox(pf, width=30, exportselection=False,
                                    height=12, font=("Segoe UI", 9), activestyle="none",
                                    highlightthickness=1, relief="flat", borderwidth=1)
        self.peer_list.pack(fill="both", expand=True, padx=4, pady=4)

        lf = ttk.LabelFrame(mid, text="Messages / Events")
        lf.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.log = tk.Text(lf, state="disabled", wrap="word", height=12, font=("Courier New", 9),
                           highlightthickness=1, relief="flat", borderwidth=1)
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

        # let the window size itself to fit everything, and never shrink below that
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_reqwidth(), self.root.winfo_reqheight())

    # ---------- helpers ----------
    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_event(self, text):          # called from network threads
        self.events.put(("log", text))

    def _on_status(self, text):         # called from network threads
        self.events.put(("status", text))

    def _on_peers_changed(self):        # called from network threads
        self.events.put(("peers", None))

    def _poll(self):                    # runs in the Tk thread
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.log_line(data)
                    if data.startswith("[ERROR]"):
                        self.status.set(data)
                elif kind == "status":
                    self.status.set(data)
                else:
                    self.refresh_peers()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def refresh_peers(self):
        selected = self.selected_peer_id()
        self.peer_list.delete(0, "end")
        self.peer_ids = []
        if self.node:
            for pid, label in self.node.get_peers():
                self.peer_ids.append(pid)
                self.peer_list.insert("end", label)
                if pid == selected:
                    self.peer_list.selection_set("end")

    def selected_peer_id(self):
        sel = self.peer_list.curselection()
        return self.peer_ids[sel[0]] if sel and sel[0] < len(self.peer_ids) else None

    def _target_peer(self):
        """Selected peer; if nothing is selected but exactly one peer is connected, use it."""
        if not self.node or not self.node.running:
            self.log_line("[ERROR] Start your peer first")
            return None
        pid = self.selected_peer_id()
        if pid is None and len(self.peer_ids) == 1:
            pid = self.peer_ids[0]
        if pid is None:
            self.log_line("[ERROR] Select a peer from the Connected Peers list first")
        return pid

    # ---------- actions ----------
    def start_peer(self):
        name = self.name_var.get().strip()
        if not name:
            self.log_line("[ERROR] Enter a peer name")
            return
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.log_line("[ERROR] Invalid listening port")
            return
        node = P2PNode(name, port, self._on_event, self._on_peers_changed,
                       on_status=self._on_status)
        try:
            node.start()
        except OSError as e:
            self.log_line(f"[ERROR] Could not start peer: {e}")
            return
        self.node = node
        self.info_var.set(f"{name} | ID: {node.peer_id} | Port: {port}")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.name_entry.configure(state="disabled")
        self.port_entry.configure(state="disabled")
        msg = f"Peer started: {name} [{node.peer_id}] on port {port}"
        self.log_line(f"[SYSTEM] {msg}")
        self.status.set(msg)

    def stop_peer(self):
        if self.node:
            self.node.stop()
            self.node = None
        self.info_var.set("Peer not started")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.name_entry.configure(state="normal")
        self.port_entry.configure(state="normal")
        self.refresh_peers()
        self.log_line("[SYSTEM] Peer stopped")
        self.status.set("Peer stopped")

    def connect_peer(self):
        if not self.node or not self.node.running:
            self.log_line("[ERROR] Start your peer first")
            return
        ip, port = self.ip_var.get().strip(), self.rport_var.get().strip()
        threading.Thread(target=self.node.connect, args=(ip, port), daemon=True).start()

    def send_text(self):
        pid = self._target_peer()
        if pid is None:
            return
        text = self.msg_var.get().strip()
        if not text:
            return
        self.msg_var.set("")
        threading.Thread(target=self.node.send_text, args=(pid, text), daemon=True).start()

    def send_file(self):
        pid = self._target_peer()
        if pid is None:
            return
        path = filedialog.askopenfilename(title="Choose a file to send")
        if path:
            threading.Thread(target=self.node.send_file, args=(pid, path), daemon=True).start()

    def on_close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


def run():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    run()
