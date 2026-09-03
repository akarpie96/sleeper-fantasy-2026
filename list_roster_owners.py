#!/usr/bin/env python3
"""
List every owner (including co-owners) on each roster in a Sleeper league.

Usage:
    python list_roster_owners.py --league-id YOUR_LEAGUE_ID
"""

import argparse
import sys

import requests

BASE_URL = "https://api.sleeper.app/v1"


def get_rosters(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/rosters", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_users_in_league(league_id: str) -> list:
    resp = requests.get(f"{BASE_URL}/league/{league_id}/users", timeout=10)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", required=True, help="A Sleeper league ID")
    args = parser.parse_args()

    try:
        rosters = get_rosters(args.league_id)
        users = get_users_in_league(args.league_id)
        names_by_id = {
            u["user_id"]: ((u.get("metadata") or {}).get("team_name") or u.get("display_name", "?")).strip()
            for u in users
        }

        for roster in rosters:
            owner_id = str(roster.get("owner_id"))
            # Sleeper sometimes lists the primary owner a second time inside co_owners;
            # drop that (comparing as strings, since id types can be inconsistent) and any duplicate ids.
            co_owner_ids = list(dict.fromkeys(
                str(cid) for cid in (roster.get("co_owners") or []) if cid and str(cid) != owner_id
            ))

            owner_name = names_by_id.get(owner_id, "Unknown")
            # Sleeper's public API doesn't expose usernames for other accounts, so show the
            # raw user_id as the disambiguator when team names collide.
            co_owner_labels = [f"{names_by_id.get(cid, 'Unknown')} (id={cid})" for cid in co_owner_ids]

            print(f"Roster {roster.get('roster_id')}: {owner_name} (id={owner_id})", end="")
            if co_owner_labels:
                print(f" (co-owned with: {', '.join(co_owner_labels)})")
            else:
                print()

        return 0

    except requests.exceptions.RequestException as e:
        print(f"\nNetwork/API error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
