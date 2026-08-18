#!/usr/bin/env python3
"""
Test access to a Sleeper fantasy football league via the public Sleeper API.

Sleeper's API is read-only and requires no API key or authentication -
you just need your username or league ID.

Usage:
    python test_sleeper_access.py --username YOUR_SLEEPER_USERNAME [--season 2026]
    python test_sleeper_access.py --league-id YOUR_LEAGUE_ID

Find your league ID in the Sleeper app/website URL, e.g.:
    https://sleeper.com/leagues/1234567890123456789/team
                                 ^^^^^^^^^^^^^^^^^^^ this is the league_id
"""

import argparse
import sys

import requests

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


def print_league_summary(league: dict, rosters: list, users: list) -> None:
    print(f"\nLeague name:   {league.get('name')}")
    print(f"League ID:     {league.get('league_id')}")
    print(f"Season:        {league.get('season')}")
    print(f"Status:        {league.get('status')}")
    print(f"Total rosters: {league.get('total_rosters')}")

    display_names = {u["user_id"]: u.get("display_name", "?") for u in users}
    print("\nTeams:")
    for roster in rosters:
        owner = display_names.get(roster.get("owner_id"), "Unknown")
        wins = roster.get("settings", {}).get("wins", 0)
        losses = roster.get("settings", {}).get("losses", 0)
        print(f"  - Roster {roster.get('roster_id')}: {owner} ({wins}-{losses})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--username", help="Your Sleeper username")
    group.add_argument("--league-id", help="A specific Sleeper league ID")
    parser.add_argument(
        "--season", default="2026", help="NFL season year (default: 2026)"
    )
    args = parser.parse_args()

    try:
        if args.league_id:
            print(f"Fetching league {args.league_id}...")
            league = get_league(args.league_id)
            rosters = get_rosters(args.league_id)
            users = get_users_in_league(args.league_id)
            print_league_summary(league, rosters, users)
        else:
            print(f"Looking up Sleeper user '{args.username}'...")
            user = get_user(args.username)
            print(f"Found user: {user.get('display_name')} (user_id={user['user_id']})")

            print(f"\nFetching {args.season} leagues for this user...")
            leagues = get_leagues_for_user(user["user_id"], args.season)

            if not leagues:
                print(f"No leagues found for {args.season}.")
                return 0

            print(f"Found {len(leagues)} league(s):")
            for league in leagues:
                print(f"  - {league['name']} (league_id={league['league_id']})")

            first = leagues[0]
            rosters = get_rosters(first["league_id"])
            users = get_users_in_league(first["league_id"])
            print_league_summary(first, rosters, users)

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
