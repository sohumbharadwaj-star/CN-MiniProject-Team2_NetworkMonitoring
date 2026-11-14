"""Time-based alerts and polling decisions, independent of the experiment script."""


class LinkAlert:
    def __init__(self, high, low, hold, max_gap):
        self.high, self.low, self.hold, self.max_gap = high, low, hold, max_gap
        self.active = False
        self.since = self.last = None

    def update(self, at, percent):
        if percent is None:
            self.since = self.last = None
            return None
        if self.last is not None and at <= self.last:
            return None
        if self.last is not None and at - self.last > self.max_gap:
            self.since = None
        self.last = at
        qualifying = percent < self.low if self.active else percent > self.high
        if not qualifying:
            self.since = None
        elif self.since is None:
            self.since = at
        elif at - self.since >= self.hold:
            self.active = not self.active
            self.since = None
            return "HIGH_UTILIZATION" if self.active else "RECOVERED"
        return None


class Polling:
    def __init__(self, mode, settings):
        self.mode, self.settings = mode, settings
        self.fast = False
        self.quiet_since = None

    @property
    def interval(self):
        if self.mode == "fixed":
            return self.settings["fixed_interval"]
        return self.settings["fast_interval" if self.fast else "quiet_interval"]

    def update(self, at, percent, missing):
        if self.mode == "fixed":
            return None
        cfg = self.settings
        if missing or (percent is not None and percent >= cfg["fast_enter_percent"]):
            self.quiet_since = None
            if not self.fast:
                self.fast = True
                return "stale telemetry" if missing else "link utilization rose"
        elif self.fast:
            if percent is None or percent > cfg["fast_exit_percent"]:
                self.quiet_since = None
            elif self.quiet_since is None:
                self.quiet_since = at
            elif at - self.quiet_since >= cfg["quiet_after_seconds"]:
                self.fast = False
                self.quiet_since = None
                return "link quiet and telemetry healthy"
        return None


class AgentHealth:
    def __init__(self):
        self.states = {}

    def update(self, host, state):
        previous = self.states.get(host)
        self.states[host] = state
        if state == "STALE" and previous == "LIVE":
            return "AGENT_STALE"
        if state == "LIVE" and previous == "STALE":
            return "AGENT_RECOVERED"
        return None
