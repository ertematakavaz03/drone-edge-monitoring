"""
Central Server – receives data from Drone and displays it in a GUI dashboard.
"""

import argparse
import socket
import re
import json
import threading
import time
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from collections import deque

serverState = {
    "aggregated": [],
    "logs": []
}
stateLock = threading.Lock()


def logRecord(message: str):
    """
    Append a timestamped log message to the serverState logs,
    trimming the list to the most recent 500 entries.
    """
    entryTime = time.strftime('%H:%M:%S')
    with stateLock:
        serverState["logs"].append(f"{entryTime}  {message}")
        if len(serverState["logs"]) > 500:
            serverState["logs"] = serverState["logs"][-500:]


def processDrone(droneSocket: socket.socket, addr):
    """
    Handle a drone connection: receive newline-delimited JSON packets,
    update aggregated data, log events and handle disconnections/errors.
    """
    logRecord(f"Drone connected from {addr}")
    droneSocket.settimeout(1.0)
    dataBuffer = ""

    try:
        while True:
            try:
                dataPiece = droneSocket.recv(1024).decode()
            except socket.timeout:
                dataPiece = None
            if dataPiece is None:
                pass
            elif dataPiece == "":
                break
            else:
                dataBuffer += dataPiece
                while "\n" in dataBuffer:
                    jsonLine, dataBuffer = dataBuffer.split("\n", 1)
                    try:
                        data = json.loads(jsonLine)
                        with stateLock:
                            serverState["aggregated"].append(data)
                            if len(serverState["aggregated"]) > 100:
                                serverState["aggregated"] = serverState["aggregated"][-100:]
                        logRecord(f"Received from drone: {data}")

                    except json.JSONDecodeError as e:
                        logRecord(f"Invalid JSON from drone: {e}")

    except Exception as e:
        logRecord(f"Connection error: {e}")
    finally:
        droneSocket.close()
        logRecord(f"Drone {addr} disconnected")


