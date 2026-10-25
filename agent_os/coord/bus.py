"""Message bus: one append-only markdown inbox per agent. Messages are data, never instructions.

Every message, crew or external, is stored as a quoted block, so nothing in an
inbox can be mistaken for a charter-level directive when it lands in a context
pack. External messages also pass through `quarantine()` on arrival, and their
flags are written next to them: a flagged message is visible to a human and
invisible to routing (see the dispatcher, which parks it rather than making a task).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..memory.note import fmt_time
from ..quarantine import quarantine
from ..runtime import Blocked, Runtime

INBOX = "Inbox"
PARKED = f"{INBOX}/_parked.md"
HEAD_RE = re.compile(r"^## (M-\d{4}) · (\S+) · from (\S+)$")


@dataclass(frozen=True)
class Message:
    id: str
    to: str
    sender: str           # an agent name, or "external:<channel>"
    text: str
    sent: str
    flags: tuple = ()

    @property
    def external(self) -> bool:
        return self.sender.startswith("external:")

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


class MessageBus:
    def __init__(self, runtime: Runtime, steward: str = "Captain"):
        self.rt, self.steward = runtime, steward

    def _next_id(self) -> str:
        top = os.path.join(self.rt.root, INBOX)
        n = 0
        for fn in sorted(os.listdir(top)) if os.path.isdir(top) else []:
            for line in (self.rt.read(f"{INBOX}/{fn}") or "").splitlines():
                m = HEAD_RE.match(line)
                if m:
                    n = max(n, int(m.group(1)[2:]))
        return f"M-{n + 1:04d}"

    def _post(self, to: str, sender: str, text: str, flags: tuple, path: str | None = None) -> Message:
        msg = Message(self._next_id(), to, sender, text.strip(), fmt_time(self.rt.clock()), flags)
        block = [f"## {msg.id} · {msg.sent} · from {sender}"]
        if flags:
            block.append(f"flags: {', '.join(flags)}")
        block += ["> " + l for l in msg.text.splitlines()] + [""]
        self.rt.append(self.steward, path or f"{INBOX}/{to}.md", "\n".join(block) + "\n")
        return msg

    def send(self, sender: str, to: str, text: str) -> Message:
        """Crew-to-crew. The sender must be an active chartered agent."""
        c = self.rt.charters.get(sender)
        if c is None or not c.runnable:
            self.rt.feed.log(sender, f"BLOCKED message to {to} (no active charter)")
            raise Blocked(f"{sender} has no active charter")
        msg = self._post(to, sender, text, ())
        self.rt.feed.log(sender, f"message {msg.id} to {to}", artifact=f"{INBOX}/{to}.md")
        return msg

    def receive_external(self, to: str, text: str, channel: str) -> Message:
        """Anything from outside the crew: quarantined and flagged on the way in."""
        q = quarantine(text, f"external:{channel}")
        msg = self._post(to, f"external:{channel}", text, q.flags)
        warn = f" ⚠ {', '.join(q.flags)}" if q.flags else ""
        self.rt.feed.log(self.steward, f"received {msg.id} from {channel} for {to}{warn}",
                         artifact=f"{INBOX}/{to}.md")
        return msg

    def inbox(self, agent: str) -> list[Message]:
        return self._parse(f"{INBOX}/{agent}.md", agent)

    def parked_ids(self) -> set[str]:
        return {m.id for m in self._parse(PARKED, "_parked")}

    def park(self, msg: Message, reason: str) -> None:
        """Record a parked message for a human. Parking is the terminal state for it."""
        body = f"{msg.id} from {msg.sender} parked: {reason}\n{msg.text}"
        block = [f"## {msg.id} · {fmt_time(self.rt.clock())} · from {msg.sender}",
                 f"flags: {', '.join(msg.flags)}"] + ["> " + l for l in body.splitlines()] + [""]
        self.rt.append(self.steward, PARKED, "\n".join(block) + "\n")

    def _parse(self, rel: str, to: str) -> list[Message]:
        out, cur, flags, lines = [], None, (), []

        def flush():
            if cur:
                out.append(Message(cur[0], to, cur[2], "\n".join(lines).strip(), cur[1], flags))
        for line in (self.rt.read(rel) or "").splitlines():
            m = HEAD_RE.match(line)
            if m:
                flush()
                cur, flags, lines = m.groups(), (), []
            elif cur and line.startswith("flags: "):
                flags = tuple(f.strip() for f in line[7:].split(","))
            elif cur and line.startswith("> "):
                lines.append(line[2:])
            elif cur and line == ">":
                lines.append("")
        flush()
        return out
