"""Central UDP receiver; publishes host telemetry for the SDN controller."""
import signal
import socket
import time
from common import append_event, atomic_json, config, decode_telemetry, run_directory


class Receiver:
    def __init__(self, hosts, directory):
        self.hosts, self.directory = hosts, directory
        self.latest = {}
        self.received = {ip: {"packets": 0, "bytes": 0} for ip in hosts.values()}
        self.retired_runs = {host: set() for host in hosts}
        self.invalid = 0

    def accept(self, data, source):
        ip = source[0]
        try:
            if ip not in self.received:
                raise ValueError("Unconfigured sender")
            if data.startswith(b"D"):
                if not 9 <= len(data) <= 1400:
                    raise ValueError("Invalid data datagram length")
                self.received[ip]["packets"] += 1
                self.received[ip]["bytes"] += len(data)
                return
            report = decode_telemetry(data, ip, self.hosts)
            host = report["host_id"]
            old = self.latest.get(host)
            if report["run_id"] in self.retired_runs[host]:
                return
            if old and old["run_id"] == report["run_id"]:
                if report["sequence"] <= old["sequence"]:
                    return
                if report["data_packets_sent"] < old["data_packets_sent"] or report["data_bytes_sent"] < old["data_bytes_sent"]:
                    raise ValueError("Counters decreased without a new run ID")
            elif old:
                self.retired_runs[host].add(old["run_id"])
            self.latest[host] = {
                **report, "source_ip": ip, "received_mono": time.monotonic(),
                "received_wall": time.time(),
            }
            append_event(self.directory / "telemetry.jsonl", self.latest[host])
        except (ValueError, UnicodeError, TypeError, OverflowError) as error:
            self.invalid += 1
            append_event(self.directory / "receiver_events.jsonl",
                         {"event": "INVALID_MESSAGE", "source": ip, "reason": str(error)})

    def publish(self):
        atomic_json(self.directory / "telemetry.json", {
            "updated_mono": time.monotonic(), "hosts": self.latest,
            "data_received": self.received, "invalid_messages": self.invalid,
        })


def main():
    cfg, directory = config(), run_directory()
    receiver = Receiver(cfg["hosts"], directory)
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_publish = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((cfg["server_ip"], cfg["server_port"]))
        server.settimeout(0.1)
        print(f"UDP server listening on {server.getsockname()}", flush=True)
        receiver.publish()
        while running:
            try:
                data, address = server.recvfrom(65535)
                receiver.accept(data, address)
            except socket.timeout:
                pass
            if time.monotonic() >= next_publish:
                receiver.publish()
                next_publish = time.monotonic() + 0.5
        receiver.publish()


if __name__ == "__main__":
    main()
