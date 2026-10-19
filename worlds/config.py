"""Paths, league metadata and config loading."""
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUTS_DIR = ROOT / "outputs"
DB_PATH = PROCESSED_DIR / "worlds.db"
CONFIG_PATH = ROOT / "config" / "worlds2026.yaml"

LEAGUE_NAMES = {
    "WLDs": "Worlds",
    "MSI": "Mid-Season Invitational",
    "FST": "First Stand",
    "EWC": "Esports World Cup",
    "DCGI": "Demacia Cup Global Invitational",
    "DCup": "Demacia Cup",
    "ASI": "Asia Invitational",
    "AC": "Americas Cup",
    "WSCI": "Academy World Showcase",
    "WLRQ": "Worlds LPL Regional Qualifier",
    "KeSPA Cup": "KeSPA Cup",
    "LTA": "LTA Championship",
}

# Leagues ignored when deciding a team's home league: events, cups and cross-region finals.
EVENT_LEAGUES = {"WLDs", "MSI", "FST", "EWC", "DCGI", "DCup", "ASI", "AC", "WSCI",
                 "KeSPA Cup", "LTA"}

# Domestic league -> region. 2025's LTA conferences became LCS / CBLOL in 2026.
REGION_OF_LEAGUE = {"LTA N": "LCS", "LTA S": "CBLOL"}


def region_of(league: str) -> str:
    return REGION_OF_LEAGUE.get(league, league)


@lru_cache
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    cfg["worlds_start"] = {int(y): pd.Timestamp(d) for y, d in cfg["worlds_start"].items()}
    return cfg
