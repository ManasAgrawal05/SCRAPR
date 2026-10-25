"""Game modes, shown as tabs on the host/join screen. To add one: write an Engine subclass
(see quiz.py), then set it as "engine" below. Modes without an engine show up as "coming soon"."""
from quiz import QuizEngine
from shopping import ShoppingEngine

MODES = [
    {"id": "all", "label": "All", "engine": QuizEngine,
     "blurb": "Race other players to answer obscure questions using nothing but search."},
    {"id": "shopping", "label": "Shopping", "engine": ShoppingEngine,
     "blurb": "Hunt down the best listing for an item: highly rated, cheap, and found fast."},
    {"id": "images", "label": "Images", "engine": None, "blurb": "Coming soon."},
    {"id": "videos", "label": "Videos", "engine": None, "blurb": "Coming soon."},
]
MODE_IDS = [m["id"] for m in MODES]
DEFAULT_MODE = "all"
