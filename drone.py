#!/usr/bin/env python3
"""
Drone Gateway – final, no manual-drain button
--------------------------------------------
• Accepts many sensor nodes (TCP).
• Computes 10-sample averages, detects anomalies.
• Sends summaries to the central server while RUNNING.
• At ≤15 % battery → listener closes, summaries queued.
• At 100 % battery → listener re-opens, queue flushed.
• GUI shows sensor table, event log, battery bar.
"""

import argparse
import json
import queue
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from tkinter import Tk, ttk, Text, StringVar, END

# thresholds & constants
TEMP_MIN, TEMP_MAX = 18.0, 27.0
HUM_MIN,  HUM_MAX = 30.0, 60.0
BATTERY_STEP_SEC = 1          # drain 1 % every second
CHARGE_STEP_SEC = 0.2        # charge 1 % every 0.2 s
GUI_REFRESH_MS = 1000


def utc_now() -> str:
    """
    Return current UTC timestamp in ISO 8601 format.
    """
    return datetime.now(timezone.utc).isoformat()

# rolling stats per sensor


class Stats:
    """
    Maintain rolling statistics for each sensor: store last 10 temperature and humidity readings,
    compute averages, record last timestamp snippet, and count anomalies.
    """
    def __init__(self):
        # initialize deques for storing recent readings
        self.t, self.h = deque(maxlen=10), deque(maxlen=10)
        self.avg_t = self.avg_h = 0.0
        self.last = "—"
        self.anom = 0

    def add(self, tt, hh, ts):
        """
        Add a new temperature (tt) and humidity (hh) reading with timestamp ts,
        update rolling averages, record timestamp, detect and count anomalies.
        Returns True if the reading is out-of-bounds (anomaly).
        """
        self.t.append(tt)
        self.h.append(hh)
        self.avg_t = sum(self.t) / len(self.t)
        self.avg_h = sum(self.h) / len(self.h)
        self.last = ts[-8:]
        bad = not (TEMP_MIN <= tt <= TEMP_MAX and HUM_MIN <= hh <= HUM_MAX)
        if bad:
            self.anom += 1
        return bad

# GUI


class GUI:
    """
    GUI class using tkinter: displays sensor table, event log, and battery status bar.
    """
    def __init__(self, root: Tk, sensors, log_queue, listening_event):
        self.sensors, self.log_queue, self.listening_event = sensors, log_queue, listening_event
        self.batt = StringVar(root, "100 %")

        ttk.Label(root, text="Battery:").grid(
            row=0, column=0, sticky="w", padx=4)
        ttk.Label(root, textvariable=self.batt, width=14).grid(
            row=0, column=1, sticky="w")

        ttk.Style(root).configure("Bar.TProgressbar",
                                  troughcolor="black", background="green")
        self.pb = ttk.Progressbar(
            root, style="Bar.TProgressbar", length=200, maximum=100)
        self.pb.grid(row=0, column=2, padx=4)

        cols = ("Sensor", "Avg T (°C)", "Avg H (%)", "Last TS", "Anoms")
        self.tbl = ttk.Treeview(root, columns=cols, show="headings", height=9)
        for c in cols:
            self.tbl.heading(c, text=c)
        self.tbl.grid(row=1, column=0, columnspan=3, padx=4, pady=4)

        self.log = Text(root, height=9, state="disabled")
        self.log.grid(row=2, column=0, columnspan=3, padx=4, pady=4)

        root.after(GUI_REFRESH_MS, self.refresh)

    def refresh(self):
        """
        Periodically refresh the GUI: update sensor table, battery bar, and append new log entries.
        """
        # update table
        for sid, st in self.sensors.items():
            row = (sid, f"{st.avg_t:.2f}", f"{st.avg_h:.2f}", st.last, st.anom)
            if self.tbl.exists(sid):
                self.tbl.item(sid, values=row)
            else:
                self.tbl.insert("", END, iid=sid, values=row)

        # battery display
        battery_level = int(self.batt.get().split("%")[0])
        self.pb["value"] = battery_level
        colour = "green" if battery_level >= 50 else "yellow" if battery_level >= 25 else "red"
        ttk.Style().configure("Bar.TProgressbar", background=colour)
        mode = "RUNNING" if self.listening_event.is_set() else "CHARGING"
        self.batt.set(f"{battery_level}% ({mode})")

        # log window
        self.log.configure(state="normal")
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            ts = datetime.now().strftime("%H:%M:%S")
            self.log.insert(END, f"{ts}  {line}\n")
            self.log.see(END)
        self.log.configure(state="disabled")

        self.log.after(GUI_REFRESH_MS, self.refresh)

# listener thread


