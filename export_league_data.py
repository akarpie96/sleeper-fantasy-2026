#!/usr/bin/env python3
"""
Export full Sleeper league roster data as readable text, for pasting into
a chat with an LLM to get qualitative trade/roster analysis (rather than
relying on a numeric proxy like search_rank).

Usage:
    python export_league_data.py --league-id YOUR_LEAGUE_ID
"""

import argparse
import sys

import requests

BASE_URL = "https://api.sleeper.app/v1"


def get_league(league_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/league/{league_id}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_rosters(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/rosters", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_users_in_league(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/users", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_all_players() -> dict:
    resp = requests.get(f"{BASE_URL}/players/nfl", timeout=30)
    resp.raise_for_status()
    return resp.json()


def player_label(players: dict, player_id: str) -> str:
    p = players.get(player_id)
    if not p:
        return player_id
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    pos = p.get("position", "?")
    team = p.get("team") or "FA"
    status = f", {p.get('injury_status')}" if p.get("injury_status") else ""
    return f"{name} ({pos}, {team}{status})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", required=True, help="A Sleeper league ID")
    args = parser.parse_args()

    try:
        league = get_league(args.league_id)
        rosters = get_rosters(args.league_id)
        users = get_users_in_league(args.league_id)
        players = get_all_players()

        names_by_id = {
            u["user_id"]: ((u.get("metadata") or {}).get("team_name") or u.get("display_name", "?")).strip()
            for u in users
        }

        print(f"League: {league.get('name')} ({league.get('season')})")
        print(f"Scoring settings: {league.get('scoring_settings')}")
        print(f"Roster positions (starters + bench): {league.get('roster_positions')}")
        print()

        for roster in sorted(rosters, key=lambda r: r["roster_id"]):
            team_name = names_by_id.get(roster.get("owner_id"), "Unknown")
            starters = set(roster.get("starters") or [])
            all_players = roster.get("players") or []
            bench = [pid for pid in all_players if pid not in starters]

            print(f"=== Roster {roster['roster_id']}: {team_name} ===")
            print("Starters:")
            for pid in roster.get("starters") or []:
                print(f"  - {player_label(players, pid)}")
            print("Bench:")
            for pid in bench:
                print(f"  - {player_label(players, pid)}")
            print()

        return 0

    except requests.exceptions.RequestException as e:
        print(f"\nNetwork/API error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
