"""One agent run, start to finish:

    claim → context pack → act → write episode + learning → handoff → retro → release

Every step lands a feed line. The episode links every note the context pack cited,
which is what citation counts (and therefore consolidation and promotion) are built
from: knowledge compounds because each run records what it relied on.
"""
from __future__ import annotations

from .brain import Brain
from .memory.note import Note, slugify
from .model import Model, ModelRequest
from .runtime import Blocked


def _fresh_id(brain: Brain, agent: str, title: str) -> str:
    """Slug of the title, or `<agent>-<slug>` if another namespace already owns that slug."""
    base = slugify(title)
    owner = brain.memory.get(base)
    if owner is None or owner.path.split("/")[1] == agent:
        return base
    return slugify(f"{agent} {title}")


def run_task(brain: Brain, agent: str, task_id: str, model: Model, handoff_to: str = "",
             budget_chars: int = 2000) -> dict:
    rt, board = brain.rt, brain.board
    board.claim(agent, task_id)
    task = board.start(agent, task_id)
    source = f"agent:{agent}/run:{task_id}"
    written: list[str] = []
    try:
        pack = brain.build_context(agent, task, budget_chars)
        rt.feed.log(agent, f"context pack for {task_id}: {len(pack.items)} notes, "
                           f"{len(pack.text)}/{budget_chars} chars"
                           + (f", {sum(i.quarantined for i in pack.items)} quarantined"
                              if any(i.quarantined for i in pack.items) else ""))
        board.heartbeat(agent)
        reply = model.act(ModelRequest(agent, rt.charters[agent].role, task_id, task.title, pack.text))

        for title, body in reply.facts:
            n = Note(_fresh_id(brain, agent, title), "fact", title, body, source, "observed")
            brain.memory.remember(agent, n)
            written.append(n.id)
        if reply.learning:
            title, body = reply.learning
            n = Note(_fresh_id(brain, agent, title), "learning", title, body, source, "inferred")
            brain.memory.remember(agent, n)
            written.append(n.id)
        if reply.decision:
            title, subject, stance = reply.decision
            n = Note(_fresh_id(brain, agent, title), "decision", title, f"{title}.", source, "inferred",
                     subject=subject, stance=stance)
            brain.memory.remember(agent, n)
            written.append(n.id)
        if reply.deliverable:
            path, content = reply.deliverable
            rt.write(agent, path, content)

        relied = " ".join(f"[[{i}]]" for i in pack.cited_ids) or "nothing"
        made = " ".join(f"[[{i}]]" for i in written) or "nothing"
        episode = Note(f"ep-{task_id.lower()}-{agent.lower()}", "episode", f"{agent} on {task_id}: {task.title}",
                       f"{reply.summary}\n\nRelied on: {relied}\nWrote: {made}", source, "observed",
                       tags=("episode", task_id.lower()))
        brain.memory.remember(agent, episode)

        handoff = Note(f"handoff-{task_id.lower()}-{agent.lower()}", "handoff",
                       f"Handoff {task_id} from {agent}",
                       f"Done: {reply.summary}\nOpen: {reply.open_items or 'nothing'}\n"
                       f"Relied on: {relied}\nNew notes: {made}", source, "observed")
        brain.memory.remember(agent, handoff)
        if handoff_to:
            board.handoff(agent, task_id, handoff_to, handoff.id)
        else:
            board.submit(agent, task_id, handoff.id)
        retro = {"worked": f"{len(written)} notes written from {len(pack.items)} cited",
                 "failed": "none", "next": f"{handoff_to} picks up {task_id}" if handoff_to else "review"}
        rt.feed.log(agent, f"run complete on {task_id}", retro=retro)
        return {"pack": pack, "written": written, "episode": episode.id, "handoff": handoff.id}
    except Blocked as e:
        if board.holds(agent, board.get(task_id), rt.clock()):
            board.release(agent, task_id)
        rt.feed.log(agent, f"run ended blocked on {task_id}",
                    retro={"worked": "-", "failed": str(e), "next": "needs a human"})
        raise
