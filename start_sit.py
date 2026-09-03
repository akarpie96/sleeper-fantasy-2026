#!/usr/bin/env python3
"""
Start/sit optimizer for a Sleeper fantasy roster.

Pulls your roster, works out which players are eligible for each starting slot,
and asks Claude to recommend the optimal lineup — researching current NFL news
(injuries, depth charts, matchups, byes) before deciding.

Requires the `anthropic` package and an API key:
    pip install anthropic requests
    export ANTHROPIC_API_KEY="your-key-here"   # from console.anthropic.com

Usage:
    python start_sit.py --league-id YOUR_LEAGUE_ID --my-username YOUR_SLEEPER_USERNAME

Options:
    --week N          Analyze a specific week (default: current NFL week)
    --no-search       Skip web research (much weaker advice; relies on stale training data)
    --refresh-players Force re-download of the cached NFL player database
"""

import argparse
import json
import os
import sys
import time

import anthropic
import requests

BASE_URL = "https://api.sleeper.app/v1"
MODEL = "claude-opus-5"
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
PLAYER_CACHE_PATH = os.path.expanduser("~/.cache/sleeper_players_nfl.json")
PLAYER_CACHE_MAX_AGE = 24 * 60 * 60

# Which roster positions each Sleeper lineup slot accepts.
SLOT_ELIGIBILITY = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}

BENCH_SLOTS = ("BN", "IR", "TAXI")


# --- Sleeper API ------------------------------------------------------------

