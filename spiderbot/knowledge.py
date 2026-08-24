"""Static game and server knowledge for Spider Bot's system prompt.

Source of truth: spider-swing repo docs/product/play-store-listing.md
(owner-approved copy, 2026-08-05) plus the server structure built 2026-08-24.
Update this file when the game or the server changes - it is the bot's brain.
"""

GAME_KNOWLEDGE = """\
## The game: Slingy Spider

Slingy Spider (Android package com.menno420.slingyspider) is a physics-based
swinging game by solo developer Menno (menno420). It is currently in CLOSED
ALPHA testing on Google Play.

Owner-approved description of the game:
- You are a small spider in an oversized world. Fire a line of silk, swing,
  and turn speed into momentum - release at the right moment and fly.
- Swinging is the whole game; everything else exists to make it feel good.
- Control that answers you: attach, swing and release with immediate,
  predictable response. Deaths are meant to be understandable, never
  arbitrary.
- Find the flow: good timing chains swings into faster, cleaner movement.
  Accelerate through the low point, ease toward the apex, release, and arc.
  Reel in your silk line to tighten a curve - it costs energy, so spend it
  where it counts.
- A miniature world: natural and household spaces built at spider scale.
  Obstacle layouts are seeded and fair, not random chaos - the same course
  rewards the same skill.
- Catch flies mid-arc with the same silk you move with; spend earnings on
  spiders and upgrades that trade one strength for another. Nothing sells
  power over other players.
- Built for short sessions.

## The closed test (why this server exists)

Google Play requires 12 testers opted in continuously for 14 days before the
game can fully launch. The join steps live PINNED in #start-here:
0. Check which Google account the phone's Play Store uses - use that account
   for every step.
1. Join the Google Group (link in #start-here).
2. Wait ~15 minutes, open the opt-in page signed into that same account, tap
   "Become a tester".
3. Install from the Play link the opt-in page shows.
Then post "opted in" in #general to receive the Slingy Tester role (granted
manually by the owner after verification - never automatic).

Troubleshooting: "App not available" almost always means the wrong Google
account, or the group join has not propagated yet - wait an hour and retry.

Retention rules testers agreed to: do not tap "Leave the beta", do not leave
the Google group, do not uninstall during the test, and play a few times a
week (Google checks real engagement).

## The server

Channels: #start-here (read-only join instructions), #rules, #announcements,
#general (chat), #bug-reports (forum - one post per bug with device, Android
version, build, repro steps), #feedback (forum - ideas and balance thoughts).
Roles: Slingy Tester = verified member of the Play closed test.
The server may later host more of Menno's games; Slingy Spider is the focus.
"""
