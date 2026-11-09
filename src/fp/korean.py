"""Heuristic 'is this a Korean name?' scorer for DBLP author names (romanized, Western order).

score in [0,1] = combination of
  surname prior (romanized Korean surnames, down-weighted when common in Chinese/Vietnamese), and
  given-name evidence (segmentable into Korean romanization syllables; Korean-only spellings like
  eo/eu/ae/yeo/oo/hy-/ky-; penalties for pinyin-only spellings like x/q/zh/ao/ian/iu/ei/ing).
Ambiguous scores are later refined by co-authorship with KR faculty and (optionally) an LLM pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# surname → prior that a bearer is Korean (CS-author population)
SURNAMES: dict[str, float] = {
    **dict.fromkeys(
        "kim gim park pak bak choi choe jung jeong cho jo yoon kwon kweon gwon hwang ahn bae baek paik "
        "heo huh hur kwak gwak byun byeon pyun pyeon chae eom um uhm seo suh shin jang jeon yoo ryu ryoo "
        "rhee moon mun noh roh sohn shon cha joo shim yeo yeom myung myeong pyo maeng seok yook yuk seol "
        "kye yim rim rhim jee hyun hyeon jegal sunwoo namgoong namkung hwangbo dokgo seonwoo "
        "whang choo koo baik myaeng chwa kahng lyu nho rhyu youn yum yhang jhun jhang ihm kwag pahk".split(),
        0.92,
    ),
    **dict.fromkeys("oh son sung seong chun cheon tak won bang ham bong nah rah shon kyung soh chey goh kho".split(), 0.75),
    **dict.fromkeys("lee lim im min yun yi chung sim ra gil ki woo nam hahn kee nah kang".split(), 0.6),
    **dict.fromkeys("han hong song ko koh ha ji na sul kook ok so no chang chong".split(), 0.45),
    **dict.fromkeys("yang jin an yu gong kong chu do".split(), 0.25),
    **dict.fromkeys("wang ma sun ryu".split(), 0.15),
}
SURNAMES["ryu"] = 0.9  # 류/유 — almost always Korean in romanized form

_INIT = r"(?:kk|tt|pp|ss|jj|ch|sh|wh|k|g|n|d|t|r|l|m|b|p|s|j|h|w|y)?"
_VOW = r"(?:yeo|yae|wae|weo|ou(?=ng)|eo|ae|ai|ya|ye|oe|wa|wo|we|wi|yo|yu|eu|ui|oo|ee|ea|ah|uh|ey|a|e|i|o|u)"
_FIN = r"(?:ng|ck|k|n|l|m|p|b|g|t)?"
SYLLABLE = re.compile(rf"{_INIT}{_VOW}{_FIN}")

# spellings that (almost) only occur in Korean romanization
KOREAN_MARKERS = re.compile(
    r"eo|eu|ae|yeo|oo|ee|^hy|[kgpbm]y[uoe]|wook|hoon|chul|cheol|seok|suk\b|sik\b|hwan|hee\b|soo\b"
    r"|young|jae|tae|seung|joon|gyu|kyu|hyun|woong|^won|won$"
)
# spellings typical of Hanyu pinyin / Vietnamese / Japanese, rare in Korean names
FOREIGN_MARKERS = re.compile(
    r"[xqzfv]|c(?![hk])|zh|ao|iu|ian|iao|uan|uai|uo|ei|ie|ia(?!n)|(?<!y)ou(?!ng)|ing$|eng$|(?<!e)en$|"
    r"^(?:thi|van|duc|minh|thanh|nguyen|takashi|hiroshi|yuki|kenji|taro)$|tsu|shi$"
)


@dataclass
class Verdict:
    score: float
    surname: str
    given: str
    reason: str


def split_name(name: str) -> tuple[str, list[str]]:
    """DBLP name → (surname, given tokens). Drops numeric disambiguator and initials."""
    name = re.sub(r"\s+\d{4}$", "", name.strip())
    toks = [t for t in re.split(r"\s+", name) if t]
    if len(toks) < 2:
        return (toks[0].lower() if toks else ""), []
    surname = toks[-1].lower()
    given = [t for t in toks[:-1] if not re.fullmatch(r"[A-Z]\.?(-[A-Z]\.?)?", t)]
    return surname, given


def _segments(word: str) -> list[str] | None:
    """Split a lowercase given-name chunk into Korean syllables (DP); None if impossible."""
    n = len(word)
    best: list[list[str] | None] = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(n, i, -1):
            if SYLLABLE.fullmatch(word, i, j):
                cand = best[i] + [word[i:j]]
                if best[j] is None or len(cand) < len(best[j]):
                    best[j] = cand
    return best[n]


def _token_evidence(tok: str) -> tuple[float, str]:
    chunks = [p.lower() for p in re.split(r"-|(?<=[a-z])(?=[A-Z])", tok) if p]
    syllables = 0
    for c in chunks:
        if not c.isalpha():
            return -0.5, f"non-alpha {c!r}"
        if FOREIGN_MARKERS.search(c):
            return -0.6, f"non-Korean spelling in {c!r}"
        seg = _segments(c)
        if seg is None:
            return -0.4, f"not Korean syllables: {c!r}"
        syllables += len(seg)
    joined = "-".join(chunks)
    if "ai" in joined:
        # 'ai' is common in pinyin (Dakai, Kai) and rare in Korean romanization: no positive evidence
        return 0.05, f"ambiguous 'ai' spelling ({joined})"
    if syllables > 3:
        return -0.3, f"{syllables} syllables ({joined})"
    if any(KOREAN_MARKERS.search(c) for c in chunks):
        return 0.5, f"Korean spelling ({joined})"
    if syllables >= 2:
        return 0.35, f"Korean-compatible syllables ({joined})"
    return 0.05, f"Korean-compatible ({joined})"


def given_evidence(given: list[str]) -> tuple[float, str]:
    """Evidence from given-name tokens. Hyphen/camel/space boundaries are syllable boundaries.

    'Wook-Shin', 'WookShin', 'Wook Shin' are treated as one Korean given name; a Western first name next to a
    Korean one ('Ted Taekyoung', 'Steven Euijong') falls back to the Korean token.
    """
    if not given:
        return 0.0, "no given name"
    whole = _token_evidence("-".join(given))
    if whole[0] > 0 or len(given) == 1:
        return whole
    per = [(_token_evidence(t), t) for t in given]
    best = max(per, key=lambda x: x[0][0])
    if best[0][0] >= 0.35:
        return best[0][0] - 0.1, f"{best[0][1]} + western name"
    return whole


WESTERN_ONLY = "western given name"


def score(name: str) -> Verdict:
    surname, given = split_name(name)
    prior = SURNAMES.get(surname, 0.0)
    if prior == 0.0:
        return Verdict(0.0, surname, " ".join(given), "not a Korean surname")
    ev, why = given_evidence(given)
    if ev < 0 and prior >= 0.9 and all(t[:1].isupper() and t[1:].islower() and len(t) > 2 for t in given):
        # e.g. 'Steven Whang', 'Alice Kim': Korean diaspora is plausible — leave for co-author/LLM check
        return Verdict(round(prior * 0.5, 3), surname, " ".join(given), WESTERN_ONLY)
    # logistic-ish blend: strong given-name evidence can move a weak surname up or a strong one down
    s = max(0.0, min(1.0, prior + ev * (1 - prior if ev > 0 else prior)))
    return Verdict(round(s, 3), surname, " ".join(given), why)
