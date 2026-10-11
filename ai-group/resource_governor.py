#!/usr/bin/env python3
"""Host-headroom governor for a dedicated Codex worker; never invokes a model."""
import json
import math
import os
from pathlib import Path
import subprocess
import time

UNIT = 'aicollabhub-worker.service'
CGROUP = Path('/sys/fs/cgroup/system.slice') / UNIT
MIB = 1024 * 1024
INTERVAL = 5


class Pressure:
    def __init__(self):
        self.active = False
        self.recovery = 0

    def update(self, headroom):
        if headroom is None:
            return self.active
        if headroom < 20:
            self.active, self.recovery = True, 0
        elif self.active and headroom >= 25:
            self.recovery += 1
            if self.recovery >= 3:
                self.active, self.recovery = False, 0
        else:
            self.recovery = 0
        return self.active


class Governor:
    def __init__(self, cpus):
        self.cpus = cpus
        self.cpu = Pressure()
        self.memory = Pressure()

    def decide(self, idle, available, total, usage):
        cpu_pressure = self.cpu.update(idle)
        memory_pressure = self.memory.update(100 * available / total)
        # Under pressure, this worker gets at most 20% of whole-host CPU.
        quota = str(self.cpus * 20) + '%' if cpu_pressure or memory_pressure else ''
        high = 'infinity'
        if memory_pressure:
            # Reclaim enough of the worker's memory to aim for 22% host headroom.
            budget = max(32 * MIB, min(total * .8, usage + available - total * .22))
            high = str(math.ceil(budget / (16 * MIB)) * 16 * MIB)
        return {'CPUQuota': quota, 'MemoryHigh': high}


def cpu_counters():
    values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    return sum(values), values[3]  # I/O wait and steal are not usable idle CPU.


def memory_counters():
    values = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()}
    return values['MemAvailable'], values['MemTotal']


def apply(properties, unit=UNIT):
    subprocess.run(['/usr/bin/systemctl', 'set-property', '--runtime', unit,
                    *(k + '=' + v for k, v in properties.items())],
                   check=True, capture_output=True, text=True, timeout=10)


def matches(properties):
    quota, period = (CGROUP / 'cpu.max').read_text().split()
    expected = properties['CPUQuota']
    cpu_ok = quota == 'max' if expected == '' else (
        quota != 'max' and abs(int(quota) / int(period) * 100 - float(expected[:-1])) < .1)
    high = (CGROUP / 'memory.high').read_text().strip()
    return cpu_ok and high == ('max' if properties['MemoryHigh'] == 'infinity' else properties['MemoryHigh'])


def main():
    state_dir = Path('/run/aicollabhub-resource-governor')
    state_dir.mkdir(exist_ok=True)
    governor = Governor(os.cpu_count() or 1)
    previous = cpu_counters()
    failures = 0
    while True:
        time.sleep(INTERVAL)
        try:
            current = cpu_counters()
            elapsed = current[0] - previous[0]
            idle = 100 * (current[1] - previous[1]) / elapsed if elapsed > 0 else None
            previous = current
            available, total = memory_counters()
            active = CGROUP.exists() and bool((CGROUP / 'cgroup.procs').read_text().strip())
            properties = None
            if active:
                usage = int((CGROUP / 'memory.current').read_text())
                properties = governor.decide(idle, available, total, usage)
                if not matches(properties):
                    apply(properties)
                    print(json.dumps({'cpu_idle_pct': round(idle, 1) if idle is not None else None,
                                      'memory_available_pct': round(100 * available / total, 1),
                                      'applied': properties}), flush=True)
            state = {'checked_at': time.time(), 'interval_seconds': INTERVAL,
                     'worker_active': active, 'cpu_idle_pct': idle,
                     'memory_available_pct': 100 * available / total,
                     'memory_available_mib': available / MIB,
                     'cpu_pressure': governor.cpu.active,
                     'memory_pressure': governor.memory.active,
                     'limits': properties}
            temp = state_dir / 'status.tmp'
            temp.write_text(json.dumps(state, indent=2) + '\n')
            temp.replace(state_dir / 'status.json')
            failures = 0
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failures += 1
            print('Resource governor error: ' + str(error), flush=True)
            if failures >= 3:
                raise


if __name__ == '__main__':
    main()
