"""Décodage des paquets URSim et recherche documentaire, sur des données construites à la main (pas de simulateur)."""

from __future__ import annotations

import struct

from robot_agentic.connectors.ursim import logs, primary, realtime
from robot_agentic.core.events import FaultCode, Level
from robot_agentic.docs.indexer import Passage
from robot_agentic.docs.search import DocSearch


def test_realtime_packet() -> None:
    """Les voies sont lues aux bons décalages du paquet temps réel."""
    size = 1108
    packet = bytearray(size)
    struct.pack_into(">i", packet, 0, size)
    struct.pack_into(">6d", packet, 252, *[0.1 * i for i in range(6)])
    struct.pack_into(">6d", packet, 348, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    struct.pack_into(">d", packet, 756, 7.0)
    s = realtime.parse(bytes(packet), t=1.0)
    assert s.values["joint_3_q_actual"] == 0.30000000000000004
    assert s.values["joint_2_current_actual"] == 3.0
    assert s.values["robot_mode"] == 7.0
    assert "tcp_pose_z" in s.values


def test_primary_error_code() -> None:
    """Un message « error code » devient un FaultCode au format de la tablette (C204A3), avec sa source."""
    body = struct.pack(">iiiBI", 204, 3, 4, 0, 0) + b"Joint position deviates"
    head = struct.pack(">BQbb", primary.ROBOT_MESSAGE, 0, 112, primary.ERROR_CODE)
    packet = struct.pack(">i", 4 + len(head) + len(body)) + head + body
    msg = primary.parse(packet, t=1.0)
    assert isinstance(msg, FaultCode)
    assert msg.code == "C204A3" and msg.level == Level.ERROR and "JOINT2" in msg.message


def test_log_lines() -> None:
    """Le niveau est lu dans la ligne ; les messages de connexion sont rétrogradés en DEBUG."""
    line = logs.parse("2026-10-24_14:00:54:48634 00:00:53:014 ERROR   - RuntimeException: Syntax error on line 1", "controller")
    assert line is not None and line.level == Level.ERROR and line.text.startswith("RuntimeException")
    noise = logs.parse("2026-10-24_14:00:54:48634 00:00:53:016 WARNING - TCPReceiver closed (CLIENTTYPE_MATLAB_CLIENT)", "controller")
    assert noise is not None and noise.level == Level.DEBUG


def test_doc_search_finds_error_code() -> None:
    """Un code d'erreur est retrouvé dans le bon passage, avec sa page."""
    passages = [
        Passage("manuel.pdf", 12, "C204A3 Joint position deviates from target. Check the joint for friction or blocked brake."),
        Passage("manuel.pdf", 40, "Installing the robot on a table requires four bolts."),
    ]
    hits = DocSearch(passages).search("code C204A3 articulation", k=1)
    assert hits and hits[0][1].page == 12
