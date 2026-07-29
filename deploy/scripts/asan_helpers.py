#!/usr/bin/env python3
"""Shared ASan / smoke helpers for PR comments and triage."""

from __future__ import annotations

import re
from dataclasses import dataclass


ASAN_ERROR_RE = re.compile(
    r"ERROR:\s+(AddressSanitizer|LeakSanitizer|MemorySanitizer|UndefinedBehaviorSanitizer):\s+(\S+)"
)
SUMMARY_RE = re.compile(r"^SUMMARY:\s+\w+:\s+(.+)$", re.MULTILINE)
# #0 0x… in Func /path/file.cc:16:10
FRAME0_SRC_RE = re.compile(
    r"#0\s+\S+\s+in\s+(\S+)\s+(\S+?):(\d+)(?::\d+)?"
)
# #0 0x… in Func (binary+offset)
FRAME0_BIN_RE = re.compile(
    r"#0\s+\S+\s+in\s+(\S+)\s+\(([^)]+)\)"
)


@dataclass
class AsanInfo:
    bug_type: str = ""
    summary: str = ""
    frame_func: str = ""
    frame_file: str = ""
    frame_line: str = ""

    def display_frame(self) -> str:
        if self.frame_file and self.frame_line:
            return f"{self.frame_func} at `{self.frame_file}:{self.frame_line}`"
        if self.frame_file:
            return f"{self.frame_func} at `{self.frame_file}`"
        return self.frame_func or "(unknown frame)"


def parse_asan(log_text: str) -> AsanInfo:
    info = AsanInfo()
    m = ASAN_ERROR_RE.search(log_text)
    if m:
        info.bug_type = m.group(2).strip()
    sm = SUMMARY_RE.search(log_text)
    if sm:
        info.summary = sm.group(1).strip()
        if not info.bug_type and info.summary:
            parts = info.summary.split()
            if parts:
                info.bug_type = parts[0].rstrip(",")
    src = FRAME0_SRC_RE.search(log_text)
    if src:
        info.frame_func = src.group(1)
        info.frame_file = src.group(2)
        info.frame_line = src.group(3)
        return info
    binary = FRAME0_BIN_RE.search(log_text)
    if binary:
        info.frame_func = binary.group(1)
        info.frame_file = binary.group(2)
    return info


def is_demo_boom_crash(data: bytes) -> bool:
    return data == b"BOOM" or data.startswith(b"BOOM")


def is_demo_target_path(path: str) -> bool:
    p = path.replace("\\", "/")
    return "demo-target/" in p or p.endswith("demo_fuzz.cc") or "demo-fuzz" in p


def triage_mentions_unknown_symbols(markdown: str, allowed: set[str]) -> bool:
    """True if markdown looks like it invented identifiers not in allowed set.

    Heuristic: flagged when it proposes strncpy/buffer assert patterns while
    stack points at intentional OOB, or cites identifiers absent from allowed.
    """
    lower = markdown.lower()
    # Common hallucination for our demo crash
    if "strncpy" in lower or "sizeof(buffer)" in lower:
        return True
    # Extract backtick identifiers
    ids = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]{2,})`", markdown))
    noise = {"c", "cpp", "py", "asan", "llm", "pr"}
    ids -= noise
    if not allowed or not ids:
        return False
    # If more than half of cited ids are unknown → suspicious
    unknown = [i for i in ids if i not in allowed and i.lower() not in {a.lower() for a in allowed}]
    return len(unknown) >= max(2, len(ids) // 2)
