"""Shared protocol, measurement calculations, and atomic process snapshots."""
import json
import math
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent


def config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def run_directory():
    value = os.environ.get("CN_RUN_DIR")
    if not value:
        raise RuntimeError("Start with: sudo python3 run_demo.py")
    return Path(value)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False)
    os.replace(temporary, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def append_event(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"wall_time": time.time(), **value}, allow_nan=False) + "\n")


def decode_telemetry(data, source_ip, hosts):
    if not data.startswith(b"T") or len(data) > 4096:
        raise ValueError("Expected a small T-prefixed telemetry message")
    value = json.loads(data[1:].decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Telemetry must be an object")
    if value.get("version") != 1 or value.get("type") != "telemetry":
        raise ValueError("Unsupported protocol")
    host = value.get("host_id")
    if not isinstance(host, str) or hosts.get(host) != source_ip:
        raise ValueError("Host identity does not match configured source address")
    if not isinstance(value.get("run_id"), str) or not 1 <= len(value["run_id"]) <= 64:
        raise ValueError("Invalid run ID")
    for key in ("sequence", "data_packets_sent", "data_bytes_sent", "telemetry_reports_sent"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError(f"Invalid nonnegative integer: {key}")
    stamp = value.get("sent_at")
    if type(stamp) not in (float, int) or not math.isfinite(stamp):
        raise ValueError("Invalid timestamp")
    return value


class CounterRate:
    """Use the switch's counter age, not an assumed polling interval."""
    def __init__(self):
        self.previous = None

    def sample(self, byte_count, counter_age):
        old = self.previous
        self.previous = (byte_count, counter_age)
        if old is None or byte_count < old[0] or counter_age <= old[1]:
            return None
        return 8 * (byte_count - old[0]) / (counter_age - old[1])


