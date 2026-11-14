"""Actual paced UDP traffic plus one telemetry report per second."""
import argparse
import json
import signal
import socket
import struct
import time
import uuid
from common import config, read_json, run_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-id", required=True)
    args = parser.parse_args()
    cfg, directory = config(), run_directory()
    if args.host_id not in cfg["hosts"]:
        parser.error("Unknown host ID")
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    packets = byte_count = reports = sequence = 0
    run_id = str(uuid.uuid4())
    payload_size = cfg["datagram_bytes"]
    credit = 0.0
    last = next_report = time.monotonic()
    target = 0.0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(0.2)
        destination = (cfg["server_ip"], cfg["server_port"])
        print(f"Agent {args.host_id} -> {destination}; run={run_id}", flush=True)
        while running:
            now = time.monotonic()
            if now >= next_report:
                scenario = read_json(directory / "scenario.json", {})
                target = float(scenario.get("mbps_per_agent", 0))
                sequence += 1
                report = {
                    "version": 1, "type": "telemetry", "host_id": args.host_id,
                    "run_id": run_id, "sequence": sequence, "sent_at": time.time(),
                    "data_packets_sent": packets, "data_bytes_sent": byte_count,
                    "telemetry_reports_sent": reports, "target_mbps": target,
                }
                try:
                    client.sendto(b"T" + json.dumps(report).encode(), destination)
                    reports += 1
                except OSError as error:
                    print(f"Telemetry send error: {error}", flush=True)
                next_report = now + cfg["report_interval"]
            # Bound catch-up bursts after a scheduler pause.
            rate = max(0, target) * 1e6 / 8
            credit = min(credit + rate * (now - last), max(payload_size, rate * 0.2))
            last = now
            while credit >= payload_size and running:
                data = b"D" + struct.pack("!Q", packets) + bytes(payload_size - 9)
                try:
                    sent = client.sendto(data, destination)
                except OSError as error:
                    print(f"Data send error: {error}", flush=True)
                    break
                packets += 1
                byte_count += sent
                credit -= payload_size
            time.sleep(0.02)
    print(f"Stopped: actual data datagrams sent={packets}, payload bytes={byte_count}", flush=True)


if __name__ == "__main__":
    main()
