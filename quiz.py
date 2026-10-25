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

# "keywords" are the words that carry the puzzle; they are the only words players are barred
# from searching (when the host leaves that rule on). Add variants a player might type to "answers".
DIFFICULTIES = ("easy", "difficult")

QUESTIONS = {
    # One fact, one lookup: fine for a relaxed game.
    "easy": [
        {"question": "What is the capital city of the Pacific island nation of Palau?",
         "keywords": ["capital", "pacific", "island", "palau"], "answers": ["Ngerulmud"]},
        {"question": "Which NASA spacecraft became the first to orbit another planet, in 1971?",
         "keywords": ["nasa", "spacecraft", "first", "orbit", "planet", "1971"], "answers": ["Mariner 9"]},
        {"question": "Which single letter of the alphabet does not appear in the name of any US state?",
         "keywords": ["letter", "alphabet", "state"], "answers": ["Q", "letter Q"]},
        {"question": "What is the smallest country by area on mainland Africa?",
         "keywords": ["smallest", "country", "area", "mainland", "africa"], "answers": ["The Gambia", "Gambia"]},
        {"question": "What is the national animal of Scotland?",
         "keywords": ["national", "animal", "scotland"], "answers": ["Unicorn"]},
        {"question": "In what year did the Tunguska event flatten forest in Siberia?",
         "keywords": ["tunguska", "flatten", "forest", "siberia"], "answers": ["1908"]},
        {"question": "What is the Japanese art of repairing broken pottery with gold lacquer called?",
         "keywords": ["japanese", "art", "repairing", "broken", "pottery", "gold", "lacquer"], "answers": ["Kintsugi", "Kintsukuroi"]},
        {"question": "What is the longest river in Europe?",
         "keywords": ["longest", "river", "europe"], "answers": ["Volga"]},
    ],
    # Every difficult question chains 2-4 facts, so no single search returns the answer; each
    # answer was checked against Wikipedia.
    "difficult": [
        {"question": "What is the elevation, in metres, of the highest point in the country whose capital is the westernmost capital city of mainland Europe?",
         "keywords": ["elevation", "highest", "capital", "westernmost", "mainland", "europe"],
         "answers": ["2351", "2351 m", "2351 metres"]},
        {"question": "How many Earth days does the largest moon of the planet with the shortest day in the Solar System take to orbit once? Round to the nearest whole number.",
         "keywords": ["earth", "day", "largest", "moon", "planet", "shortest", "solar", "system", "orbit"],
         "answers": ["7", "7 days", "seven"]},
        {"question": "Take the number of years the Hundred Years' War is conventionally said to have lasted. What is the chemical symbol of the element with that atomic number?",
         "keywords": ["hundred", "years", "war", "lasted", "chemical", "symbol", "element", "atomic"],
         "answers": ["Lv", "Livermorium"]},
        {"question": "What is the name of the longest river that flows entirely within the largest country in the world by area?",
         "keywords": ["longest", "river", "largest", "country", "world", "area"],
         "answers": ["Lena", "Lena River"]},
        {"question": "What is the name of the highest mountain in the home country of the leader of the first expedition to reach the South Pole?",
         "keywords": ["highest", "mountain", "leader", "first", "expedition", "south", "pole"],
         "answers": ["Galdhøpiggen", "Galdhopiggen", "Mount Galdhøpiggen"]},
        {"question": "In which Italian town was the composer of the opera containing the aria \"Nessun dorma\" born?",
         "keywords": ["italian", "town", "composer", "opera", "aria", "nessun", "dorma", "born"],
         "answers": ["Lucca"]},
        {"question": "What is the height, in metres, of the highest mountain in the only country whose national flag is not rectangular or square? Give the most recent official survey figure, rounded to the nearest metre.",
         "keywords": ["height", "highest", "mountain", "national", "flag", "rectangular", "square", "survey"],
         "answers": ["8849", "8848", "8848.86", "8849 m", "8848 m"]},
        {"question": "What is the atomic number of the element named after the birth country of the first person to win Nobel Prizes in two different scientific fields?",
         "keywords": ["atomic", "element", "birth", "country", "nobel", "prizes", "scientific", "fields"],
         "answers": ["84"]},
        {"question": "How many Earth days does it take the first planet other than Earth to be orbited by a human-made spacecraft to circle the Sun once? Round to the nearest whole number.",
         "keywords": ["earth", "days", "first", "planet", "orbited", "human", "spacecraft", "sun"],
         "answers": ["687", "687 days"]},
    ],
}
ALL_QUESTIONS = [q for pool in QUESTIONS.values() for q in pool]
DEFAULT_SETTINGS = {"difficulty": "difficult", "block_words": True}


_SPECIAL = str.maketrans({"ø": "o", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l"})


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text).lower().translate(_SPECIAL))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 2,351 == 2351
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


# Players may not search with the question's key words. Function words are never keywords.
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


