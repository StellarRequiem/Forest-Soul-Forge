"""Forest's synaptic layer — a contextual, tamper-evident trust graph.

THE THESIS (ADR-0095). A polymorphic cognitive mesh becomes more than an agent
swarm when the *connections* between its nodes carry weight: a source, model,
agent, tool, or strategy earns trust through repeated useful performance and
loses it through hallucination, stale data, failed tests, or unsafe behavior.
This module is that synaptic layer — the place where "machine experience" lives
in a form you can audit.

Four properties make it Forest-shaped rather than just a scoreboard:

  1. PROVENANCE-FIRST.  Trust is not a stored number you can edit. It is a
     deterministic *fold* over an append-only, sha256 hash-chained ledger of
     outcomes. Replaying the ledger reproduces every weight exactly; tampering
     with any past outcome breaks the chain (``verify()`` catches it). This is
     the audit-chain discipline (ADR-0049) applied to trust itself: you cannot
     forge a node's track record.

  2. CONTEXTUAL.  Trust is per *(node, problem_class)*, not global. A node
     trusted for regulatory-timing analysis is not blindly trusted for code
     review. This is the metacognitive question — "which nodes are good at THIS
     class of problem" — made into state.

  3. CALIBRATED + HONEST ABOUT IGNORANCE.  Each (node, class) carries a
     Beta(α, β) posterior over its success rate. Trust is the posterior mean,
     but it always travels with a credible interval and an observation count, so
     0.80 after 3 trials (wide, uncertain) is never confused with 0.80 after 300
     (sharp). Routing uses Thompson sampling from the posteriors — principled
     exploration of under-tested nodes, exploitation of proven ones.

  4. GOVERNED.  The mesh may autonomously *weaken* and *quarantine* a node whose
     trust collapses (the cheap, reversible, safe direction). It may NOT
     autonomously *promote* a quarantined node, or convert trust into capability
     (permissions, capital, execution). Those cross the human-gated boundary
     (ADR-0095). Quarantine is automatic; release is a decision.

Pure standard library. No daemon, no network. Decoupled like ``core.audit_chain``
so the runtime can wire it in without this module knowing about the registry.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

GENESIS_PREV_HASH = "0" * 64
LEDGER_SCHEMA = "fsf.trust_graph.v1"


def _canonical(seq: int, prev_hash: str, node: str, problem_class: str,
               success: bool, weight: float, evidence: str | None) -> bytes:
    """Deterministic, key-ordered serialization of an outcome's signed content."""
    return json.dumps(
        {"seq": seq, "prev_hash": prev_hash, "node": node,
         "problem_class": problem_class, "success": bool(success),
         "weight": round(float(weight), 6), "evidence": evidence},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Outcome:
    """One audited outcome — an immutable, hash-chained ledger entry."""
    seq: int
    node: str
    problem_class: str
    success: bool
    weight: float
    evidence: str | None
    prev_hash: str
    entry_hash: str

    def to_json_line(self) -> str:
        return json.dumps({
            "schema": LEDGER_SCHEMA, "seq": self.seq, "node": self.node,
            "problem_class": self.problem_class, "success": self.success,
            "weight": self.weight, "evidence": self.evidence,
            "prev_hash": self.prev_hash, "entry_hash": self.entry_hash,
        }, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class TrustScore:
    """A node's trust for a problem class — a Beta posterior, honestly reported."""
    node: str
    problem_class: str
    alpha: float
    beta: float

    @property
    def n(self) -> float:
        """Effective observation count (excludes the Beta(1,1) prior)."""
        return (self.alpha - 1.0) + (self.beta - 1.0)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def stdev(self) -> float:
        a, b = self.alpha, self.beta
        return math.sqrt((a * b) / (((a + b) ** 2) * (a + b + 1.0)))

    def interval(self, z: float = 1.96) -> tuple[float, float]:
        """Normal-approx credible interval, clamped to [0, 1]."""
        lo = max(0.0, self.mean - z * self.stdev)
        hi = min(1.0, self.mean + z * self.stdev)
        return (lo, hi)

    def __repr__(self) -> str:
        lo, hi = self.interval()
        return (f"trust({self.node}@{self.problem_class})="
                f"{self.mean:.2f} [{lo:.2f},{hi:.2f}] n={self.n:.0f}")


class TrustGraph:
    """Append-only, hash-chained, contextual trust over audited outcomes.

    State is a pure fold over the ledger: ``record`` appends one entry and
    advances the posterior; ``replay``/``load`` rebuild identical state from the
    ledger alone. The ledger is the truth; the posteriors are a cache of it.
    """

    def __init__(self, prior: tuple[float, float] = (1.0, 1.0), *,
                 ledger_path: str | Path | None = None) -> None:
        if prior[0] <= 0 or prior[1] <= 0:
            raise ValueError("Beta prior parameters must be > 0")
        self._prior = prior
        self._ledger: list[Outcome] = []
        self._post: dict[tuple[str, str], list[float]] = {}  # (node,class) -> [alpha, beta]
        self._head = GENESIS_PREV_HASH
        self._seq = -1
        #: When set, record() appends each new outcome to this JSONL file so the
        #: graph is durable across daemon restarts (the ledger stays the truth).
        self._ledger_path = Path(ledger_path) if ledger_path else None

    # -- write ------------------------------------------------------------
    def record(self, node: str, problem_class: str, success: bool, *,
               weight: float = 1.0, evidence: str | None = None) -> Outcome:
        """Append one audited outcome and update the (node, class) posterior.

        ``weight`` lets a strong signal (a verified failure, a refuted claim)
        count for more than a soft one. ``evidence`` is a free-form pointer
        (an audit seq, a test id, a URL) — the provenance ``why()`` surfaces.
        """
        if not node or not problem_class:
            raise ValueError("node and problem_class must be non-empty")
        if weight <= 0:
            raise ValueError("weight must be > 0")
        seq = self._seq + 1
        entry_hash = _sha256(_canonical(seq, self._head, node, problem_class,
                                        success, weight, evidence))
        entry = Outcome(seq=seq, node=node, problem_class=problem_class,
                        success=bool(success), weight=float(weight),
                        evidence=evidence, prev_hash=self._head,
                        entry_hash=entry_hash)
        self._apply(entry)
        self._ledger.append(entry)
        self._head = entry_hash
        self._seq = seq
        if self._ledger_path is not None:
            with self._ledger_path.open("a", encoding="utf-8") as f:
                f.write(entry.to_json_line() + "\n")
        return entry

    def _apply(self, e: Outcome) -> None:
        key = (e.node, e.problem_class)
        post = self._post.setdefault(key, [self._prior[0], self._prior[1]])
        if e.success:
            post[0] += e.weight
        else:
            post[1] += e.weight

    # -- read -------------------------------------------------------------
    def trust(self, node: str, problem_class: str) -> TrustScore:
        a, b = self._post.get((node, problem_class), self._prior)
        return TrustScore(node, problem_class, a, b)

    def best(self, candidates: Iterable[str], problem_class: str, *,
             rng: random.Random | None = None) -> str | None:
        """Route to ONE node via Thompson sampling — sample each candidate's
        Beta posterior, pick the max. Under-tested nodes (wide posteriors) get
        explored; proven nodes get exploited. Pass a seeded ``rng`` for
        deterministic routing (tests, replays)."""
        ranked = self.rank(candidates, problem_class, rng=rng)
        return ranked[0][0] if ranked else None

    def rank(self, candidates: Iterable[str], problem_class: str, *,
             rng: random.Random | None = None) -> list[tuple[str, float]]:
        r = rng or random
        out = []
        for c in candidates:
            a, b = self._post.get((c, problem_class), self._prior)
            out.append((c, r.betavariate(a, b)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    def quarantined(self, *, threshold: float = 0.4, min_n: float = 5.0
                    ) -> list[TrustScore]:
        """Nodes the mesh may AUTONOMOUSLY weaken/isolate: confidently below
        ``threshold`` (the credible-interval upper bound is under it) with at
        least ``min_n`` observations — enough evidence that it's not noise.

        Per ADR-0095, surfacing a quarantine candidate is automatic; *releasing*
        one is a human-gated promotion, not something this method performs."""
        out = []
        for (node, pc), (a, b) in self._post.items():
            s = TrustScore(node, pc, a, b)
            if s.n >= min_n and s.interval()[1] < threshold:
                out.append(s)
        out.sort(key=lambda s: s.mean)
        return out

    def why(self, node: str, problem_class: str) -> list[Outcome]:
        """The provenance behind a trust value: every outcome that shaped it,
        in order. This is the answer to 'why is this node trusted 0.83?'"""
        return [e for e in self._ledger
                if e.node == node and e.problem_class == problem_class]

    def nodes(self) -> list[str]:
        return sorted({n for (n, _pc) in self._post})

    def problem_classes(self) -> list[str]:
        return sorted({pc for (_n, pc) in self._post})

    def scores(self) -> list[TrustScore]:
        """Every (node, problem_class) trust score currently held, for display."""
        return [TrustScore(n, pc, a, b) for (n, pc), (a, b) in self._post.items()]

    # -- integrity --------------------------------------------------------
    def verify(self) -> tuple[bool, str | None]:
        """Replay the hash chain. Returns (ok, reason). A single edited past
        outcome — flipping a success, nudging a weight, reordering — breaks the
        link and is caught here. Trust you can't silently rewrite."""
        prev = GENESIS_PREV_HASH
        for i, e in enumerate(self._ledger):
            if e.seq != i:
                return (False, f"seq gap at index {i}: got {e.seq}")
            if e.prev_hash != prev:
                return (False, f"prev_hash mismatch at seq {e.seq}")
            expect = _sha256(_canonical(e.seq, e.prev_hash, e.node,
                                        e.problem_class, e.success, e.weight,
                                        e.evidence))
            if e.entry_hash != expect:
                return (False, f"entry_hash mismatch at seq {e.seq}")
            prev = e.entry_hash
        return (True, None)

    # -- persistence (ledger is the source of truth) ----------------------
    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            "".join(e.to_json_line() + "\n" for e in self._ledger), encoding="utf-8")

    @classmethod
    def replay(cls, entries: Iterable[dict], *,
               prior: tuple[float, float] = (1.0, 1.0)) -> "TrustGraph":
        """Rebuild a graph by re-recording each ledger entry — trust as a pure
        fold. Verifies chain integrity as it goes; raises on tamper."""
        g = cls(prior=prior)
        for obj in entries:
            e = g.record(obj["node"], obj["problem_class"], obj["success"],
                         weight=obj.get("weight", 1.0), evidence=obj.get("evidence"))
            if e.entry_hash != obj.get("entry_hash"):
                raise ValueError(
                    f"ledger tamper: recomputed hash != stored at seq {e.seq}")
        return g

    @classmethod
    def load(cls, path: str | Path, *,
             prior: tuple[float, float] = (1.0, 1.0)) -> "TrustGraph":
        lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
        return cls.replay((json.loads(l) for l in lines), prior=prior)

    @classmethod
    def load_or_create(cls, path: str | Path, *,
                       prior: tuple[float, float] = (1.0, 1.0)) -> "TrustGraph":
        """Open a PERSISTENT trust graph: replay an existing ledger (verifying
        integrity) or start fresh, then bind ``record()`` to append to it. This
        is the live, durable form the daemon uses — the ledger survives restarts
        and stays the single source of truth."""
        p = Path(path)
        g = cls.load(p, prior=prior) if p.exists() else cls(prior=prior)
        p.parent.mkdir(parents=True, exist_ok=True)
        g._ledger_path = p
        return g
