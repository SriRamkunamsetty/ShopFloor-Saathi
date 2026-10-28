"""Deterministic synthetic telemetry for the demo fleet.

Healthy behaviour is generated around AI4I-2020-like operating points. A few machines get an
injected fault in the most recent window so the whole pipeline can be demonstrated and tested
without any real plant data. Real data replaces this by implementing the same DataFrame schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from saathi.domain import MACHINES

COLUMNS = [
    "timestamp",
    "machine_id",
    "air_temp_k",
    "process_temp_k",
    "rpm",
    "torque_nm",
    "tool_wear_min",
]
STEP_MIN = 5


def generate_telemetry(seed: int = 7, hours: int = 48, end: datetime | None = None) -> pd.DataFrame:
    end = (end or datetime.now(UTC)).replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % STEP_MIN)
    n = hours * 60 // STEP_MIN
    times = [end - timedelta(minutes=STEP_MIN * (n - 1 - i)) for i in range(n)]
    minutes = np.arange(n) * STEP_MIN

    frames = []
    for idx, machine in enumerate(MACHINES):
        # One independent stream per machine so changing one machine's fault never shifts others.
        rng = np.random.default_rng([seed, idx])
        diurnal = 0.8 * np.sin(2 * np.pi * (minutes / (24 * 60)))
        air = 300.0 + diurnal + rng.normal(0, 0.25, n)
        process = air + 10.3 + rng.normal(0, 0.35, n)
        rpm = 1500 + rng.normal(0, 30, n)
        torque = 40 + rng.normal(0, 3, n)
        # Sawtooth: tool changed every 150 minutes, so healthy wear stays below 150.
        wear = ((minutes + idx * 37) % 150).astype(float)

        tail = 12  # last hour = 12 samples
        if machine.machine_id == "M-07":  # heat dissipation failure, ramping over the last hour
            ramp = np.clip(np.linspace(0, 1.6, tail), 0, 1)  # reaches full fault, then holds
            process[-tail:] = air[-tail:] + 10.3 - ramp * 3.5 + rng.normal(0, 0.1, tail)
            rpm[-tail:] = 1500 - ramp * 200 + rng.normal(0, 8, tail)
        elif machine.machine_id == "M-03":  # tool wear beyond the 200 minute limit
            wear[-tail:] = np.linspace(201, 212, tail)
        elif machine.machine_id == "M-05":  # torque spike -> power above the drive limit
            torque[-6:] = 75 + rng.normal(0, 1.0, 6)
            rpm[-6:] = 1500 + rng.normal(0, 5, 6)
        elif machine.machine_id == "M-02":  # overstrain: worn tool plus high torque, type L
            wear[-tail:] = np.linspace(192, 198, tail)
            torque[-tail:] = 59 + rng.normal(0, 0.5, tail)
            rpm[-tail:] = 1350 + rng.normal(0, 5, tail)  # keeps power just under the drive limit

        frames.append(
            pd.DataFrame(
                {
                    "timestamp": times,
                    "machine_id": machine.machine_id,
                    "air_temp_k": air.round(2),
                    "process_temp_k": process.round(2),
                    "rpm": rpm.round(1),
                    "torque_nm": torque.round(2),
                    "tool_wear_min": wear.round(1),
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    return df[COLUMNS].sort_values(["machine_id", "timestamp"]).reset_index(drop=True)
