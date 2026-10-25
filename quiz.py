"""Quiz engine: N rounds, one obscure question each. First correct answer starts a grace
timer for the other player. Points reward speed, few clicks and few wrong guesses."""
import random
import re
import time
import unicodedata

from game import Engine, GameError

ROUNDS = 3
GRACE = 60            # seconds the other player gets after the first correct answer
ROUND_LIMIT = 300     # seconds before a round ends with nobody (else) solving it
INTERMISSION = 8      # seconds between rounds

BASE = 1000
TIME_PENALTY_PER_SEC, MAX_TIME_PENALTY = 2, 500
CLICK_PENALTY, MAX_CLICK_PENALTY = 10, 200
WRONG_PENALTY, MAX_WRONG_PENALTY = 50, 200

# Every question chains 2-4 facts, so no single search returns the answer; each answer was
# checked against Wikipedia. Add variants a player might type (units, spellings) to "answers".
QUESTIONS = [
    {"question": "What is the elevation, in metres, of the highest point in the country whose capital is the westernmost capital city of mainland Europe?",
     "answers": ["2351", "2351 m", "2351 metres"]},
    {"question": "How many Earth days does the largest moon of the planet with the shortest day in the Solar System take to orbit once? Round to the nearest whole number.",
     "answers": ["7", "7 days", "seven"]},
    {"question": "Take the number of years the Hundred Years' War is conventionally said to have lasted. What is the chemical symbol of the element with that atomic number?",
     "answers": ["Lv", "Livermorium"]},
    {"question": "What is the name of the longest river that flows entirely within the largest country in the world by area?",
     "answers": ["Lena", "Lena River"]},
    {"question": "What is the name of the highest mountain in the home country of the leader of the first expedition to reach the South Pole?",
     "answers": ["Galdhøpiggen", "Galdhopiggen", "Mount Galdhøpiggen"]},
    {"question": "In which Italian town was the composer of the opera containing the aria \"Nessun dorma\" born?",
     "answers": ["Lucca"]},
    {"question": "What is the height, in metres, of the highest mountain in the only country whose national flag is not rectangular or square? Give the most recent official survey figure, rounded to the nearest metre.",
     "answers": ["8849", "8848", "8848.86", "8849 m", "8848 m"]},
    {"question": "What is the atomic number of the element named after the birth country of the first person to win Nobel Prizes in two different scientific fields?",
     "answers": ["84"]},
    {"question": "How many Earth days does it take the first planet other than Earth to be orbited by a human-made spacecraft to circle the Sun once? Round to the nearest whole number.",
     "answers": ["687", "687 days"]},
]


_SPECIAL = str.maketrans({"ø": "o", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l"})


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text).lower().translate(_SPECIAL))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 2,351 == 2351
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


# Players may not search with any word from the question (function words are exempt).
STOPWORDS = frozenset("""a an the of in on at to for and or is are was were be been by with from as that this these those
it its what which who whom whose how do does did has have had not no than then there their his her they them he she you
your i we our so if but into out up over under about after before between per each every can will would should could
may might""".split())


def tokens(text):
    text = unicodedata.normalize("NFKD", str(text).lower().translate(_SPECIAL))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", text)


