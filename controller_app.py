"""OpenFlow 1.3 forwarding, actual statistics, telemetry correlation, and alerts."""
import logging
import os
import signal
import sys
import threading
import time

from os_ken import cfg
from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from os_ken.lib import hub
from os_ken.lib.packet import packet, ethernet, ether_types, ipv4, udp
from os_ken.ofproto import ofproto_v1_3
from common import CounterRate, append_event, atomic_json, config, read_json, run_directory
from policy import AgentHealth, LinkAlert, Polling


class D2Controller(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings, self.directory = config(), run_directory()
        self.lock = threading.RLock()
        self.switches, self.learned, self.requests = {}, {}, {}
        self.flow_rates, self.flows = {}, {}
        self.link_rate = CounterRate()
        self.link = None
        self.last_evaluated_link = None
        self.alert = LinkAlert(self.settings["alert_above_percent"],
                               self.settings["clear_below_percent"],
                               self.settings["alert_hold_seconds"],
                               self.settings["stats_stale_after"])
        self.health = AgentHealth()
        self.polling = Polling(os.environ["CN_POLL_MODE"], self.settings)
        self.last_poll = None
        self.threads.append(hub.spawn(self.monitor))

    def install_rule(self, switch, priority, match, actions):
        p, ofp = switch.ofproto_parser, switch.ofproto
        switch.send_msg(p.OFPFlowMod(
            datapath=switch, priority=priority, match=match,
            idle_timeout=60 if priority else 0,
            instructions=[p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def connected(self, event):
        switch = event.msg.datapath
        p, ofp = switch.ofproto_parser, switch.ofproto
        # This controller owns this isolated demo topology. Clear prior-run rules.
        switch.send_msg(p.OFPFlowMod(datapath=switch, command=ofp.OFPFC_DELETE,
                        out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                        match=p.OFPMatch(), table_id=ofp.OFPTT_ALL))
        self.learned[switch.id] = {}
        self.install_rule(switch, 0, p.OFPMatch(), [
            p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        self.logger.info("Switch %s connected using OpenFlow 1.3", switch.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change(self, event):
        switch = event.datapath
        with self.lock:
            if event.state == MAIN_DISPATCHER:
                self.switches[switch.id] = switch
            elif self.switches.get(switch.id) is switch:
                self.switches.pop(switch.id, None)
            # On either transition, new measurements must establish a baseline.
            self.flows.pop(switch.id, None)
            self.flow_rates = {key: value for key, value in self.flow_rates.items()
                               if key[0] != switch.id}
            self.requests = {key: value for key, value in self.requests.items()
                             if key[0] != switch.id}
            if switch.id == 2:
                self.link_rate = CounterRate()
                self.link = None

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def forward(self, event):
        message = event.msg
        switch, incoming = message.datapath, message.match["in_port"]
        p, ofp = switch.ofproto_parser, switch.ofproto
        frame = packet.Packet(message.data)
        eth = frame.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        learned = self.learned.setdefault(switch.id, {})
        learned[eth.src] = incoming
        outgoing = learned.get(eth.dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(outgoing)]
        if outgoing != ofp.OFPP_FLOOD:
            fields = dict(in_port=incoming, eth_src=eth.src, eth_dst=eth.dst,
                          eth_type=eth.ethertype)
            ip_header = frame.get_protocol(ipv4.ipv4)
            udp_header = frame.get_protocol(udp.udp)
            priority = 10
            if ip_header:
                fields.update(ipv4_src=ip_header.src, ipv4_dst=ip_header.dst,
                              ip_proto=ip_header.proto)
            if ip_header and udp_header:
                fields["udp_dst"] = udp_header.dst_port
                priority = 20
            self.install_rule(switch, priority, p.OFPMatch(**fields), actions)
        switch.send_msg(p.OFPPacketOut(
            datapath=switch, buffer_id=message.buffer_id, in_port=incoming,
            actions=actions, data=message.data if message.buffer_id == ofp.OFP_NO_BUFFER else None))

    def request(self, switch, kind, now):
        # Keep at most one outstanding request of each kind per switch.
        if any(key[0] == switch.id and value["kind"] == kind
               for key, value in self.requests.items()):
            return
        p = switch.ofproto_parser
        message = (p.OFPFlowStatsRequest(switch) if kind == "flows" else
                   p.OFPPortStatsRequest(switch, flags=0, port_no=1))
        switch.set_xid(message)
        self.requests[(switch.id, message.xid)] = {"kind": kind, "at": now, "body": []}
        switch.send_msg(message)
        append_event(self.directory / "polling.jsonl", {
            "event": "REQUEST", "mono": now, "kind": kind, "switch": switch.id,
            "xid": message.xid})

    def monitor(self):
        while self.is_active:
            try:
                now = time.monotonic()
                with self.lock:
                    self.requests = {key: value for key, value in self.requests.items()
                                     if now - value["at"] < self.settings["stats_stale_after"]}
                    self.publish(now)
                    if self.switches and (self.last_poll is None or
                                          now - self.last_poll >= self.polling.interval - 0.01):
                        self.last_poll = now
                        for switch in list(self.switches.values()):
                            self.request(switch, "flows", now)
                            if switch.id == 2:
                                self.request(switch, "ports", now)
            except Exception:
                self.logger.exception("Monitoring cycle failed")
            hub.sleep(self.settings["status_interval"])

    def complete_reply(self, event, kind):
        message = event.msg
        key = (message.datapath.id, message.xid)
        pending = self.requests.get(key)
        if not pending or pending["kind"] != kind:
            return None
        pending["body"].extend(message.body)
        if message.flags & message.datapath.ofproto.OFPMPF_REPLY_MORE:
            return None
        self.requests.pop(key, None)
        append_event(self.directory / "polling.jsonl", {
            "event": "REPLY_COMPLETE", "mono": time.monotonic(), "kind": kind,
            "switch": message.datapath.id, "xid": message.xid})
        return pending["body"]

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_reply(self, event):
        with self.lock:
            body = self.complete_reply(event, "flows")
            if body is None:
                return
            switch_id = event.msg.datapath.id
            observations, active_keys = [], set()
            for flow in body:
                match = dict(flow.match.items())
                if flow.priority != 20 or match.get("udp_dst") != self.settings["server_port"]:
                    continue
                if match.get("ipv4_dst") != self.settings["server_ip"]:
                    continue
                key = (switch_id, flow.table_id, flow.cookie, tuple(sorted(match.items())))
                active_keys.add(key)
                counter = self.flow_rates.setdefault(key, CounterRate())
                age = flow.duration_sec + flow.duration_nsec / 1e9
                rate = counter.sample(flow.byte_count, age)
                observations.append({"match": match, "packets": flow.packet_count,
                                     "bytes": flow.byte_count, "bps": rate})
            self.flow_rates = {key: value for key, value in self.flow_rates.items()
                               if key[0] != switch_id or key in active_keys}
            sample = {"at": time.monotonic(), "entries": observations}
            self.flows[switch_id] = sample
            append_event(self.directory / "flow_stats.jsonl", {"switch": switch_id, **sample})

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_reply(self, event):
        with self.lock:
            body = self.complete_reply(event, "ports")
            if body is None or event.msg.datapath.id != 2:
                return
            for port in body:
                if port.port_no != 1:
                    continue
                age = port.duration_sec + port.duration_nsec / 1e9
                bps = self.link_rate.sample(port.rx_bytes, age)
                self.link = {"at": time.monotonic(), "interface": "s2-eth1",
                             "direction": "toward_server", "bytes": port.rx_bytes,
                             "packets": port.rx_packets, "counter_age": age, "bps": bps,
                             "percent": None if bps is None else
                             100 * bps / (self.settings["link_capacity_mbps"] * 1e6)}
                append_event(self.directory / "port_stats.jsonl", self.link)

    def publish(self, now):
        telemetry = read_json(self.directory / "telemetry.json", {})
        raw_hosts = telemetry.get("hosts", {})
        flow_sample = self.flows.get(2, {})
        flows_fresh = bool(flow_sample) and now - flow_sample["at"] <= self.settings["stats_stale_after"]
        rows = []
        for name, ip in self.settings["hosts"].items():
            host = raw_hosts.get(name)
            host_age = None if not host else max(0, now - host["received_mono"])
            fresh = host_age is not None and host_age <= self.settings["stale_after"]
            entries = [entry for entry in flow_sample.get("entries", [])
                       if entry["match"].get("ipv4_src") == ip] if flows_fresh else []
            rates = [entry["bps"] for entry in entries]
            flow_bps = sum(rates) if rates and all(rate is not None for rate in rates) else None
            rows.append({"host": name, "ip": ip, "telemetry_state": "LIVE" if fresh else "STALE" if host else "WAITING",
                         "age_seconds": host_age, "sequence": host["sequence"] if host else None,
                         "sent_data_bytes": host["data_bytes_sent"] if host else None,
                         "received_data_bytes": telemetry.get("data_received", {}).get(ip, {}).get("bytes", 0),
                         "flow_mbps": None if flow_bps is None else flow_bps / 1e6,
                         "flow_packets": sum(entry["packets"] for entry in entries) if entries else None})
        link_fresh = self.link is not None and now - self.link["at"] <= self.settings["stats_stale_after"]
        ready = (set(self.switches) >= {1, 2} and link_fresh and self.link["bps"] is not None
                 and flows_fresh and all(row["telemetry_state"] == "LIVE" and row["flow_mbps"] is not None for row in rows))
        for row in rows:
            transition = self.health.update(row["host"], row["telemetry_state"])
            if transition:
                self.record_alert(transition, now, host=row["host"], hosts=rows)
        percent = self.link["percent"] if link_fresh else None
        previous_interval = self.polling.interval
        reason = self.polling.update(now, percent, any(row["telemetry_state"] == "STALE" for row in rows))
        if reason:
            append_event(self.directory / "polling.jsonl", {
                "event": "INTERVAL_CHANGED", "mono": now, "from_seconds": previous_interval,
                "to_seconds": self.polling.interval, "reason": reason, "percent": percent})
            self.logger.info("Polling %.2fs -> %.2fs: %s", previous_interval, self.polling.interval, reason)
        if not ready:
            self.alert.update(now, None)
        if link_fresh and self.link["at"] != self.last_evaluated_link:
            self.last_evaluated_link = self.link["at"]
            transition = self.alert.update(self.link["at"], percent) if ready else None
            if transition:
                self.record_alert(transition, now, percent=percent, hosts=rows)
        status = {"wall_time": time.time(), "updated_mono": now, "ready": ready,
                  "state": "INCOMPLETE" if not ready else "HIGH" if self.alert.active else "NORMAL",
                  "alert_active": self.alert.active,
                  "connected_switches": sorted(self.switches), "hosts": rows,
                  "link": self.link if link_fresh else None,
                  "invalid_messages": telemetry.get("invalid_messages", 0),
                  "mode": self.polling.mode, "poll_seconds": self.polling.interval}
        atomic_json(self.directory / "status.json", status)
        append_event(self.directory / "measurements.jsonl", status)

    def record_alert(self, event, now, **fields):
        append_event(self.directory / "alerts.jsonl", {"event": event, "mono": now, **fields})
        self.logger.info("%s %s", event, fields.get("host", fields.get("percent", "")))


def main():
    settings = config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def stop(signum, _frame):
        # OS-Ken 4.1.1 native HubThread cleanup calls a nonexistent .kill().
        # This isolated controller owns no child processes. Exit this process;
        # the orchestrator handles its agents/server and Mininet separately.
        print(f"Controller stopping (signal {signum})", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    cfg.CONF([], project="team2-d2")
    cfg.CONF.set_override("ofp_listen_host", settings["controller_ip"])
    cfg.CONF.set_override("ofp_tcp_listen_port", settings["controller_port"])
    app_manager.AppManager.run_apps(["controller_app"])


if __name__ == "__main__":
    main()