def listener(listen_ip, listen_port, sensors, log_queue, send_avg, listening_event, stop_event):
    """
    Listener thread: accepts TCP connections from sensor nodes, parses incoming JSON data,
    updates sensor stats, logs events, and triggers send_avg for completed samples.
    """
    def handle(conn):
        sid = None
        buf = b""
        with conn:
            while not stop_event.is_set() and listening_event.is_set():
                data = conn.recv(1024)
                if not data:
                    break
                buf += data
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    try:
                        j = json.loads(line)
                        sid = j["sensor_id"]
                        t = float(j["temperature"])
                        h = float(j["humidity"])
                        ts = j["timestamp"]
                    except Exception:
                        continue
                    st = sensors.setdefault(sid, Stats())
                    warn = st.add(t, h, ts)
                    log_queue.put(
                        f"{sid}{' ⚠' if warn else ''} {t:.1f}°C {h:.1f}%")
                    if len(st.t) == 10:
                        send_avg(sid, st.avg_t, st.avg_h, st.anom)
        if sid:
            log_queue.put(f"{sid} disconnected")

    sock = None
    while not stop_event.is_set():
        if listening_event.is_set() and sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((listen_ip, listen_port))
            sock.listen()
            sock.settimeout(1)
            log_queue.put(f"Listener on {listen_ip}:{listen_port}")
        if not listening_event.is_set() and sock:
            sock.close()
            sock = None
            log_queue.put("Listener paused")
        if sock:
            try:
                conn, _ = sock.accept()
                threading.Thread(target=handle, args=(
                    conn,), daemon=True).start()
            except socket.timeout:
                pass
        else:
            time.sleep(0.1)
    if sock:
        sock.close()

# sender / queue logic


def make_sender(server_ip, server_port, log_queue, charging_event, packet_queue):
    """
    Factory for sender function: sends averaged sensor data to central server,
    queues messages when charging or when server is offline, and retries connections.
    """
    sock = None
    lock = threading.Lock()

    def send(sensor_id, avg_temp, avg_hum, anomaly_count):
        packet = {"sensor_id": sensor_id, "avg_temp": round(avg_temp, 2), "avg_hum": round(avg_hum, 2),
                  "anomaly_count": anomaly_count, "timestamp": utc_now()}
        if charging_event.is_set():
            packet_queue.append(packet)
            return
        message = json.dumps(packet) + "\n"
        with lock:
            nonlocal sock
            if sock is None:
                try:
                    sock = socket.create_connection((server_ip, server_port), timeout=2)
                except Exception as e:
                    log_queue.put(f"Central offline: {e}")
                    packet_queue.append(packet)
                    return
            try:
                sock.sendall(message.encode())
            except Exception as e:
                log_queue.put(f"Central lost: {e}")
                sock.close()
                sock = None
                packet_queue.append(packet)
    return send


def flush_queue(server_ip, server_port, packet_queue, log_queue):
    """
    Attempt to send all queued packets to the central server, stopping on failure.
    """
    while packet_queue:
        packet = packet_queue.pop(0)
        try:
            with socket.create_connection((server_ip, server_port), timeout=2) as s:
                s.sendall((json.dumps(packet) + "\n").encode())
            log_queue.put("Queued summary sent")
        except Exception as e:
            log_queue.put(f"Still offline: {e}")
            packet_queue.insert(0, packet)
            break

# battery worker


def battery(gui, log_queue, listening_event, charging_event, packet_queue, server_ip, server_port):
    """
    Battery management thread: simulate battery drain/charge over time,
    pause listener and queue sends when low, resume and flush queue when full.
    """
    battery_level = 100
    is_discharging = True
    while True:
        time.sleep(BATTERY_STEP_SEC if is_discharging else CHARGE_STEP_SEC)
        battery_level += -1 if is_discharging else +1
        gui.batt.set(f"{battery_level}%")
        if is_discharging and battery_level <= 15:
            is_discharging = False
            listening_event.clear()
            charging_event.set()
            log_queue.put("Battery low – RETURNING")
        elif not is_discharging and battery_level >= 100:
            is_discharging = True
            listening_event.set()
            charging_event.clear()
            log_queue.put("Battery full – RUNNING")
            flush_queue(server_ip, server_port, packet_queue, log_queue)

# main


def main():
    """
    Main entry point: parse CLI arguments, initialize shared resources,
    start listener and battery threads, launch GUI main loop.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--serverip", required=True)
    ap.add_argument("--serverport", type=int, required=True)
    ap.add_argument("--listenip", default="0.0.0.0")
    ap.add_argument("--listenport", type=int, default=9000)
    cfg = ap.parse_args()

    sensors = {}
    log_queue = queue.Queue()
    listening_event = threading.Event()
    listening_event.set()
    stop_event = threading.Event()
    charging_event = threading.Event()
    packet_queue = []

    root = Tk()
    root.title("Drone GUI")
    gui = GUI(root, sensors, log_queue, listening_event)
    sender = make_sender(cfg.serverip, cfg.serverport,
                         log_queue, charging_event, packet_queue)

    threading.Thread(target=listener, args=(cfg.listenip, cfg.listenport, sensors, log_queue,
                                            sender, listening_event, stop_event), daemon=True).start()

    threading.Thread(target=battery, args=(gui, log_queue, listening_event, charging_event,
                                           packet_queue, cfg.serverip, cfg.serverport), daemon=True).start()

    root.mainloop()
    stop_event.set()


if __name__ == "__main__":
    main()