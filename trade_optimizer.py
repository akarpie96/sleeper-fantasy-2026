#!/usr/bin/env python3
"""
Suggest realistic trades for your Sleeper fantasy roster.

For each team in the league, this computes which positions they have surplus
depth at (more players than their starting lineup requires) versus which
positions they're short on (accounting for FLEX slots). It then looks for
complementary trades: places where another team has surplus at a position
you need, and you have surplus at a position they need.

Player "value" uses Sleeper's own search_rank field (their internal overall
ranking, lower = better) since Sleeper's public API doesn't expose real
ADP/expert rankings or projections. Treat suggestions as a starting point
for negotiation, not gospel.

Usage:
    python trade_optimizer.py --league-id YOUR_LEAGUE_ID --my-username YOUR_SLEEPER_USERNAME
"""

import argparse
import sys
from collections import Counter

import requests

BASE_URL = "https://api.sleeper.app/v1"
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def get_user(username: str) -> dict:
    resp = requests.get(f"{BASE_URL}/user/{username}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data is None:
        raise ValueError(f"No Sleeper user found for username '{username}'")
    return data


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


def rank_of(player: dict) -> int:
    return player.get("search_rank") if player.get("search_rank") is not None else 999999


def players_by_position(player_ids: list, players: dict) -> dict:
    by_pos = {}
    for pid in player_ids:
        p = players.get(pid)
        if not p or p.get("position") not in FANTASY_POSITIONS:
            continue
        by_pos.setdefault(p["position"], []).append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=rank_of)
    return by_pos


def compute_position_balance(roster_positions: list, by_pos: dict) -> tuple:
    exact_required = Counter()
    flex_slots = 0
    superflex = False
    for slot in roster_positions:
        if slot in ("BN", "IR", "TAXI"):
            continue
        if "FLEX" in slot:
            flex_slots += 1
            if "SUPER" in slot:
                superflex = True
        else:
            exact_required[slot] += 1

    need = {}
    surplus = {}
    for pos in FANTASY_POSITIONS:
        owned = len(by_pos.get(pos, []))
        req = exact_required.get(pos, 0)
        if owned < req:
            need[pos] = req - owned
        elif owned > req:
            surplus[pos] = owned - req

    flex_pool = {"RB", "WR", "TE"} | ({"QB"} if superflex else set())
    flex_remaining = flex_slots
    for pos in sorted(flex_pool, key=lambda p: -surplus.get(p, 0)):
        if flex_remaining <= 0:
            break
        used = min(surplus.get(pos, 0), flex_remaining)
        if used:
            surplus[pos] -= used
            if surplus[pos] == 0:
                del surplus[pos]
            flex_remaining -= used
    if flex_remaining > 0:
        need["FLEX"] = flex_remaining

    return need, surplus


def spare_players(by_pos: dict, pos: str, count: int) -> list:
    if pos == "FLEX":
        return []
    return by_pos.get(pos, [])[-count:] if count > 0 else []


def suggest_trades(my_by_pos, my_need, my_surplus, their_by_pos, their_need, their_surplus) -> list:
    suggestions = []
    for want_pos, want_amount in my_need.items():
        if want_pos not in their_surplus:
            continue
        get_candidates = spare_players(their_by_pos, want_pos, their_surplus[want_pos])
        if not get_candidates:
            continue

        for give_pos, give_amount in their_need.items():
            if give_pos not in my_surplus:
                continue
            give_candidates = spare_players(my_by_pos, give_pos, my_surplus[give_pos])
            if not give_candidates:
                continue

            get_player = get_candidates[-1]
            give_player = give_candidates[-1]
            suggestions.append((give_player, get_player))

    return suggestions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", required=True, help="A Sleeper league ID")
    parser.add_argument("--my-username", required=True, help="Your Sleeper username")
    args = parser.parse_args()

    try:
        league = get_league(args.league_id)
        rosters = get_rosters(args.league_id)
        users = get_users_in_league(args.league_id)
        players = get_all_players()

        my_user = get_user(args.my_username)
        my_roster = next(
            (
                r for r in rosters
                if r.get("owner_id") == my_user["user_id"]
                or my_user["user_id"] in (r.get("co_owners") or [])
            ),
            None,
        )
        if my_roster is None:
            print(f"Could not find a roster owned by '{args.my_username}' in this league.")
            return 1

        names_by_id = {
            u["user_id"]: ((u.get("metadata") or {}).get("team_name") or u.get("display_name", "?")).strip()
            for u in users
        }

        roster_positions = league.get("roster_positions", [])
        my_by_pos = players_by_position(my_roster.get("players") or [], players)
        my_need, my_surplus = compute_position_balance(roster_positions, my_by_pos)

        print(f"Your roster needs: {my_need or 'none'}")
        print(f"Your roster surplus: {my_surplus or 'none'}\n")

        found_any = False
        for roster in rosters:
            if roster["roster_id"] == my_roster["roster_id"]:
                continue

            their_by_pos = players_by_position(roster.get("players") or [], players)
            their_need, their_surplus = compute_position_balance(roster_positions, their_by_pos)

            trades = suggest_trades(my_by_pos, my_need, my_surplus, their_by_pos, their_need, their_surplus)
            if not trades:
                continue

            found_any = True
            team_name = names_by_id.get(roster.get("owner_id"), "Unknown")
            print(f"--- Possible trade with {team_name} ---")
            for give_player, get_player in trades:
                print(
                    f"  You give: {give_player.get('full_name')} ({give_player.get('position')}, "
                    f"rank={give_player.get('search_rank')})"
                )
                print(
                    f"  You get:  {get_player.get('full_name')} ({get_player.get('position')}, "
                    f"rank={get_player.get('search_rank')})"
                )
                rank_gap = abs(rank_of(give_player) - rank_of(get_player))
                if rank_gap > 50:
                    print(f"  Note: large rank gap ({rank_gap}) - may be lopsided, use as a starting offer")
                print()

        if not found_any:
            print("No complementary trade opportunities found against any team right now.")

        return 0

    except requests.exceptions.RequestException as e:
        print(f"\nNetwork/API error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
