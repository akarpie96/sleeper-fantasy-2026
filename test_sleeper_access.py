#!/usr/bin/env python3
"""
Test access to a Sleeper fantasy football league via the public Sleeper API.

Sleeper's API is read-only and requires no API key or authentication -
you just need your username or league ID.

Usage:
    python test_sleeper_access.py --username YOUR_SLEEPER_USERNAME [--season 2026]
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID

    # Optional extras, combine with either of the above:
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID --matchups 1
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID --transactions 1
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID --draft
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID --trending add
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID --watch-draft
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID --watch-draft --my-username YOUR_SLEEPER_USERNAME

Find your league ID in the Sleeper app/website URL, e.g.:
    https://sleeper.com/leagues/1234567890123456789/team
                                 ^^^^^^^^^^^^^^^^^^^ this is the league_id
"""

import argparse
import os
import sys
import time
from collections import Counter

import requests

WATCH_POLL_SECONDS = 5

BASE_URL = "https://api.sleeper.app/v1"


def get_user(username: str) -> dict:
    resp = requests.get(f"{BASE_URL}/user/{username}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data is None:
        raise ValueError(f"No Sleeper user found for username '{username}'")
    return data


def get_leagues_for_user(user_id: str, season: str) -> list:
    resp = requests.get(
        f"{BASE_URL}/user/{user_id}/leagues/nfl/{season}", timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def get_league(league_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/league/{league_id}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data is None:
        raise ValueError(f"No league found for league_id '{league_id}'")
    return data


def get_rosters(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/rosters", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_users_in_league(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/users", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_matchups(league_id: str, week: int) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/matchups/{week}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_transactions(league_id: str, round_: int) -> list:
    resp = requests.get(
        f"{BASE_URL}/league/{league_id}/transactions/{round_}", timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def get_drafts_for_league(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/drafts", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_draft(draft_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/draft/{draft_id}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_draft_picks(draft_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/draft/{draft_id}/picks", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_trending_players(direction: str, lookback_hours: int = 24, limit: int = 25) -> list:
    resp = requests.get(
        f"{BASE_URL}/players/nfl/trending/{direction}",
        params={"lookback_hours": lookback_hours, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_all_players() -> dict:
    resp = requests.get(f"{BASE_URL}/players/nfl", timeout=30)
    resp.raise_for_status()
    return resp.json()


def player_name(players: dict, player_id: str) -> str:
    p = players.get(player_id)
    if not p:
        return player_id
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


def team_display_names(users: list) -> dict:
    return {
        u["user_id"]: (u.get("metadata") or {}).get("team_name") or u.get("display_name", "?")
        for u in users
    }


def print_league_summary(league: dict, rosters: list, users: list) -> None:
    print(f"\nLeague name:   {league.get('name')}")
    print(f"League ID:     {league.get('league_id')}")
    print(f"Season:        {league.get('season')}")
    print(f"Status:        {league.get('status')}")
    print(f"Total rosters: {league.get('total_rosters')}")

    display_names = team_display_names(users)
    print("\nTeams:")
    for roster in rosters:
        owner = display_names.get(roster.get("owner_id"), "Unknown")
        wins = roster.get("settings", {}).get("wins", 0)
        losses = roster.get("settings", {}).get("losses", 0)
        print(f"  - Roster {roster.get('roster_id')}: {owner} ({wins}-{losses})")


def print_matchups(matchups: list, rosters: list, users: list, week: int) -> None:
    owner_by_roster = {r["roster_id"]: r.get("owner_id") for r in rosters}
    display_names = team_display_names(users)

    print(f"\nMatchups - Week {week}:")
    by_matchup_id = {}
    for m in matchups:
        by_matchup_id.setdefault(m.get("matchup_id"), []).append(m)

    for matchup_id, teams in sorted(by_matchup_id.items(), key=lambda kv: (kv[0] is None, kv[0])):
        parts = []
        for t in teams:
            owner = display_names.get(owner_by_roster.get(t["roster_id"]), "Unknown")
            parts.append(f"{owner} ({t.get('points', 0)} pts)")
        print(f"  - {' vs '.join(parts)}")


def print_transactions(transactions: list, rosters: list, users: list, players: dict) -> None:
    owner_by_roster = {r["roster_id"]: r.get("owner_id") for r in rosters}
    display_names = team_display_names(users)

    print(f"\nTransactions ({len(transactions)}):")
    for t in transactions:
        roster_ids = t.get("roster_ids") or []
        owners = [display_names.get(owner_by_roster.get(rid), "Unknown") for rid in roster_ids]
        adds = t.get("adds") or {}
        drops = t.get("drops") or {}
        add_names = [player_name(players, pid) for pid in adds]
        drop_names = [player_name(players, pid) for pid in drops]
        summary = f"  - [{t.get('type')}] {', '.join(owners) or 'league'}"
        if add_names:
            summary += f" | added: {', '.join(add_names)}"
        if drop_names:
            summary += f" | dropped: {', '.join(drop_names)}"
        print(summary)


def print_draft(picks: list, players: dict) -> None:
    print(f"\nDraft picks ({len(picks)}):")
    for pick in picks:
        round_ = pick.get("round")
        pick_no = pick.get("pick_no")
        name = player_name(players, pick.get("player_id"))
        print(f"  - Round {round_}, Pick {pick_no}: {name}")


def draft_board_text(draft: dict, picks: list, players: dict, display_names: dict) -> str:
    total_slots = draft.get("settings", {}).get("rounds", 0) * draft.get("settings", {}).get("teams", 0)
    lines = [f"Draft status: {draft.get('status')} ({len(picks)}/{total_slots or '?'} picks made)", ""]

    for pick in picks:
        round_ = pick.get("round")
        pick_no = pick.get("pick_no")
        name = player_name(players, pick.get("player_id"))
        drafter = display_names.get(pick.get("picked_by"), f"Roster {pick.get('roster_id')}")
        lines.append(f"  Round {round_}, Pick {pick_no}: {drafter} -> {name}")

    if draft.get("status") == "complete":
        lines.append("\nDraft complete.")

    return "\n".join(lines)


def slot_on_the_clock(draft: dict, picks_made: int) -> int:
    settings = draft.get("settings", {})
    teams = settings.get("teams")
    if not teams:
        return None

    next_pick_no = picks_made + 1
    round_no = (next_pick_no - 1) // teams + 1
    position_in_round = (next_pick_no - 1) % teams + 1

    if draft.get("type") == "linear":
        return position_in_round
    # snake (default): odd rounds go 1->teams, even rounds reverse
    if round_no % 2 == 1:
        return position_in_round
    return teams - position_in_round + 1


def roster_id_for_slot(draft: dict, slot: int):
    slot_to_roster = draft.get("slot_to_roster_id") or {}
    return slot_to_roster.get(str(slot), slot_to_roster.get(slot))


def compute_roster_needs(roster_positions: list, drafted_positions: list) -> tuple:
    exact_needed = Counter()
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
            exact_needed[slot] += 1

    drafted_counts = Counter(drafted_positions)
    exact_remaining = {pos: max(cnt - drafted_counts.get(pos, 0), 0) for pos, cnt in exact_needed.items()}

    flex_pool = {"RB", "WR", "TE"} | ({"QB"} if superflex else set())
    surplus = sum(max(drafted_counts.get(pos, 0) - exact_needed.get(pos, 0), 0) for pos in flex_pool)
    flex_remaining = max(flex_slots - surplus, 0)

    return exact_remaining, flex_remaining, flex_pool


def build_recommendation_panel(
    players: dict, drafted_ids: set, roster_positions: list, my_drafted_positions: list, top_n: int = 8
) -> str:
    exact_remaining, flex_remaining, flex_pool = compute_roster_needs(roster_positions, my_drafted_positions)
    needed_positions = {pos for pos, cnt in exact_remaining.items() if cnt > 0}
    if flex_remaining > 0:
        needed_positions |= flex_pool

    available = [
        p for pid, p in players.items()
        if pid not in drafted_ids and p.get("position") in ("QB", "RB", "WR", "TE", "K", "DEF") and p.get("team")
    ]
    available.sort(key=lambda p: p.get("search_rank") if p.get("search_rank") is not None else 999999)

    lines = ["", "=" * 50, "YOU'RE ON THE CLOCK", "=" * 50]
    lines.append("Remaining starter needs: " + (", ".join(sorted(needed_positions)) or "bench/depth only"))

    needed_players = [p for p in available if p.get("position") in needed_positions][:top_n]
    lines.append("\nTop available at positions you need:")
    for p in needed_players:
        lines.append(f"  - {p.get('full_name')} ({p.get('position')}, {p.get('team')}) rank={p.get('search_rank')}")

    lines.append("\nTop overall available:")
    for p in available[:top_n]:
        lines.append(f"  - {p.get('full_name')} ({p.get('position')}, {p.get('team')}) rank={p.get('search_rank')}")

    lines.append("=" * 50)
    return "\n".join(lines)


def watch_draft(draft_id: str, players: dict, users: list, league: dict = None, my_roster_id=None) -> None:
    display_names = team_display_names(users)
    clear = "cls" if os.name == "nt" else "clear"

    print(f"Watching draft {draft_id} (refreshing every {WATCH_POLL_SECONDS}s, Ctrl+C to stop)...")
    try:
        while True:
            draft = get_draft(draft_id)
            picks = get_draft_picks(draft_id)

            os.system(clear)
            print(draft_board_text(draft, picks, players, display_names))

            if my_roster_id is not None and draft.get("status") != "complete":
                slot = slot_on_the_clock(draft, len(picks))
                if slot is not None and roster_id_for_slot(draft, slot) == my_roster_id:
                    drafted_ids = {p.get("player_id") for p in picks}
                    my_positions = [
                        players.get(p.get("player_id"), {}).get("position")
                        for p in picks
                        if p.get("roster_id") == my_roster_id
                    ]
                    print(build_recommendation_panel(
                        players, drafted_ids, league.get("roster_positions", []), my_positions
                    ))

            if draft.get("status") == "complete":
                break
            time.sleep(WATCH_POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped watching draft.")


def print_trending(trending: list, players: dict, direction: str) -> None:
    print(f"\nTrending {direction} (league-wide, last 24h):")
    for entry in trending:
        name = player_name(players, entry.get("player_id"))
        print(f"  - {name}: {entry.get('count')} {direction}s")


def resolve_league(args) -> tuple:
    if args.league_id:
        print(f"Fetching league {args.league_id}...")
        league = get_league(args.league_id)
    else:
        print(f"Looking up Sleeper user '{args.username}'...")
        user = get_user(args.username)
        print(f"Found user: {user.get('display_name')} (user_id={user['user_id']})")

        print(f"\nFetching {args.season} leagues for this user...")
        leagues = get_leagues_for_user(user["user_id"], args.season)

        if not leagues:
            print(f"No leagues found for {args.season}.")
            return None, None, None

        print(f"Found {len(leagues)} league(s):")
        for l in leagues:
            print(f"  - {l['name']} (league_id={l['league_id']})")

        league = leagues[0]

    rosters = get_rosters(league["league_id"])
    users = get_users_in_league(league["league_id"])
    return league, rosters, users


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--username", help="Your Sleeper username")
    group.add_argument("--league-id", help="A specific Sleeper league ID")
    parser.add_argument(
        "--season", default="2026", help="NFL season year (default: 2026)"
    )
    parser.add_argument("--matchups", type=int, metavar="WEEK", help="Show matchups for a given week")
    parser.add_argument("--transactions", type=int, metavar="ROUND", help="Show transactions for a given round/week")
    parser.add_argument("--draft", action="store_true", help="Show the most recent draft's picks")
    parser.add_argument("--trending", choices=["add", "drop"], help="Show league-wide trending adds/drops")
    parser.add_argument(
        "--watch-draft", action="store_true",
        help=f"Live-track the most recent draft, redrawing the board every {WATCH_POLL_SECONDS}s",
    )
    parser.add_argument(
        "--my-username", help="Your Sleeper username, to get pick recommendations when it's your turn (with --watch-draft)"
    )
    args = parser.parse_args()

    try:
        league, rosters, users = resolve_league(args)
        if league is None:
            return 0

        print_league_summary(league, rosters, users)

        needs_players = args.transactions is not None or args.draft or args.trending or args.watch_draft
        players = get_all_players() if needs_players else {}

        if args.matchups is not None:
            matchups = get_matchups(league["league_id"], args.matchups)
            print_matchups(matchups, rosters, users, args.matchups)

        if args.transactions is not None:
            transactions = get_transactions(league["league_id"], args.transactions)
            print_transactions(transactions, rosters, users, players)

        if args.draft:
            drafts = get_drafts_for_league(league["league_id"])
            if not drafts:
                print("\nNo drafts found for this league.")
            else:
                picks = get_draft_picks(drafts[0]["draft_id"])
                print_draft(picks, players)

        if args.trending:
            trending = get_trending_players(args.trending)
            print_trending(trending, players, args.trending)

        if args.watch_draft:
            my_roster_id = None
            if args.my_username:
                my_user = get_user(args.my_username)
                my_roster_id = next(
                    (
                        r["roster_id"] for r in rosters
                        if r.get("owner_id") == my_user["user_id"]
                        or my_user["user_id"] in (r.get("co_owners") or [])
                    ),
                    None,
                )
                if my_roster_id is None:
                    print(f"\nCould not find a roster owned by '{args.my_username}' in this league.")

            drafts = get_drafts_for_league(league["league_id"])
            if not drafts:
                print("\nNo drafts found for this league.")
            else:
                watch_draft(drafts[0]["draft_id"], players, users, league, my_roster_id)
            return 0

        print("\nSuccess: Sleeper API access is working.")
        return 0

    except requests.exceptions.RequestException as e:
        print(f"\nNetwork/API error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