def _get(path: str, timeout: int = 10):
    resp = requests.get(f"{BASE_URL}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_league(league_id: str) -> dict:
    data = _get(f"/league/{league_id}")
    if data is None:
        raise ValueError(f"No league found for league_id '{league_id}'")
    return data


def get_rosters(league_id: str) -> list:
    return _get(f"/league/{league_id}/rosters")


def get_users_in_league(league_id: str) -> list:
    return _get(f"/league/{league_id}/users")


def get_matchups(league_id: str, week: int) -> list:
    return _get(f"/league/{league_id}/matchups/{week}")


def get_nfl_state() -> dict:
    return _get("/state/nfl")


def get_user(username: str) -> dict:
    data = _get(f"/user/{username}")
    if data is None:
        raise ValueError(f"No Sleeper user found for username '{username}'")
    return data


def get_all_players(force_refresh: bool = False) -> dict:
    if not force_refresh and os.path.exists(PLAYER_CACHE_PATH):
        age = time.time() - os.path.getmtime(PLAYER_CACHE_PATH)
        if age < PLAYER_CACHE_MAX_AGE:
            try:
                with open(PLAYER_CACHE_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    print("Downloading NFL player database (~5MB, cached for 24h)...")
    players = _get("/players/nfl", timeout=60)
    try:
        os.makedirs(os.path.dirname(PLAYER_CACHE_PATH), exist_ok=True)
        with open(PLAYER_CACHE_PATH, "w") as f:
            json.dump(players, f)
    except OSError:
        pass
    return players


# --- Helpers ----------------------------------------------------------------

def gather_player_scoring(league_id: str, current_week: int) -> dict:
    """Per-player actual fantasy points for completed weeks."""
    scoring = {}
    last_completed = min(max(current_week - 1, 0), 18)
    if last_completed < 1:
        return scoring

    print(f"Fetching per-player scoring for weeks 1-{last_completed}...")
    for week in range(1, last_completed + 1):
        try:
            matchups = get_matchups(league_id, week)
        except requests.exceptions.RequestException:
            break
        for entry in matchups or []:
            for pid, points in (entry.get("players_points") or {}).items():
                scoring.setdefault(pid, []).append(points)
    return scoring


def scoring_note(scoring: dict, player_id: str) -> str:
    weeks = scoring.get(player_id) if scoring else None
    if not weeks:
        return ""
    avg = sum(weeks) / len(weeks)
    recent = ", ".join(f"{p:g}" for p in weeks[-3:])
    return f" [{avg:.1f}ppg over {len(weeks)}g; last: {recent}]"


def describe(players: dict, player_id: str, scoring: dict = None) -> str:
    p = players.get(player_id)
    if not p:
        return str(player_id)
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    pos = p.get("position", "?")
    team = p.get("team") or "FA"
    status = f", {p['injury_status']}" if p.get("injury_status") else ""
    return f"{name} ({pos}, {team}{status}){scoring_note(scoring, player_id)}"


def active_scoring_rules(scoring_settings: dict) -> dict:
    return {k: v for k, v in sorted(scoring_settings.items()) if v}


def eligible_for(slot: str, position: str) -> bool:
    allowed = SLOT_ELIGIBILITY.get(slot)
    return bool(allowed and position in allowed)


def find_opponent(matchups: list, my_roster_id: int):
    """Return (my_matchup_entry, opponent_entry) for the week, if scheduled."""
    mine = next((m for m in matchups or [] if m.get("roster_id") == my_roster_id), None)
    if not mine or mine.get("matchup_id") is None:
        return mine, None
    opponent = next(
        (
            m for m in matchups
            if m.get("matchup_id") == mine["matchup_id"] and m.get("roster_id") != my_roster_id
        ),
        None,
    )
    return mine, opponent


# --- Payload ----------------------------------------------------------------

def build_payload(league, my_roster, players, week, scoring, opponent_entry, opponent_name) -> str:
    roster_positions = league.get("roster_positions", [])
    starting_slots = [s for s in roster_positions if s not in BENCH_SLOTS]

    all_ids = [pid for pid in (my_roster.get("players") or []) if pid and pid != "0"]
    current_starters = [pid for pid in (my_roster.get("starters") or []) if pid and pid != "0"]
    bench = [pid for pid in all_ids if pid not in set(current_starters)]

    lines = [
        f"League: {league.get('name')} ({league.get('season')} season)",
        f"Analyzing lineup for WEEK {week}.",
        "",
        "Scoring rules (non-zero values only):",
        json.dumps(active_scoring_rules(league.get("scoring_settings") or {}), indent=2),
        "",
        f"Required starting lineup slots (in order): {starting_slots}",
        "",
        "=== MY CURRENT LINEUP AS SET ===",
    ]

    for slot, pid in zip(starting_slots, current_starters):
        lines.append(f"  {slot}: {describe(players, pid, scoring)}")
    if len(current_starters) < len(starting_slots):
        for slot in starting_slots[len(current_starters):]:
            lines.append(f"  {slot}: (EMPTY)")

    lines.append("")
    lines.append("=== MY BENCH ===")
    for pid in bench:
        lines.append(f"  {describe(players, pid, scoring)}")
    if not bench:
        lines.append("  (empty)")

    lines.append("")
    lines.append("=== ELIGIBLE OPTIONS FOR EACH STARTING SLOT ===")
    lines.append("(every player on my roster who can legally fill that slot)")
    for slot in dict.fromkeys(starting_slots):
        options = [
            pid for pid in all_ids
            if eligible_for(slot, (players.get(pid) or {}).get("position", ""))
        ]
        rendered = "; ".join(describe(players, pid, scoring) for pid in options) or "none available"
        lines.append(f"  {slot}: {rendered}")

    if opponent_entry is not None:
        lines.append("")
        lines.append(f"=== THIS WEEK'S OPPONENT: {opponent_name} ===")
        opp_starters = [p for p in (opponent_entry.get("starters") or []) if p and p != "0"]
        lines.append("  Their starters: " + (
            ", ".join(describe(players, pid, scoring) for pid in opp_starters) or "not set yet"
        ))

    return "\n".join(lines)


SYSTEM_PROMPT = """You are a sharp fantasy football start/sit advisor. You are given one \
manager's full roster, the exact starting-lineup slots they must fill, which of their \
players is eligible for each slot, this league's real scoring rules, and — if the season \
is underway — each player's actual fantasy points to date.

IMPORTANT: your training data predates the current NFL season, so you must not rely on \
remembered depth charts or player situations. Use web search to check, for the specific \
players whose start/sit status is genuinely in question: current injury designations and \
practice participation, this week's bye teams, depth chart / snap-share role, and the \
strength of their matchup this week. Research the handful of real decisions, not every \
player on the roster — obvious every-week starters do not need investigation.

Then output:
1. RECOMMENDED LINEUP — the full slate of starting slots, each with the player you would \
start. Mark any change from their current lineup clearly.
2. THE REAL DECISIONS — for each genuinely close call, a short explanation of why you \
landed where you did, naming the specific factor (matchup, role, injury, floor vs ceiling).
3. WARNINGS — anyone currently in the lineup who is on bye, ruled Out, injured, or \
otherwise unstartable. This is the highest-value thing you can catch, so check it carefully.

Be decisive; a start/sit answer that hedges everything is useless. But flag genuine \
uncertainty (e.g. a game-time decision) and say what to monitor before kickoff."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--league-id", required=True, help="A Sleeper league ID")
    parser.add_argument("--my-username", required=True, help="Your Sleeper username")
    parser.add_argument("--week", type=int, help="Week to analyze (default: current NFL week)")
    parser.add_argument("--no-search", action="store_true", help="Disable Claude's web research")
    parser.add_argument("--refresh-players", action="store_true", help="Force player DB re-download")
    args = parser.parse_args()

    try:
        print("Fetching league data from Sleeper...")
        league = get_league(args.league_id)
        rosters = get_rosters(args.league_id)
        users = get_users_in_league(args.league_id)
        players = get_all_players(force_refresh=args.refresh_players)

        try:
            nfl_state = get_nfl_state()
        except requests.exceptions.RequestException:
            nfl_state = {}

        week = args.week or nfl_state.get("week") or 1
        season_type = (nfl_state.get("season_type") or "").lower()

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
            print(f"Could not find a roster owned by '{args.my_username}' in this league.",
                  file=sys.stderr)
            return 1

        scoring = {}
        if season_type == "regular":
            scoring = gather_player_scoring(args.league_id, week)

        opponent_entry = opponent_name = None
        try:
            matchups = get_matchups(args.league_id, week)
            _, opponent_entry = find_opponent(matchups, my_roster["roster_id"])
            if opponent_entry:
                names_by_id = {
                    u["user_id"]: ((u.get("metadata") or {}).get("team_name")
                                   or u.get("display_name", "?")).strip()
                    for u in users
                }
                opp_roster = next(
                    (r for r in rosters if r["roster_id"] == opponent_entry.get("roster_id")), None
                )
                opponent_name = names_by_id.get((opp_roster or {}).get("owner_id"), "Unknown")
        except requests.exceptions.RequestException:
            pass  # matchup context is a bonus, not required

        payload = build_payload(
            league, my_roster, players, week, scoring, opponent_entry, opponent_name
        )

        tools = []
        if not args.no_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 12})
            print(f"Asking Claude to research and optimize your Week {week} lineup...\n")
        else:
            print(f"Asking Claude to optimize your Week {week} lineup (research disabled)...\n")

        client = anthropic.Anthropic()
        request = {
            "model": MODEL,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": payload}],
        }
        if tools:
            request["tools"] = tools

        with client.messages.stream(**request) as stream:
            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "text":
                    print(event.text, end="", flush=True)
                elif etype == "content_block_start":
                    block_type = getattr(getattr(event, "content_block", None), "type", None)
                    if block_type == "server_tool_use":
                        print("[researching...]", flush=True)
        print()

        return 0

    except requests.exceptions.RequestException as e:
        print(f"\nNetwork/API error (Sleeper): {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("\nInvalid or missing Anthropic API key. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
    except anthropic.RateLimitError as e:
        print(f"\nRate limited by the Anthropic API: {e}", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"\nAnthropic API error ({e.status_code}): {e.message}", file=sys.stderr)
        if "web_search" in str(e.message):
            print("Tip: your SDK or account may not support the web search tool. "
                  "Re-run with --no-search.", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("\nNetwork error reaching the Anthropic API.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