def csGUI():
    """
    Build and return the tkinter GUI for the Central Server Dashboard,
    displaying latest readings, event logs, and per-sensor graphs.
    """

    root = tk.Tk()
    root.title("Central Server Dashboard")
    root.geometry("900x700")

    # ——— 1) Latest Aggregated Readings
    tk.Label(root, text="Latest Aggregated Readings:").pack(
        anchor="w", padx=10, pady=(10, 0))
    readingBox = ScrolledText(root, height=8, state="disabled",
                              bg="#2e2e2e", fg="#cccccc", insertbackground="#cccccc")
    readingBox.pack(fill="x", padx=10, pady=(0, 10))

    # ——— 2) Event Log
    tk.Label(root, text="Event Log:").pack(anchor="w", padx=10)
    logBox = ScrolledText(root, height=6, state="disabled",
                          bg="#1e1e1e", fg="#bbbbbb", insertbackground="#bbbbbb")
    logBox.pack(fill="x", padx=10, pady=(0, 10))

    # tag for anomalies
    logBox.tag_configure("anom", foreground="red")

    # ——— 3) Scrollable container for sensor graphs
    container = tk.Frame(root)
    container.pack(fill="both", expand=True, padx=10, pady=10)
    canvas = tk.Canvas(container)
    vsb = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    scrollable_frame = tk.Frame(canvas)
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

    def on_configure(e):
        canvas.configure(scrollregion=canvas.bbox("all"))
    scrollable_frame.bind("<Configure>", on_configure)

    # ——— storage for each sensor’s widgets/buffers
    graph_widgets: dict[str, dict] = {}
    max_len = 50

    def create_graph_panel(sid: str):
        panel = tk.Frame(scrollable_frame, bd=1, relief="sunken")
        panel.pack(fill="x", pady=5)

        # Header + zoom controls
        hdr = tk.Frame(panel)
        hdr.pack(fill="x", padx=5, pady=(5, 0))
        tk.Label(hdr, text=f"Sensor {sid}", font=("Arial", 12, "bold"))\
            .pack(side="left")
        ctrl = tk.Frame(hdr)
        ctrl.pack(side="right")
        tk.Button(ctrl, text="+", width=2,
                  command=lambda s=sid: zoom(s, 1.2)).pack(side="left", padx=2)
        tk.Button(ctrl, text="–", width=2,
                  command=lambda s=sid: zoom(s, 1/1.2)).pack(side="left")

        # Graph canvas
        cnv = tk.Canvas(panel, height=150, bg="#ffffff")
        cnv.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        # panning
        cnv.bind("<ButtonPress-1>", lambda e, c=cnv: c.scan_mark(e.x, e.y))
        cnv.bind("<B1-Motion>", lambda e,
                 c=cnv: c.scan_dragto(e.x, e.y, gain=1))

        graph_widgets[sid] = {
            "canvas": cnv,
            "temp_buf": deque(maxlen=max_len),
            "hum_buf":  deque(maxlen=max_len),
            "zoom": 1.0
        }

    def zoom(sid: str, factor: float):
        gw = graph_widgets[sid]
        gw["zoom"] *= factor
        redraw_panel(sid)

    # numeric sort helper
    def keyfn(name):
        m = re.search(r'(\d+)$', name)
        return int(m.group(1)) if m else float('inf')

    def redraw_panel(sid: str):
        """Redraw only one panel with current zoom."""
        gw = graph_widgets[sid]
        c = gw["canvas"]
        buf_t = gw["temp_buf"]
        buf_h = gw["hum_buf"]
        z = gw["zoom"]

        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        # scale drawing by zoom
        def sx(x): return x * z
        def sy(y): return y * z

        allv = list(buf_t) + list(buf_h)
        vmin, vmax = (min(allv), max(allv)) if allv else (0, 1)
        if vmin == vmax:
            vmax += 1

        # temperature line
        pts_t = [
            (sx(i * w/(max_len-1)), sy(h - ((v-vmin)/(vmax-vmin))*h))
            for i, v in enumerate(buf_t)
        ]
        for i in range(len(pts_t)-1):
            c.create_line(*pts_t[i], *pts_t[i+1], fill="red", width=2)

        # humidity line
        pts_h = [
            (sx(i * w/(max_len-1)), sy(h - ((v-vmin)/(vmax-vmin))*h))
            for i, v in enumerate(buf_h)
        ]
        for i in range(len(pts_h)-1):
            c.create_line(*pts_h[i], *pts_h[i+1], fill="blue", width=2)

        # labels
        if pts_t:
            x, y = pts_t[-1]
            c.create_text(x+5, y-10, text=f"{buf_t[-1]:.1f}°C",
                          fill="red", font=("Arial", 9, "bold"), anchor="w")
        if pts_h:
            x, y = pts_h[-1]
            c.create_text(x+5, y+10, text=f"{buf_h[-1]:.1f}%",
                          fill="blue", font=("Arial", 9, "bold"), anchor="w")

        # update scrollregion
        c.configure(scrollregion=(0, 0, w*z, h*z))

    def refresh():
        with stateLock:
            # Latest readings
            readingBox.configure(state="normal")
            readingBox.delete("1.0", "end")
            for e in serverState["aggregated"][-10:]:
                line = (f"{e['timestamp']} | {e['sensor_id']} | "
                        f"T={e.get('avg_temp', e.get('temperature', '?'))}°C "
                        f"H={e.get('avg_hum',  e.get('humidity', '?'))}%")
                if "anomaly_count" in e:
                    line += f" | Anomalies={e['anomaly_count']}"
                readingBox.insert("end", line + "\n")
            readingBox.configure(state="disabled")

            # Event log
            logBox.configure(state="normal")
            logBox.delete("1.0", "end")
            for l in serverState["logs"][-100:]:
                tag = "anom" if "Anomaly" in l or l.startswith("⚠️") else None
                if tag:
                    logBox.insert("end", l + "\n", tag)
                else:
                    logBox.insert("end", l + "\n")
            logBox.configure(state="disabled")

            # Graph panels
            sensors = sorted({e["sensor_id"]
                             for e in serverState["aggregated"]}, key=keyfn)
            for sid in sensors:
                if sid not in graph_widgets:
                    create_graph_panel(sid)

                # push latest into buffer
                latest = next((x for x in reversed(
                    serverState["aggregated"]) if x["sensor_id"] == sid), {})
                t = latest.get("avg_temp", latest.get("temperature", None))
                h = latest.get("avg_hum",  latest.get("humidity",    None))
                if t is not None:
                    graph_widgets[sid]["temp_buf"].append(t)
                if h is not None:
                    graph_widgets[sid]["hum_buf"].append(h)

                # redraw after data update
                redraw_panel(sid)

        root.after(1000, refresh)

    refresh()
    return root


def initTCPServer(ip: str, port: int):
    """
    Initialize a background TCP server thread to accept drone connections,
    spawning a processDrone thread for each new connection.
    """
    serverSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serverSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serverSocket.bind((ip, port))
    serverSocket.listen()
    logRecord(f"Central server listening on {ip}:{port}")

    def acceptLoop():
        while True:
            try:
                droneSocket, droneAddr = serverSocket.accept()
                droneThread = threading.Thread(
                    target=processDrone, args=(droneSocket, droneAddr), daemon=True)
                droneThread.start()
            except Exception as e:
                logRecord(f"Failed to accept connection: {e}")
                time.sleep(1)

    threading.Thread(target=acceptLoop, daemon=True).start()


def main():
    """
    Entry point: parse command-line arguments, start the TCP server,
    launch the GUI main loop.
    """
    parser = argparse.ArgumentParser(
        description="Central Server: Receives data from Drone and displays it")
    parser.add_argument("--ip", default="0.0.0.0", help="IP address to bind")
    parser.add_argument("--port", type=int, default=9100,
                        help="TCP port to listen on")
    args = parser.parse_args()

    initTCPServer(args.ip, args.port)

    gui = csGUI()
    gui.mainloop()


if __name__ == "__main__":
    main()