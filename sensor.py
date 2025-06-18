#!/usr/bin/env python3

import socket
import json
import time
import random
import sys
import argparse
import threading
from datetime import datetime


def generate_sensor_data(sensor_id):
    """Rastgele sıcaklık ve nem verisi üretir."""
    return {
        "sensor_id": sensor_id,
        "temperature": round(random.uniform(15.0, 30.0), 2),
        "humidity":    round(random.uniform(30.0, 70.0), 2),
        "timestamp":   datetime.utcnow().isoformat() + "Z"
    }


def sensor_loop(sensor_id, drone_ip, drone_port, interval):
    """Belirtilen aralıkla Drone’a bağlanıp veri yollayan döngü."""
    while True:
        try:
            s = socket.create_connection((drone_ip, drone_port), timeout=5)
            print(f"[{sensor_id}] Connected to Drone at {drone_ip}:{drone_port}")
            while True:
                data = generate_sensor_data(sensor_id)
                s.sendall((json.dumps(data) + "\n").encode())
                print(f"[{sensor_id} SENT] {data}")
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, socket.timeout) as e:
            print(f"[{sensor_id}] Connection lost ({e}), retrying in {interval}s…")
            time.sleep(interval)
        except Exception as e:
            print(f"[{sensor_id}] Unexpected error: {e}, retrying in {interval}s…")
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="TCP Sensor Node (multi-sensor)")
    parser.add_argument("--drone_ip",   type=str,   default="127.0.0.1",
                        help="Drone’ın IP adresi")
    parser.add_argument("--drone_port", type=int,   default=9000,
                        help="Drone’ın TCP portu")
    parser.add_argument("--interval",   type=float, default=2.0,
                        help="Okuma aralığı (saniye)")
    parser.add_argument("--sensor_id",  type=str,   required=True,
                        help="Sensör taban ID’si (ör. 'sensorX')")
    parser.add_argument("--count",      type=int,   default=1,
                        help="Kaç adet sensör başlatılacağı")
    args = parser.parse_args()

    threads = []
    for i in range(1, args.count + 1):
        sid = f"{args.sensor_id}{i}"
        t = threading.Thread(
            target=sensor_loop,
            args=(sid, args.drone_ip, args.drone_port, args.interval),
            daemon=True
        )
        t.start()
        threads.append(t)
        print(f"[INFO] Started sensor thread: {sid}")

    # Ana iş parçacığını canlı tut
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[EXIT] Shutting down all sensors.")
        sys.exit(0)


if __name__ == "__main__":
    main()