"""Run one D2 trial on the isolated Mininet topology."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime
from common import ROOT, atomic_json, config, read_json
from results import summarize


def display(status, phase):
    if not status:
        print(f"[{phase}] Waiting for controller status...", flush=True)
        return
    link = status.get("link") or {}
    percent = link.get("percent")
    utilization = "baseline/unavailable" if percent is None else f"{percent:.2f}%"
    print(f"\n[{phase}] state={status['state']} | toward-server utilization={utilization} | "
          f"{status['mode']} polling every {status['poll_seconds']:g}s", flush=True)
    print("Host  IP          Telemetry  Seq     SDN UDP Mbps  Received data bytes", flush=True)
    for row in status["hosts"]:
        rate = "unavailable" if row["flow_mbps"] is None else f"{row['flow_mbps']:.3f}"
        print(f"{row['host']:<5} {row['ip']:<11} {row['telemetry_state']:<10} "
              f"{str(row['sequence']):<7} {rate:<13} {row['received_data_bytes']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "adaptive"), default="adaptive")
    parser.add_argument("--output-parent", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise SystemExit("Run inside Ubuntu: sudo python3 run_demo.py")
    from mininet.link import TCLink
    from mininet.log import setLogLevel
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController

    settings = config()
    # Avoid taking over the network/processes from the earlier exercises.
    for bridge in ("s1", "s2"):
        if subprocess.run(["ovs-vsctl", "br-exists", bridge], capture_output=True).returncode == 0:
            raise SystemExit(f"Existing {bridge} detected. Exit the OLD Mininet session first; see README.")
    with socket.socket() as probe:
        if probe.connect_ex((settings["controller_ip"], settings["controller_port"])) == 0:
            raise SystemExit("Port 6653 is occupied. Stop the OLD controller first; see README.")
    directory = args.output_parent.resolve() / datetime.now().strftime("run-%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    print(f"Evidence: {directory}", flush=True)
    os.environ["CN_RUN_DIR"] = str(directory)
    os.environ["CN_POLL_MODE"] = args.mode
    atomic_json(directory / "configuration.json", settings)
    atomic_json(directory / "scenario.json", {"name": "WARMUP", "mbps_per_agent": 0.1})
    environment = {"python": sys.version, "os_release": Path("/etc/os-release").read_text()}
    for name, command in {"mininet": ["mn", "--version"], "ovs": ["ovs-vsctl", "--version"]}.items():
        process = subprocess.run(command, capture_output=True, text=True)
        environment[name] = process.stdout + process.stderr
    from importlib.metadata import version
    environment["os_ken"] = version("os-ken")
    atomic_json(directory / "environment.json", environment)
    handles, children = [], []
    net = None
    connectivity = None
    interrupted = False
    timeline = []
    target = "h2"

    def launch(name, script, host=None, extra=()):
        log = (directory / f"{name}.log").open("w")
        handles.append(log)
        command = [sys.executable, "-u", str(ROOT / script), *extra]
        options = dict(cwd=str(ROOT), env=os.environ.copy(), stdout=log,
                       stderr=subprocess.STDOUT, start_new_session=True)
        # Python already creates the new session. Mininet's default mnexec -d
        # would fork a second time and return the exited wrapper's PID.
        process = (host.popen(command, mncmd=["mnexec", "-a", str(host.pid)], **options)
                   if host else subprocess.Popen(command, **options))
        children.append((name, process))
        return process

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def check_children():
        for name, process in children:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited early (code {process.returncode}); read {directory / (name + '.log')}")

    try:
        controller = launch("controller", "controller_app.py")
        deadline = time.monotonic() + 15
        while True:
            check_children()
            with socket.socket() as probe:
                if probe.connect_ex((settings["controller_ip"], settings["controller_port"])) == 0:
                    break
            if time.monotonic() > deadline:
                raise RuntimeError("Controller did not start; inspect controller.log")
            time.sleep(0.2)
        setLogLevel("info")
        net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                      autoSetMacs=True, build=False)
        net.addController("c0", controller=RemoteController,
                          ip=settings["controller_ip"], port=settings["controller_port"])
        s1 = net.addSwitch("s1", protocols="OpenFlow13", failMode="secure", dpid="0000000000000001")
        s2 = net.addSwitch("s2", protocols="OpenFlow13", failMode="secure", dpid="0000000000000002")
        agents = {}
        for index, (name, ip) in enumerate(settings["hosts"].items(), 1):
            agents[name] = net.addHost(name, ip=ip + "/24")
            net.addLink(agents[name], s1, port2=index)
        server_host = net.addHost("h4", ip=settings["server_ip"] + "/24")
        net.addLink(s1, s2, port1=4, port2=1, bw=settings["link_capacity_mbps"])
        net.addLink(server_host, s2, port2=2)
        net.build()
        net.start()
        if not net.waitConnected(timeout=15):
            raise RuntimeError("A switch failed to connect")
        connectivity = net.pingAll()
        if connectivity:
            raise RuntimeError("Initial ping connectivity failed")
        atomic_json(directory / "topology.json", {
            "hosts": {**settings["hosts"], "h4": settings["server_ip"]},
            "bottleneck": "s1-eth4 <-> s2-eth1", "link_capacity_mbps": settings["link_capacity_mbps"],
            "measurement": "s2-eth1 RX toward server, after the inter-switch shaped link"})
        server = launch("server", "server.py", server_host)
        deadline = time.monotonic() + 10
        while not (directory / "telemetry.json").exists():
            check_children()
            if time.monotonic() > deadline:
                raise RuntimeError("Server did not publish its startup snapshot")
            time.sleep(0.1)
        agent_processes = {name: launch(name, "agent.py", host, ["--host-id", name])
                           for name, host in agents.items()}
        print(f"\nRunning D2 ({args.mode}) including a deliberate {target} agent interruption.", flush=True)
        injected = False
        for phase in settings["phases"]:
            atomic_json(directory / "scenario.json", phase)
            record = {**phase, "start": time.time(), "start_mono": time.monotonic()}
            if phase.get("action") == "stop":
                process = agent_processes.pop(target)
                stop(process)
                children.remove((target, process))
                print(f"Stopped only the {target} sender; host and switch links remain up.", flush=True)
            elif phase.get("action") == "restart":
                agent_processes[target] = launch(target + "-restart", "agent.py", agents[target], ["--host-id", target])
                print(f"Restarted the {target} sender.", flush=True)
            timeline.append(record)
            atomic_json(directory / "phase_times.json", timeline)
            print(f"\n=== {phase['name']}: requested payload rate {phase['mbps_per_agent']} Mbps per agent ===", flush=True)
            deadline = record["start_mono"] + phase["seconds"]
            while time.monotonic() < deadline:
                check_children()
                display(read_json(directory / "status.json"), phase["name"])
                if not injected and (directory / "status.json").exists():
                    code = ("import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                            f"s.sendto(b'invalid-message',({settings['server_ip']!r},{settings['server_port']})); s.close()")
                    test = agents["h2"].popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try:
                        if test.wait(timeout=3):
                            raise RuntimeError("Malformed-message probe failed to send")
                    finally:
                        stop(test)
                    injected = True
                time.sleep(max(0, min(2, deadline - time.monotonic())))
            record["end"] = time.time()
            record["end_mono"] = time.monotonic()
            atomic_json(directory / "phase_times.json", timeline)
        # Freeze the logs before reading them; no partially written JSONL rows.
        check_children()
        stop(controller)
        stop(server)
        result = summarize(directory, connectivity, args.mode, target)
        print("\nD2 DEMO CHECKS", flush=True)
        for name, passed in result["checks"].items():
            print(f"  {'PASS' if passed else 'NOT OBSERVED'}: {name}", flush=True)
        print(f"Evidence: {directory}", flush=True)
        return 0 if result["all_checks_passed"] else 2
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted; stopping only this demo's processes...", flush=True)
        return 130
    except Exception as error:
        print(f"\nDEMO ERROR: {error}\nLogs: {directory}", file=sys.stderr, flush=True)
        return 1
    finally:
        for name, process in reversed(children):
            stop(process)
        if net is not None:
            net.stop()
        for handle in handles:
            handle.close()
        if interrupted:
            atomic_json(directory / "interrupted.json", {"interrupted": True, "wall_time": time.time()})


if __name__ == "__main__":
    raise SystemExit(main())
