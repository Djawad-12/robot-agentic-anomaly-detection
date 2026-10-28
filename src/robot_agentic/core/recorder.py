"""Recorder: keeps the three live streams in memory and cuts aligned recordings out of them.

The `Recorder` reads the signals, the log and the fault codes continuously into ring
buffers. A recording is a plain slice of those buffers between two instants: a test can be
recorded before, during and after it without missing its start.

Signals are stored in one preallocated numpy array (rows = instants, columns = channels)
rather than one Python object per sample: 120 s at 500 Hz on 63 channels takes about 60 MB
instead of more than 200 MB, which matters on a Raspberry Pi.
"""

from __future__ import annotations

import gzip
import json
import logging
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .events import FaultCode, Level, LogLine, Sample
from .interfaces import Connector

log = logging.getLogger(__name__)


@dataclass
class Recording:
    """Signals, log lines and fault codes between two instants.

    `t` holds the instant of each sample; every array in `channels` has the same length as
    `t`. A channel missing from a sample is NaN.
    """

    id: str
    label: str
    machine: str
    t0: float
    t1: float
    t: np.ndarray
    channels: dict[str, np.ndarray]
    logs: list[LogLine] = field(default_factory=list)
    faults: list[FaultCode] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """Length of the recorded window, in seconds."""
        return self.t1 - self.t0

    @property
    def rate_hz(self) -> float:
        """Mean sample rate, in Hz (0 if there are fewer than two samples)."""
        return (len(self.t) - 1) / (self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0

    def resample(self, hz: float, channels: list[str] | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Bring the recording down to `hz` points per second by averaging over windows.

        Averaging (rather than skipping samples) keeps the energy of short peaks. If `hz`
        is above the original rate, the data is returned as is.

        Args:
            hz: wanted rate.
            channels: channels to keep; all of them if None or empty.

        Returns:
            The instants, relative to the start of the window (s), and the resampled channels.

        Raises:
            KeyError: if a requested channel does not exist in the recording.
        """
        if channels:
            missing = [c for c in channels if c not in self.channels]
            if missing:
                raise KeyError(f"unknown channels: {missing}")
            data = {c: self.channels[c] for c in channels}
        else:
            data = self.channels
        if len(self.t) == 0 or hz >= self.rate_hz:
            return self.t - self.t0, data
        step = max(1, round(self.rate_hz / hz))
        n = len(self.t) // step * step
        t = self.t[:n].reshape(-1, step).mean(axis=1) - self.t0
        return t, {k: v[:n].reshape(-1, step).mean(axis=1) for k, v in data.items()}

    def save(self, directory: Path) -> Path:
        """Write the recording to `directory/<id>.json.gz` and return the path."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json.gz"
        payload = {
            "id": self.id,
            "label": self.label,
            "machine": self.machine,
            "t0": self.t0,
            "t1": self.t1,
            "t": self.t.tolist(),
            "channels": {k: v.tolist() for k, v in self.channels.items()},
            "logs": [asdict(x) for x in self.logs],
            "faults": [asdict(x) for x in self.faults],
        }
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
        log.debug("recording %s saved (%d points, %d channels)", self.id, len(self.t), len(self.channels))
        return path

    @classmethod
    def load(cls, path: Path) -> Recording:
        """Read back a recording written by `save`.

        Raises:
            FileNotFoundError: if the file does not exist.
        """
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            p = json.load(fh)
        return cls(
            id=p["id"],
            label=p["label"],
            machine=p["machine"],
            t0=p["t0"],
            t1=p["t1"],
            t=np.asarray(p["t"], dtype=float),
            channels={k: np.asarray(v, dtype=float) for k, v in p["channels"].items()},
            logs=[LogLine(**{**x, "level": Level(x["level"])}) for x in p["logs"]],
            faults=[FaultCode(**{**x, "level": Level(x["level"])}) for x in p["faults"]],
        )


class Recorder:
    """Reads the connector's streams continuously and cuts recordings on demand.

    Three threads (signals, log, fault codes) fill ring buffers. A stream that stops
    (machine off, connection lost) is restarted every second; the cause is kept in `errors`
    and logged, because losing data is itself a clue for the diagnosis.

    Args:
        connector: the machine whose streams are read.
        directory: folder where `record` saves recordings.
        history_s: depth of the buffers, in seconds.
        max_rate_hz: highest expected signal rate, to size the signal buffer.
    """

    def __init__(self, connector: Connector, directory: Path, history_s: float = 120.0, max_rate_hz: float = 500.0) -> None:
        """Allocate the buffers; reading only starts with `start`."""
        self.connector = connector
        self.directory = directory
        self._capacity = int(history_s * max_rate_hz)
        self._t = np.full(self._capacity, np.nan)
        self._data = np.full((self._capacity, 0), np.nan)
        self._columns: dict[str, int] = {}
        self._count = 0
        self._logs: deque[LogLine] = deque(maxlen=5000)
        self._faults: deque[FaultCode] = deque(maxlen=1000)
        self._subscribers: list[queue.Queue[LogLine | FaultCode]] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self.errors: dict[str, str] = {}

    def start(self) -> Recorder:
        """Start reading the three streams in the background (no effect if already running)."""
        if self._threads:
            return self
        streams: list[tuple[str, Callable[[], Iterator[Any]], Callable[[Any], None]]] = [
            ("signals", self.connector.signals, self._add_sample),
            ("logs", self.connector.logs, lambda x: self._add_event(self._logs, x)),
            ("faults", self.connector.faults, lambda x: self._add_event(self._faults, x)),
        ]
        for name, stream, sink in streams:
            th = threading.Thread(target=self._pump, args=(name, stream, sink), name=f"recorder-{name}", daemon=True)
            th.start()
            self._threads.append(th)
        log.info("started reading the streams of %s", self.connector.name)
        return self

    def _pump(self, name: str, stream: Callable[[], Iterator[Any]], sink: Callable[[Any], None]) -> None:
        """Loop of one reader thread: feeds `sink` with every item, and never stops."""
        while True:
            try:
                for item in stream():
                    sink(item)
                    if name in self.errors:
                        log.info("stream %s restored", name)
                        del self.errors[name]
                self.errors[name] = "stream ended"
            except Exception as exc:  # noqa: BLE001 - any network or process error must be survived
                self.errors[name] = f"{type(exc).__name__}: {exc}"
            log.warning("stream %s interrupted (%s), retrying in 1 s", name, self.errors[name])
            time.sleep(1.0)

    def _add_sample(self, sample: Sample) -> None:
        """Write one sample into the next row of the signal buffer, adding columns for new channels."""
        with self._lock:
            new = [k for k in sample.values if k not in self._columns]
            if new:
                for i, k in enumerate(new):
                    self._columns[k] = self._data.shape[1] + i
                self._data = np.hstack([self._data, np.full((self._capacity, len(new)), np.nan)])
            row = self._count % self._capacity
            self._data[row] = np.nan
            for k, v in sample.values.items():
                self._data[row, self._columns[k]] = v
            self._t[row] = sample.t
            self._count += 1

    def _add_event(self, sink: deque[Any], item: LogLine | FaultCode) -> None:
        """Keep a log line or fault code, and push it to the subscribed queues (`subscribe`)."""
        with self._lock:
            sink.append(item)
        for q in self._subscribers:
            q.put(item)

    def subscribe(self) -> queue.Queue[LogLine | FaultCode]:
        """Queue that receives every new log line and every new fault code."""
        q: queue.Queue[LogLine | FaultCode] = queue.Queue()
        self._subscribers.append(q)
        return q

    def last_sample_age(self) -> float | None:
        """Age of the last sample received, in seconds (None if none has arrived yet)."""
        with self._lock:
            return time.time() - self._t[(self._count - 1) % self._capacity] if self._count else None

    def window(self, t0: float, t1: float, label: str = "") -> Recording:
        """Cut the buffers between `t0` and `t1` into a recording (not saved).

        Channels with no value in the window are left out.
        """
        with self._lock:
            n = min(self._count, self._capacity)
            rows = np.arange(self._count - n, self._count) % self._capacity
            rows = rows[(self._t[rows] >= t0) & (self._t[rows] <= t1)]
            t = self._t[rows]
            data = self._data[rows]
            columns = dict(self._columns)
            logs = [x for x in self._logs if t0 <= x.t <= t1]
            faults = [x for x in self._faults if t0 <= x.t <= t1]
        channels = {k: data[:, i] for k, i in sorted(columns.items()) if np.isfinite(data[:, i]).any()}
        return Recording(
            id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
            label=label,
            machine=self.connector.name,
            t0=t0,
            t1=t1,
            t=t,
            channels=channels,
            logs=logs,
            faults=faults,
        )

    def latest(self, seconds: float, label: str = "latest") -> Recording:
        """Recording of the last `seconds` seconds (not saved)."""
        t1 = time.time()
        return self.window(t1 - seconds, t1, label)

    def record(self, action: Callable[[], Any], label: str, before: float = 1.0, after: float = 1.5) -> tuple[Recording, Any]:
        """Record every stream around an action, then save the recording.

        Args:
            action: function without arguments that starts the test and waits for its end.
            label: readable description of the test.
            before: seconds kept before the action, to see the starting state.
            after: seconds waited after the action, to see the return to rest.

        Returns:
            The recording and the value returned by `action`.
        """
        t0 = time.time() - before
        log.info("recording: %s", label)
        result = action()
        time.sleep(after)
        rec = self.window(t0, time.time(), label)
        rec.save(self.directory)
        log.info(
            "recording %s: %.1f s, %d points, %d log lines, %d fault codes",
            rec.id,
            rec.duration,
            len(rec.t),
            len(rec.logs),
            len(rec.faults),
        )
        return rec, result

    def load(self, recording_id: str) -> Recording:
        """Read back a recording saved by `record`, from its id."""
        return Recording.load(self.directory / f"{recording_id}.json.gz")