def stem(word):
    """Crude plural folding so 'rivers' can't dodge a ban on 'river'. Mirrored in static/play.js."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def banned_stems(question):
    return {stem(w) for w in tokens(question) if len(w) > 1 and w not in STOPWORDS}


def score(seconds, clicks, wrong):
    return (BASE
            - min(MAX_TIME_PENALTY, round(TIME_PENALTY_PER_SEC * seconds))
            - min(MAX_CLICK_PENALTY, CLICK_PENALTY * clicks)
            - min(MAX_WRONG_PENALTY, WRONG_PENALTY * wrong))


class QuizEngine(Engine):
    def __init__(self):
        self.clock = time.time
        self.finished = False

    # -- lifecycle ---------------------------------------------------------
    def start(self, players):
        self.pids = [p["id"] for p in players]
        self.rounds = random.sample(QUESTIONS, min(ROUNDS, len(QUESTIONS)))
        self.totals = {pid: 0 for pid in self.pids}
        self.history = []
        self.index = -1
        self._begin_round(self.clock())

    def _begin_round(self, now):
        self.index += 1
        self.phase = "round"
        self.round_started = now
        self.deadline = None
        self.next_at = None
        self.banned = banned_stems(self.rounds[self.index]["question"])
        self.p = {pid: {"status": "searching", "time": None, "points": 0, "clicks": 0, "searches": 0, "wrong": 0}
                  for pid in self.pids}

    def _end_round(self, now):
        for p in self.p.values():
            if p["status"] == "searching":
                p["status"] = "timeout"
        q = self.rounds[self.index]
        for pid, p in self.p.items():
            self.totals[pid] += p["points"]
        self.history.append({"round": self.index + 1, "question": q["question"], "answer": q["answers"][0],
                             "players": {pid: {k: p[k] for k in ("status", "time", "points")} for pid, p in self.p.items()}})
        if self.index + 1 >= len(self.rounds):
            self.phase, self.finished = "finished", True
        else:
            self.phase, self.next_at = "intermission", now + INTERMISSION

    def tick(self):
        """Advance timers lazily; called before every read/write."""
        if self.finished or not hasattr(self, "phase"):
            return
        now = self.clock()
        if self.phase == "round" and ((self.deadline and now >= self.deadline) or now - self.round_started >= ROUND_LIMIT):
            self._end_round(now)
        if self.phase == "intermission" and now >= self.next_at:
            self._begin_round(now)

    # -- player input ------------------------------------------------------
    def on_event(self, player_id, event):
        if self.phase != "round" or self.p[player_id]["status"] != "searching":
            return
        key = {"search": "searches", "click": "clicks"}.get(event["type"])
        if key:
            self.p[player_id][key] += 1

    def apply(self, player_id, action):
        if not isinstance(action, dict) or action.get("type") != "answer":
            raise GameError("Unknown action")
        me = self.p[player_id] if self.phase == "round" else None
        if me is None:
            raise GameError("No round in progress", 409)
        if me["status"] != "searching":
            raise GameError("You already answered this round", 409)
        guess = normalize(action.get("text", ""))
        if not guess:
            raise GameError("Type an answer first")
        if guess not in {normalize(a) for a in self.rounds[self.index]["answers"]}:
            me["wrong"] += 1
            return {"correct": False}
        now = self.clock()
        me["status"], me["time"] = "solved", round(now - self.round_started, 2)
        me["points"] = score(me["time"], me["clicks"], me["wrong"])
        if all(p["status"] != "searching" for p in self.p.values()):
            self._end_round(now)
        elif self.deadline is None:
            self.deadline = now + GRACE
        return {"correct": True, "points": me["points"]}

    def blocked_words(self, query):
        """Words in `query` that the current question forbids (empty = fine to search)."""
        if getattr(self, "phase", None) != "round":
            return []
        return sorted({w for w in tokens(query) if stem(w) in self.banned})

    # -- public state ------------------------------------------------------
    def state(self):
        s = {
            "phase": self.phase,
            "round": self.index + 1,
            "of": len(self.rounds),
            "question": self.rounds[self.index]["question"],
            "round_started": self.round_started,
            "deadline": self.deadline,
            "next_at": self.next_at,
            "players": self.p,
            "totals": self.totals,
            "history": self.history,
            "banned": sorted(self.banned) if self.phase == "round" else [],
            "winner": None,
        }
        if self.finished:
            top = max(self.totals.values())
            leaders = [pid for pid, t in self.totals.items() if t == top]
            s["winner"] = leaders[0] if len(leaders) == 1 else "draw"
        return s
