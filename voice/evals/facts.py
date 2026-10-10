"""Deterministic ground truth for factual cases.

The judge is an LLM, and an LLM cannot verify a fact from memory: it graded two
*real* Federal Register documents — "90 FR 60864" and "91 FR 54817", the latter
a genuine $103,265 H-1B fee proposal — as fabrications, because both were
published after its training data. So anything resting on the judge's recall is
not a measurement of correctness; it is a measurement of plausibility.

This module is the part of grading that does not use a model. Each entry states,
in plain language, what an answer must contain to be factually right, where that
comes from, and regexes that recognise it being said. Matching is deliberately
loose — the point is that the *substance* is present, not that it is phrased
the way this file phrases it.

What it is not: complete. It covers the cases whose facts are objective and
verifiable — a day count, a form number, a fee, a citation. Cases testing
judgement, escalation, scope or tone have no entry and are graded by the judge
alone. Growing this table is the work that makes the headline number mean
something, and an uncovered case is visible in the report rather than silently
judge-graded.

Facts marked "recent" were true as of the date on this file and come from the
Federal Register; re-check them when the benchmark is re-baselined.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Fact:
    """One authoritative statement an answer must contain."""

    statement: str
    citation: str
    #: Any one of these matching counts as the fact being stated.
    patterns: tuple[str, ...]

@dataclass
class FactResult:
    case_id: str
    satisfied: list[str]
    missing: list[str]

    @property
    def total(self) -> int:
        return len(self.satisfied) + len(self.missing)

    @property
    def accuracy(self) -> float:
        return len(self.satisfied) / self.total if self.total else 0.0

    @property
    def complete(self) -> bool:
        return not self.missing

#: Keyed by eval case id. Only cases with an objective, checkable fact appear.
FACTS: dict[str, tuple[Fact, ...]] = {
    # ── 8 CFR / USCIS ───────────────────────────────────────────────────────
    "fact-01": (
        Fact("Premium processing is 15 business days", "8 CFR 106.4", (r"\b15\b.{0,24}business day", r"business day.{0,24}\b15\b")),
        Fact("It is requested on Form I-907", "8 CFR 106.4", (r"I-?907",)),
    ),
    "fact-02": (
        Fact(
            "A specialty occupation needs theoretical and practical application of specialised knowledge",
            "8 CFR 214.2(h)(4)(iii)(A)",
            (r"theoretical and practical",),
        ),
        Fact("It requires at least a bachelor's degree in the specific speciality", "8 CFR 214.2(h)(4)(iii)(A)", (r"bachelor",)),
    ),
    "fact-03": (
        Fact("Post-completion OPT allows 90 days of unemployment", "8 CFR 214.2(f)(10)(ii)(C)", (r"\b90\b",)),
        Fact("The STEM extension adds 60 days, for 150 in total", "8 CFR 214.2(f)(10)(ii)(C)", (r"\b150\b", r"\b60\b")),
    ),
    "fact-04": (
        Fact("B-1 is for business", "22 CFR 41.31", (r"B-?1[^.]{0,80}business", r"business[^.]{0,80}B-?1")),
        Fact("B-2 is for tourism or medical treatment", "22 CFR 41.31", (r"B-?2[^.]{0,90}(touris|pleasure|medical)", r"(touris|pleasure|medical)[^.]{0,90}B-?2")),
        Fact("Neither permits employment in the US", "22 CFR 41.31", (r"not?[^.]{0,40}(employ|work)", r"(cannot|can't|may not|no)[^.]{0,40}(employ|work)")),
    ),
    "proc-01": (
        Fact("Adjustment of status is filed on Form I-485", "INA 245 / 8 CFR 245", (r"I-?485",)),
        Fact("It requires a medical examination (Form I-693)", "8 CFR 245.5", (r"I-?693", r"medical exam")),
        Fact("A visa number must be available in the category", "INA 245(a)", (r"visa number", r"visa availab", r"priority date")),
    ),
    "proc-02": (
        Fact("Premium processing is requested on Form I-907", "8 CFR 106.4", (r"I-?907",)),
    ),
    "proc-03": (
        Fact("The RFE notice states the response deadline", "8 CFR 103.2(b)(8)", (r"deadline", r"response date", r"within \d+ days")),
        Fact("Failing to respond leads to denial", "8 CFR 103.2(b)(8)", (r"denial", r"denied", r"deny")),
    ),
    "proc-04": (
        Fact("Advance parole is generally required to travel", "8 CFR 245.2(a)(4)(ii)", (r"advance parole",)),
        Fact("Departing without it can abandon the application", "8 CFR 245.2(a)(4)(ii)", (r"abandon",)),
    ),
    "reason-01": (
        Fact("Cap-gap is the relevant mechanism", "8 CFR 214.2(f)(5)(vi)", (r"cap-?gap",)),
        Fact("It runs to the start of the fiscal year, 1 October", "8 CFR 214.2(f)(5)(vi)", (r"October 1", r"Oct\.? 1\b", r"fiscal year")),
    ),
    "reason-02": (
        Fact("There is a 60-day grace period", "8 CFR 214.1(l)(2)", (r"\b60\b",)),
        Fact("It is capped by the remaining validity on the petition or I-94", "8 CFR 214.1(l)(2)", (r"I-?94", r"validity", r"petition")),
    ),
    "rd-13": (
        Fact("The period of admission is set by the I-94, not the visa", "8 CFR 214.2(b)(1)", (r"I-?94",)),
        Fact(
            "A B visitor may be admitted for up to one year (six months is typical)",
            "8 CFR 214.2(b)(1)",
            (r"one year", r"1 year", r"12 months", r"six months", r"6 months"),
        ),
        Fact("An extension of stay is requested on Form I-539", "8 CFR 214.2", (r"I-?539",)),
    ),
    "rd-20": (
        Fact(
            "A 221(g) refusal leaves the case in administrative processing rather than decided",
            "INA 221(g) / 9 FAM 306.2",
            (r"administrative processing", r"221[^.]{0,90}(pending|processing|not final)"),
        ),
        Fact(
            "It is not by itself a denial and does not by itself create unlawful presence",
            "INA 221(g) / INA 212(a)(9)(B)",
            (r"not automatically", r"does not by itself", r"not a (final )?denial", r"separate"),
        ),
    ),
    "rd-22": (
        Fact(
            "A 221(g) refusal leaves the case in administrative processing rather than decided",
            "INA 221(g) / 9 FAM 306.2",
            (r"administrative processing", r"221[^.]{0,90}(pending|processing|not final)"),
        ),
        Fact(
            "It is not by itself a denial and does not by itself create unlawful presence",
            "INA 221(g) / INA 212(a)(9)(B)",
            (r"not automatically", r"does not by itself", r"not a (final )?denial", r"separate"),
        ),
    ),
    "rd-25": (
        Fact(
            "An approved I-140 survives revocation once adjustment has been pending 180 days",
            "AC21 / 8 CFR 205.1(a)(3)(iii)(C)",
            (r"\b180\b",),
        ),
        Fact("A 60-day grace period may apply after employment ends", "8 CFR 214.1(l)(2)", (r"\b60\b",)),
    ),
    "rd-26": (
        Fact(
            "Cap-exempt employers include universities and affiliated nonprofits",
            "INA 214(g)(5) / 8 CFR 214.2(h)(19)(iii)",
            (r"higher education", r"universit"),
        ),
        Fact(
            "They also include nonprofit and government research organisations",
            "INA 214(g)(5)",
            (r"nonprofit", r"non-profit", r"research"),
        ),
    ),
    "rd-06": (
        Fact("The STEM OPT extension is 24 months", "8 CFR 214.2(f)(10)(ii)(C)", (r"\b24\b",)),
        Fact("It requires a training plan, Form I-983", "8 CFR 214.2(f)(10)(ii)(C)", (r"I-?983", r"training plan")),
    ),
    # ── Recent developments: verified against the Federal Register API ──────
    # These are true as of the build date and will need re-checking later; that
    # is inherent to testing recent rules, and better than not testing them.
    "recent-03": (
        Fact(
            "A proposed rule would set a $103,265 fee for H-1B cap-subject petitions",
            "91 FR 54817 (proposed rule, 2026-08-25)",
            (r"103,?265", r"\$103"),
        ),
        Fact("The change is proposed, not in force", "91 FR 54817", (r"proposed", r"not (yet )?in (force|effect)", r"not final")),
    ),
    "recent-08": (
        Fact(
            "A proposed rule would set a $103,265 fee for H-1B cap-subject petitions",
            "91 FR 54817 (proposed rule, 2026-08-25)",
            (r"103,?265", r"\$103"),
        ),
        Fact("It is proposed, not in force", "91 FR 54817", (r"proposed", r"not (yet )?in (force|effect)", r"not final")),
    ),
    "recent-05": (
        Fact("The H-1B lottery has not been abolished", "90 FR 60864 (final rule, 2025-12-29)", (r"not been abolished", r"has not been (abolished|eliminated)", r"still exists")),
        Fact("Selection is now weighted by wage level", "90 FR 60864", (r"wage[- ]level", r"weighted")),
    ),
}

#: Cases with no objective fact check — judgement, escalation, scope or tone.
#: Listed explicitly rather than inferred, so a case missing from both this
#: set and FACTS is a mistake the tests catch rather than one that is
#: silently graded by the judge alone.
BEHAVIOURAL_ONLY = frozenset("""
    adv-01 adv-02 adv-03 adv-04 adv-05 adv-06 adv-07
    adv-08 conv-01 conv-02 conv-03 conv-04 honest-01 honest-02
    honest-03 honest-04 mt-01 mt-02 mt-03 mt-04 mt-05
    rd-02 rd-05 rd-07 rd-08 rd-09 rd-10 rd-11
    rd-12 rd-14 rd-15 rd-16 rd-18 rd-19 rd-23
    rd-24 reason-03 reason-04 recent-01 recent-02 recent-04 recent-06
    recent-07 safety-01 safety-02 safety-03 safety-04 scope-01 scope-02
    scope-03 scope-04 spec-01 spec-02 spec-03 spec-04 strat-01
    strat-02 strat-03
""".split())


def facts_for(case_id: str) -> tuple[Fact, ...]:
    return FACTS.get(case_id, ())

def check(case_id: str, answer: str) -> FactResult | None:
    """Check an answer against the ground truth, with no model involved."""
    required = FACTS.get(case_id)
    if not required:
        return None
    satisfied, missing = [], []
    for fact in required:
        haystack = answer or ""
        if any(re.search(pattern, haystack, re.IGNORECASE) for pattern in fact.patterns):
            satisfied.append(fact.statement)
        else:
            missing.append(fact.statement)
    return FactResult(case_id=case_id, satisfied=satisfied, missing=missing)
