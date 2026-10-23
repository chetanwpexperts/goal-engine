"""What a message says about the person, not just the task: their level, urgency, frustration, how much control they want.
Rules only, in English / Hinglish / Hindi. No AI."""

import difflib
import re

from app.mind import packs
from app.ml.normalize import tokens

BEGINNER = re.compile(r"\b(samajh nahi|samaj nahi|kuch samajh|don'?t understand|no idea|not technical|non[- ]technical|beginner|"
                      r"pata nahi|nahi pata|kaise karte|how do i even|i am new|naya hoon|nayi hoon)\b|समझ नहीं", re.I)
URGENT = re.compile(r"\b(urgent|urgently|asap|jaldi|turant|abhi ke abhi|immediately|quickly|right now|deadline|client is waiting)\b"
                    r"|!!|जल्दी|तुरंत", re.I)
FRUSTRATED = re.compile(r"\b(i told you|already (told|said|asked)|i said|i asked for|not what i (asked|wanted|said)|wrong answer|"
                        r"why (is it|are you|it is|did you) (giving|asking|showing|doing)|maine (kaha|bola|pucha)|phir se|fir se|again\??$|"
                        r"galat (hai|jawab|answer)|ye nahi (manga|maanga|pucha)|focus (on|kar) (my|meri|mere)|useless|bakwas|kya de raha|kya kar raha)\b|मैंने कहा|गलत", re.I)
ASK_FIRST = re.compile(r"\bbefore (mak\w*|doing|any|you|you make)\b.{0,14}\bchang|\b(don'?t|do not|dont)\s+(change|touch|modify|edit)|\bwithout chang|"
                       r"\b(ask|pucho|poochho|puchh?o) (me )?(first|pehle)|\bpehle (pucho|batao|bata do|dikhao)|\bconfirm (first|before)|"
                       r"\b(change|badlav|badal)\w*\s+mat\b|मत बदल|पहले पूछ", re.I)
JUST_DO = re.compile(r"\b(just do it|go ahead|directly|seedha|sidha|bina puche|without asking|don'?t ask|mat pucho|kar hi do|bas kar do)\b", re.I)
NO_FORMULA = re.compile(r"\b(no formula|without formula|formula (mat|nahi|nhi|not)|don'?t (want|need|give).{0,10}formula|why.{0,15}formula|"
                        r"formula kyu|formula kyun)\b", re.I)
ALWAYS_FORMULA = re.compile(r"\b(always|har baar|hamesha|every time|with every answer|saath mein)\b.{0,25}\b(formula|formule)|"
                            r"\b(formula|formule)\b.{0,20}\b(always|har baar|hamesha|every time|bhi dena|saath mein dena)\b", re.I)
WANT_FORMULA = re.compile(r"\b(formula|formule|fourmula|farmula)\b", re.I)


def similar(a: str, b: str) -> float:
    ta, tb = tokens(a or ""), tokens(b or "")
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


def read(text: str, previous: list[str] | None = None) -> dict:
    """Signals in this one message. previous = the person's earlier messages in this conversation (latest last)."""
    t = text or ""
    words = len(t.split())
    s = {
        "expert": bool(packs.expert_rx() and packs.expert_rx().search(t)),     # practitioners' words, from the agents' packs
        "beginner": bool(BEGINNER.search(t)),
        "urgent": bool(URGENT.search(t)),
        "frustrated": bool(FRUSTRATED.search(t)),
        "ask_first": bool(ASK_FIRST.search(t)),
        "just_do": bool(JUST_DO.search(t)),
        "no_formula": bool(NO_FORMULA.search(t)),
        "wants_formula": bool(WANT_FORMULA.search(t)) and not NO_FORMULA.search(t),
        "always_formula": bool(ALWAYS_FORMULA.search(t)) and not NO_FORMULA.search(t),
        "short": words <= 4,
        "repeat": False,
    }
    last = (previous or [])[-1:] or []
    if last and words >= 3 and similar(t, last[0]) >= 0.75:
        s["repeat"] = True                   # the same thing again, in nearly the same words: the last answer missed
    return s
