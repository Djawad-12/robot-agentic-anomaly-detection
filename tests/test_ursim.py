"""Decoding of URSim packets and log lines, and documentation search, on hand-made data (no simulator)."""

from __future__ import annotations

import struct

from robot_agentic.connectors import ursim
from robot_agentic.core.events import Level
from robot_agentic.docs import DocSearch, Passage


def test_realtime_packet() -> None:
    """The channels are read at the right offsets of the Real-Time packet."""
    size = 1108
    packet = bytearray(size)
    struct.pack_into(">i", packet, 0, size)
    struct.pack_into(">6d", packet, 252, *[0.1 * i for i in range(6)])
    struct.pack_into(">6d", packet, 348, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    struct.pack_into(">d", packet, 756, 7.0)
    s = ursim.parse_realtime(bytes(packet), t=1.0)
    assert s.values["joint_3_q_actual"] == 0.30000000000000004
    assert s.values["joint_2_current_actual"] == 3.0
    assert s.values["robot_mode"] == 7.0
    assert "tcp_pose_z" in s.values


def test_primary_error_code() -> None:
    """An "error code" message becomes a FaultCode in teach-pendant format (C204A3), with its source."""
    body = struct.pack(">iiiBI", 204, 3, 4, 0, 0) + b"Joint position deviates"
    head = struct.pack(">BQbb", ursim.ROBOT_MESSAGE, 0, 112, ursim.ERROR_CODE)
    packet = struct.pack(">i", 4 + len(head) + len(body)) + head + body
    fault = ursim.parse_primary(packet, t=1.0)
    assert fault is not None
    assert fault.code == "C204A3" and fault.level == Level.ERROR and "JOINT2" in fault.message


def test_log_lines() -> None:
    """The level is read in the line; connection messages are demoted to DEBUG."""
    line = ursim.parse_log_line("2026-10-24_14:00:54:48634 00:00:53:014 ERROR   - RuntimeException: Syntax error on line 1", "controller")
    assert line is not None and line.level == Level.ERROR and line.text.startswith("RuntimeException")
    noise = ursim.parse_log_line(
        "2026-10-24_14:00:54:48634 00:00:53:016 WARNING - TCPReceiver closed (CLIENTTYPE_MATLAB_CLIENT)", "controller"
    )
    assert noise is not None and noise.level == Level.DEBUG


def test_polyscope_line_with_colour_codes() -> None:
    """Colour codes at the start of a PolyScope line do not hide its level."""
    line = ursim.parse_log_line("\x1b[31m2026-10-24 14:00:01.875 ERROR -  Program failed {thread: main}\x1b[0m", "polyscope")
    assert line is not None and line.level == Level.ERROR and line.text == "Program failed"


def test_doc_search_finds_error_code() -> None:
    """A fault code is found in the right passage, with its page."""
    passages = [
        Passage("manual.pdf", 12, "C204A3 Joint position deviates from target. Check the joint for friction or blocked brake."),
        Passage("manual.pdf", 40, "Installing the robot on a table requires four bolts."),
    ]
    hits = DocSearch(passages).search("code C204A3 joint", k=1)
    assert hits and hits[0][1].page == 12
