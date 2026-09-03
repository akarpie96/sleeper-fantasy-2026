#!/usr/bin/env python3
"""
Fetch your Sleeper league's data and ask Claude for roster/trade analysis,
grounded in current NFL reporting via web search.

Sends Claude: all rosters, computed positional needs, the top free agents,
your league's real scoring rules, and (once games are played) standings and
weekly results. Claude researches current player news before advising.

Requires the `anthropic` package and an API key:
    pip install anthropic requests
    export ANTHROPIC_API_KEY="your-key-here"   # from console.anthropic.com

Usage:
    python trade_advisor_llm.py --league-id YOUR_LEAGUE_ID --my-username YOUR_SLEEPER_USERNAME

Options:
    --no-search       Skip web research (faster/cheaper, but relies on stale training data)
    --free-agents N   How many top free agents to include (default 50)
    --refresh-players Force re-download of the cached NFL player database
"""

import argparse
import json
import os
import sys
import time
from collections import Counter

import anthropic
import requests

BASE_URL = "https://api.sleeper.app/v1"
MODEL = "claude-opus-5"
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
PLAYER_CACHE_PATH = os.path.expanduser("~/.cache/sleeper_players_nfl.json")
PLAYER_CACHE_MAX_AGE = 24 * 60 * 60  # re-download once a day


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
    """The player database is ~5MB and changes slowly, so cache it on disk."""
    if not force_refresh and os.path.exists(PLAYER_CACHE_PATH):
        age = time.time() - os.path.getmtime(PLAYER_CACHE_PATH)
        if age < PLAYER_CACHE_MAX_AGE:
            try:
                with open(PLAYER_CACHE_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # fall through and re-download

    print("Downloading NFL player database (~5MB, cached for 24h)...")
    players = _get("/players/nfl", timeout=60)
    try:
        os.makedirs(os.path.dirname(PLAYER_CACHE_PATH), exist_ok=True)
        with open(PLAYER_CACHE_PATH, "w") as f:
            json.dump(players, f)
    except OSError:
        pass  # caching is best-effort
    return players


# --- Formatting helpers -----------------------------------------------------

def gather_player_scoring(league_id: str, current_week: int) -> dict:
    """Per-player actual fantasy points for each completed week, keyed by player id."""
    scoring = {}
    last_completed = min(max(current_week - 1, 0), 18)
    if last_completed < 1:
        return scoring

    print(f"Fetching per-player scoring for weeks 1-{last_completed}...")
    for week in range(1, last_completed + 1):
        try:
            matchups = get_matchups(league_id, week)
        except requests.exceptions.RequestException:
            break  # partial history is still useful
        for entry in matchups or []:
            for pid, points in (entry.get("players_points") or {}).items():
                scoring.setdefault(pid, []).append(points)
    return scoring


def scoring_note(scoring: dict, player_id: str) -> str:
    weeks = scoring.get(player_id)
    if not weeks:
        return ""
    total = sum(weeks)
    avg = total / len(weeks)
    recent = ", ".join(f"{p:g}" for p in weeks[-3:])
    return f" [{avg:.1f}ppg over {len(weeks)}g; last: {recent}]"


def player_label(players: dict, player_id: str, scoring: dict = None) -> str:
    p = players.get(player_id)
    if not p:
        return str(player_id)
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    pos = p.get("position", "?")
    team = p.get("team") or "FA"
    status = f", {p.get('injury_status')}" if p.get("injury_status") else ""
    note = scoring_note(scoring, player_id) if scoring else ""
    return f"{name} ({pos}, {team}{status}){note}"


def rank_of(player: dict) -> int:
    rank = player.get("search_rank")
    return rank if rank is not None else 999999


def compute_position_balance(roster_positions: list, player_ids: list, players: dict) -> tuple:
    """Return (starter_needs, surplus_depth) for a roster."""
    owned = Counter()
    for pid in player_ids:
        p = players.get(pid)
        if p and p.get("position") in FANTASY_POSITIONS:
            owned[p["position"]] += 1

    required = Counter()
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
            required[slot] += 1

    need = {}
    surplus = {}
    for pos in FANTASY_POSITIONS:
        diff = owned.get(pos, 0) - required.get(pos, 0)
        if diff < 0:
            need[pos] = -diff
        elif diff > 0:
            surplus[pos] = diff

    flex_pool = {"RB", "WR", "TE"} | ({"QB"} if superflex else set())
    remaining_flex = flex_slots
    for pos in sorted(flex_pool, key=lambda p: -surplus.get(p, 0)):
        if remaining_flex <= 0:
            break
        used = min(surplus.get(pos, 0), remaining_flex)
        if used:
            surplus[pos] -= used
            if not surplus[pos]:
                del surplus[pos]
            remaining_flex -= used
    if remaining_flex > 0:
        need["FLEX"] = remaining_flex

    return need, surplus


def active_scoring_rules(scoring: dict) -> dict:
    """Most of Sleeper's ~140 scoring keys are zero; only the rest matter."""
    return {k: v for k, v in sorted(scoring.items()) if v}


def top_free_agents(players: dict, rostered_ids: set, limit: int) -> list:
    pool = [
        {**p, "player_id": p.get("player_id", pid)}
        for pid, p in players.items()
        if pid not in rostered_ids
        and p.get("position") in FANTASY_POSITIONS
        and p.get("team")
        and p.get("active", True)
    ]
    pool.sort(key=rank_of)
    return pool[:limit]


# --- Payload ----------------------------------------------------------------

def build_league_dump(league, rosters, users, players, my_roster_id, nfl_state, free_agent_limit, scoring=None) -> str:
    names_by_id = {
        u["user_id"]: ((u.get("metadata") or {}).get("team_name") or u.get("display_name", "?")).strip()
        for u in users
    }
    roster_positions = league.get("roster_positions", [])

    lines = [
        f"League: {league.get('name')} ({league.get('season')} season)",
        f"League status: {league.get('status')}",
        f"Current NFL week: {nfl_state.get('week')} ({nfl_state.get('season_type')})",
        f"Starting lineup + bench: {roster_positions}",
        "",
        "Scoring rules (only non-zero values shown):",
        json.dumps(active_scoring_rules(league.get("scoring_settings") or {}), indent=2),
        "",
    ]

    games_played = any(
        (r.get("settings") or {}).get("wins", 0) or (r.get("settings") or {}).get("losses", 0)
        for r in rosters
    )

    if games_played:
        lines.append("=== STANDINGS ===")
        ranked = sorted(
            rosters,
            key=lambda r: (
                -(r.get("settings") or {}).get("wins", 0),
                -(r.get("settings") or {}).get("fpts", 0),
            ),
        )
        for r in ranked:
            s = r.get("settings") or {}
            name = names_by_id.get(r.get("owner_id"), "Unknown")
            # Sleeper splits points into whole and hundredths parts.
            pf = s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100
            pa = s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100
            lines.append(
                f"  {name}: {s.get('wins', 0)}-{s.get('losses', 0)}-{s.get('ties', 0)}, "
                f"{pf:.2f} PF / {pa:.2f} PA"
            )
        lines.append("")
    else:
        lines.append("=== STANDINGS ===")
        lines.append("  Season has not started — no games played, all teams 0-0.")
        lines.append("")

    lines.append("=== ROSTERS ===")
    for roster in sorted(rosters, key=lambda r: r["roster_id"]):
        team_name = names_by_id.get(roster.get("owner_id"), "Unknown")
        mine = "   <<<<< THIS IS MY TEAM" if roster["roster_id"] == my_roster_id else ""
        all_ids = roster.get("players") or []
        starters = [pid for pid in (roster.get("starters") or []) if pid and pid != "0"]
        starter_set = set(starters)
        bench = [pid for pid in all_ids if pid not in starter_set]

        need, surplus = compute_position_balance(roster_positions, all_ids, players)

        lines.append(f"--- {team_name}{mine}")
        lines.append("    Starters: " + (", ".join(player_label(players, p, scoring) for p in starters) or "none set"))
        lines.append("    Bench:    " + (", ".join(player_label(players, p, scoring) for p in bench) or "empty"))
        lines.append(f"    Computed starter shortfalls: {need or 'none'}")
        lines.append(f"    Computed surplus depth:      {surplus or 'none'}")
        lines.append("")

    rostered = {pid for r in rosters for pid in (r.get("players") or [])}
    free_agents = top_free_agents(players, rostered, free_agent_limit)
    lines.append(f"=== TOP {len(free_agents)} AVAILABLE FREE AGENTS (not on any roster) ===")
    lines.append(", ".join(
        f"{p.get('full_name')} ({p.get('position')}, {p.get('team')}"
        f"{', ' + p['injury_status'] if p.get('injury_status') else ''})"
        f"{scoring_note(scoring, p.get('player_id')) if scoring else ''}"
        for p in free_agents
    ))

    if scoring:
        lines.append("")
        lines.append(
            "Note: [N.Nppg over Xg; last: ...] shows each player's ACTUAL fantasy points "
            "in this league's scoring this season — average per game, games played, and the "
            "three most recent weekly scores. Weight this heavily over preseason expectations."
        )

    return "\n".join(lines)


SYSTEM_PROMPT = """You are a sharp, realistic fantasy football analyst advising one manager in a \
12-team league. You are given complete league data: every roster, computed positional \
surpluses and shortfalls, the league's real scoring rules, current standings, the top \
available free agents, and — once the season is underway — each player's actual fantasy \
points scored in this league.

When actual scoring data is present, weight it heavily: a player's real production and \
recent trend in THIS league's scoring matters more than reputation or draft position. \
Also read the standings strategically — a team near the bottom is more willing to trade \
proven production for upside, while a contender wants win-now pieces.

IMPORTANT: your training data is older than the current NFL season. Before giving advice, \
use web search to check the current state of the relevant players — depth chart roles, \
target/carry share, camp or in-season injury news, and any recent trades or suspensions. \
Do not rely on remembered player situations; verify the ones your recommendations depend on. \
Be efficient: research the handful of players that actually drive your recommendations \
rather than every player in the league.

Then deliver, concisely:
1. An honest read of the user's roster — real strengths and real weaknesses, in terms of \
roster construction against their starting lineup requirements, not just player names.
2. Two to four concrete trade proposals naming specific teams and specific players on both \
sides. For each, explain why the other manager would plausibly say yes — a trade only the \
user benefits from is useless.
3. Any free agent pickups worth making, including who to drop. Often this beats a trade.

Be direct. Flag when you are uncertain, and say so when the data or your research is \
inconclusive rather than inventing confident detail."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--league-id", required=True, help="A Sleeper league ID")
    parser.add_argument("--my-username", required=True, help="Your Sleeper username")
    parser.add_argument("--no-search", action="store_true", help="Disable Claude's web research")
    parser.add_argument("--free-agents", type=int, default=50, help="Free agents to include (default 50)")
    parser.add_argument("--refresh-players", action="store_true", help="Force player DB re-download")
    parser.add_argument("--no-scoring", action="store_true",
                        help="Skip fetching weekly per-player scoring history (faster)")
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
            print(f"Could not find a roster owned by '{args.my_username}' in this league.", file=sys.stderr)
            return 1

        scoring = {}
        if not args.no_scoring:
            week = nfl_state.get("week") or 0
            if (nfl_state.get("season_type") or "").lower() == "regular":
                scoring = gather_player_scoring(args.league_id, week)

        league_dump = build_league_dump(
            league, rosters, users, players, my_roster["roster_id"], nfl_state,
            args.free_agents, scoring,
        )

        tools = []
        if not args.no_search:
            tools.append({
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 12,
            })

        if tools:
            print("Asking Claude to research current NFL news and analyze the league...\n")
        else:
            print("Asking Claude to analyze the league (web research disabled)...\n")

        client = anthropic.Anthropic()
        request = {
            "model": MODEL,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": league_dump}],
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