# Searches may use at most MAX_KEYWORDS of a question's key words, and never two of them side by side in the
# question's own order. A keyword and its variants ("scotland" / "scottish") count as ONE keyword.
MAX_KEYWORDS = 2
VARIANTS = {
    "scotland": ["scottish", "scots", "scot"], "palau": ["palauan"], "orbit": ["orbited", "orbiting", "orbital"],
    "flatten": ["flattened", "flattening"], "siberia": ["siberian"], "japanese": ["japan"],
    "repairing": ["repair", "repaired"], "africa": ["african"], "europe": ["european"], "italian": ["italy"],
    "alphabet": ["alphabetical"], "lasted": ["last", "lasting"], "born": ["birth", "birthplace"],
    "birth": ["born", "birthplace"], "composer": ["compose", "composed", "composition"],
    "westernmost": ["western", "west"], "largest": ["large", "biggest"], "smallest": ["small"],
    "orbited": ["orbit", "orbiting", "orbital"], "solar": ["sun"], "sun": ["solar"], "rectangular": ["rectangle"],
    "survey": ["surveyed"], "scientific": ["science", "scientist"], "leader": ["led", "leading"],
    "pole": ["polar"], "spacecraft": ["spaceship"], "elevation": ["altitude"], "highest": ["tallest"],
    "height": ["tall"], "mountain": ["mount", "mountainous"], "hundred": ["100"], "earth": ["terrestrial"],
    "prizes": ["prize"], "fields": ["field"], "days": ["day"], "years": ["year"],
}


def keyword_concepts(keywords):
    """{word stem -> concept id} for the question's key words and their variants."""
    concept = {}
    for kw in keywords:
        cid = stem(kw)
        for word in [kw, *VARIANTS.get(kw, [])]:
            for tok in tokens(word):
                if len(tok) > 1 and tok not in STOPWORDS:
                    concept[stem(tok)] = cid
    return concept


def content_sequence(text, concept):
    """Text as a list of concept ids / stems, with function words dropped (so 'animal of scotland' is a chain)."""
    return [concept.get(stem(w), stem(w)) for w in tokens(text) if len(w) > 1 and w not in STOPWORDS]


def score(seconds, clicks, wrong):
    return (BASE
            - min(MAX_TIME_PENALTY, round(TIME_PENALTY_PER_SEC * seconds))
            - min(MAX_CLICK_PENALTY, CLICK_PENALTY * clicks)
            - min(MAX_WRONG_PENALTY, WRONG_PENALTY * wrong))


class QuizEngine(Engine):
    kind = "quiz"

    def __init__(self):
        self.clock = time.time
        self.finished = False

    # -- lifecycle ---------------------------------------------------------
    def start(self, players, settings=None):
        settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.block_words = bool(settings["block_words"])
        pool = QUESTIONS[settings["difficulty"]]
        self.pids = [p["id"] for p in players]
        self.rounds = random.sample(pool, min(ROUNDS, len(pool)))
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
        q = self.rounds[self.index]
        self.concept = keyword_concepts(q["keywords"]) if self.block_words else {}
        cids = set(self.concept.values())
        seq = content_sequence(q["question"], self.concept)
        self.chains = {(a, b) for a, b in zip(seq, seq[1:]) if a in cids and b in cids and a != b}
        self.p = {pid: {"status": "searching", "time": None, "points": 0, "clicks": 0, "searches": 0, "wrong": 0}
                  for pid in self.pids}

    def _end_round(self, now):
        for p in self.p.values():
            if p["status"] == "searching":
                p["status"] = "timeout"
        q = self.rounds[self.index]
        for pid, p in self.p.items():
            self.totals[pid] += p["points"]
        self.history.append({"round": self.index + 1, "question": q["question"], "answer": self.reveal(q),
                             "reveal": self.reveal_text(q),
                             "players": {pid: self.player_result(p) for pid, p in self.p.items()}})
        if self.index + 1 >= len(self.rounds):
            self.phase, self.finished = "finished", True
        else:
            self.phase, self.next_at = "intermission", now + INTERMISSION

    # hooks other round-based engines (shopping.py) override
    def reveal(self, q):
        return q["answers"][0]

    def reveal_text(self, q):
        return "Answer: " + self.reveal(q)

    def player_result(self, p):
        return {k: p[k] for k in ("status", "time", "points")}

    def _mark_solved(self, me, points):
        """Record a correct answer, end the round if everyone is done, else start the others' grace timer."""
        now = self.clock()
        me["status"], me["time"], me["points"] = "solved", round(now - self.round_started, 2), points
        if all(p["status"] != "searching" for p in self.p.values()):
            self._end_round(now)
        elif self.deadline is None:
            self.deadline = now + GRACE

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
        self._mark_solved(me, score(round(self.clock() - self.round_started, 2), me["clicks"], me["wrong"]))
        return {"correct": True, "points": me["points"]}

    def blocked_info(self, query):
        """(offending words, message) if `query` breaks the key-word rule, else ([], "")."""
        if getattr(self, "phase", None) != "round" or not self.concept:
            return [], ""
        used = {}  # concept id -> the word the player typed
        for w in tokens(query):
            cid = self.concept.get(stem(w))
            if cid and cid not in used:
                used[cid] = w
        if len(used) > MAX_KEYWORDS:
            words = list(used.values())
            return words, f"Too many of the question's key words (the limit is {MAX_KEYWORDS}): {', '.join(words)}."
        seq = content_sequence(query, self.concept)
        for a, b in zip(seq, seq[1:]):
            if (a, b) in self.chains:
                words = [used[a], used[b]]
                return words, f"Those key words appear side by side in the question: \u201c{words[0]} {words[1]}\u201d."
        return [], ""

    def blocked_words(self, query):
        return self.blocked_info(query)[0]

    # -- public state ------------------------------------------------------
    def state(self):
        s = {
            "kind": self.kind,
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
