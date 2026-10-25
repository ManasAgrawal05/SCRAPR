"""Quiz engine: N rounds, one obscure question each. First correct answer starts a grace
timer for the other player. Points reward speed, few clicks and few wrong guesses."""
import random
import re
import time

from game import Engine, GameError

ROUNDS = 3
GRACE = 60            # seconds the other player gets after the first correct answer
ROUND_LIMIT = 300     # seconds before a round ends with nobody (else) solving it
INTERMISSION = 8      # seconds between rounds

BASE = 1000
TIME_PENALTY_PER_SEC, MAX_TIME_PENALTY = 2, 500
CLICK_PENALTY, MAX_CLICK_PENALTY = 10, 200
WRONG_PENALTY, MAX_WRONG_PENALTY = 50, 200

QUESTIONS = [
    {"question": "What is the capital city of the Pacific island nation of Palau?", "answers": ["Ngerulmud"]},
    {"question": "Which NASA spacecraft became the first to orbit another planet, in 1971?", "answers": ["Mariner 9"]},
    {"question": "Which single letter of the alphabet does not appear in the name of any US state?", "answers": ["Q", "letter Q"]},
    {"question": "What is the smallest country by area on mainland Africa?", "answers": ["The Gambia", "Gambia"]},
    {"question": "What is the national animal of Scotland?", "answers": ["Unicorn"]},
    {"question": "In what year did the Tunguska event flatten forest in Siberia?", "answers": ["1908"]},
    {"question": "What is the Japanese art of repairing broken pottery with gold lacquer called?", "answers": ["Kintsugi", "Kintsukuroi"]},
    {"question": "What is the longest river in Europe?", "answers": ["Volga"]},
]


def normalize(text):
    text = re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()
    return re.sub(r"^(the|a|an) ", "", text)


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
            "winner": None,
        }
        if self.finished:
            top = max(self.totals.values())
            leaders = [pid for pid, t in self.totals.items() if t == top]
            s["winner"] = leaders[0] if len(leaders) == 1 else "draw"
        return s
