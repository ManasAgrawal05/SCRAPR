"""Shopping mode: find a listing for an item that is highly rated, cheap, and found fast.

Players search Google Shopping (SerpAPI), select one listing and submit it. A listing counts if its
metadata (title, description, store name, tags) mentions every required term; points then reward
rating, price (relative to a per-task price range) and speed. Everything is scored from the server's own copy of
the listing, never from numbers sent by the browser."""
import hashlib
import random

from game import GameError
from quiz import QuizEngine, ROUNDS

# ---- scoring (adds up to 1000) ----------------------------------------------
BASE = 200                 # any listing that matches the task
RATING_PTS = 300           # 3.0 stars -> 0, 5.0 stars -> all of it; no rating -> 0
COST_PTS = 350             # cheapest end of the task's price range -> all of it, dearest end -> 0
SPEED_PTS = 150            # all of it at 0s, none after SPEED_WINDOW seconds
SPEED_WINDOW = 180
WRONG_PENALTY, MAX_WRONG_PENALTY = 40, 160
RATING_FLOOR = 3.0

# "must" terms are matched anywhere in a listing's metadata (case-insensitive substrings, so "bag" also finds
# "handbag"). A term can be a list of alternatives (any one will do). "min" is the lowest believable price: anything
# cheaper is an accessory or a fake and doesn't count. lo/hi is the price range (USD) cost points are measured against.
# Only tasks checked against real Google Shopping results are listed; probe a query before adding one.
TASKS = [
    {"question": "Find a Stanley tumbler: pick a highly rated, cheap listing and submit it.", "label": "Stanley tumbler",
     "must": ["stanley", "tumbler"], "min": 10, "lo": 20, "hi": 70},
    {"question": "Find Sony headphones: pick a highly rated, cheap listing and submit it.", "label": "Sony headphones",
     "must": ["sony", "headphone"], "min": 25, "lo": 50, "hi": 450},
    {"question": "Find a Dior handbag: pick a highly rated, cheap listing and submit it.", "label": "Dior handbag",
     "must": ["dior", ["handbag", "bag", "tote", "purse"]], "min": 400, "lo": 1000, "hi": 6500},
]

# ---- listings the server has shown, by id (so a submit can't claim made-up numbers) ----------
ITEMS = {}
MAX_ITEMS = 5000


def parse(results):
    """SerpAPI `shopping_results` -> clean listings. Listings without a price can't be scored, so they're dropped."""
    items = []
    for r in results or []:
        price = r.get("extracted_price")
        if not isinstance(price, (int, float)) or not r.get("title"):
            continue
        extra = " ".join(str(x) for x in (r.get("extensions") or []))
        item = {
            "title": r["title"], "source": r.get("source") or "", "snippet": r.get("snippet") or "",
            "price": float(price), "price_text": r.get("price") or f"${price:,.2f}",
            "rating": float(r["rating"]) if isinstance(r.get("rating"), (int, float)) else None,
            "reviews": r.get("reviews") if isinstance(r.get("reviews"), int) else None,
            "delivery": r.get("delivery") or "", "thumbnail": r.get("thumbnail") or "",
            "tag": r.get("tag") or "",
        }
        # Google's product_link embeds the searcher's own query text, so it is deliberately NOT part of the check.
        item["haystack"] = " ".join([item["title"], item["snippet"], item["source"], item["tag"], str(r.get("badge") or ""), extra]).lower()
        item["id"] = hashlib.sha1(f'{item["title"]}|{item["source"]}|{item["price"]}'.encode()).hexdigest()[:12]
        items.append(item)
    return items


def remember(items):
    if len(ITEMS) > MAX_ITEMS:
        ITEMS.clear()
    for it in items:
        ITEMS[it["id"]] = it
    return items


def _has(item, term):
    return any(alt.lower() in item["haystack"] for alt in ([term] if isinstance(term, str) else term))


def check(item, task):
    """(ok, reason) -- does this listing count for the task?"""
    if not all(_has(item, t) for t in task["must"]):
        need = " + ".join(t if isinstance(t, str) else t[0] for t in task["must"])
        return False, f"That listing doesn't match the task (it needs to be: {need})."
    if item["price"] < task.get("min", 0):
        return False, "That price is too low to be the real thing. Pick a genuine listing."
    return True, ""


def _clamp(x):
    return max(0.0, min(1.0, x))


def breakdown(item, task, seconds, wrong):
    rating = RATING_PTS * _clamp((item["rating"] - RATING_FLOOR) / (5.0 - RATING_FLOOR)) if item["rating"] else 0
    cost = COST_PTS * (1 - _clamp((item["price"] - task["lo"]) / (task["hi"] - task["lo"])))
    speed = SPEED_PTS * _clamp(1 - seconds / SPEED_WINDOW)
    penalty = min(MAX_WRONG_PENALTY, WRONG_PENALTY * wrong)
    parts = {"base": BASE, "rating": round(rating), "cost": round(cost), "speed": round(speed), "wrong": -penalty}
    parts["total"] = sum(parts.values())
    return parts


def summary(item):
    return {k: item[k] for k in ("id", "title", "source", "price", "price_text", "rating", "reviews")}


class ShoppingEngine(QuizEngine):
    kind = "shopping"

    def start(self, players, settings=None):
        self.block_words = False  # no search restrictions in this mode
        self.pids = [p["id"] for p in players]
        self.rounds = random.sample(TASKS, min(ROUNDS, len(TASKS)))
        self.totals = {pid: 0 for pid in self.pids}
        self.history = []
        self.index = -1
        self._begin_round(self.clock())

    def _begin_round(self, now):
        super()._begin_round(now)
        for p in self.p.values():
            p["pick"], p["breakdown"] = None, None

    def apply(self, player_id, action):
        if not isinstance(action, dict) or action.get("type") != "select":
            raise GameError("Unknown action")
        if self.phase != "round":
            raise GameError("No round in progress", 409)
        me = self.p[player_id]
        if me["status"] != "searching":
            raise GameError("You already submitted this round", 409)
        item = ITEMS.get(str(action.get("item_id", "")))
        if not item:
            raise GameError("That listing has expired. Search again and re-select it.", 410)
        task = self.rounds[self.index]
        good, reason = check(item, task)
        if not good:
            me["wrong"] += 1
            return {"correct": False, "message": reason}
        seconds = round(self.clock() - self.round_started, 2)
        parts = breakdown(item, task, seconds, me["wrong"])
        me["pick"], me["breakdown"] = summary(item), parts
        self._mark_solved(me, parts["total"])
        return {"correct": True, "points": parts["total"], "breakdown": parts}

    # what the round-end screen shows
    def reveal(self, q):
        return q["label"]

    def reveal_text(self, q):
        return f"Looking for: {q['label']}"

    def player_result(self, p):
        return {**super().player_result(p), "pick": p.get("pick"), "breakdown": p.get("breakdown")}
