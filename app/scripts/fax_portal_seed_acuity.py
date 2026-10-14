from __future__ import annotations

import sys

from app.db.session import SessionLocal
from app.services.fax_portal_acuity import SeedOutcome, seed_acuity_once

# One-time seeding of the Fax Portal's clinics (Acuity calendars) and exams
# (appointment types):
#
#   python -m app.scripts.fax_portal_seed_acuity
#
# Safe to run more than once: after the first successful run it does nothing
# (it checks cc_fp_acuity_sync_run for a successful sync). A failed attempt does
# not count, so it can be re-run. From then on the hourly "fax-portal-acuity-sync"
# job (and the Refresh from Acuity button) keep the tables current.
#
# It is a script rather than an Alembic migration on purpose: it needs the
# network and Acuity's API key, which migrations should never depend on.
#
# Exit codes: 0 = seeded or nothing to do, 1 = failed (re-run it).


def main() -> int:
    with SessionLocal() as db:
        outcome, run = seed_acuity_once(db)

    if outcome == SeedOutcome.ALREADY_SEEDED:
        when = run.finished_at.isoformat() if run and run.finished_at else "earlier"
        print(f"Already seeded ({when}); nothing to do.")

        return 0

    if outcome == SeedOutcome.ALREADY_RUNNING:
        print("An Acuity sync is already running; nothing to do.")

        return 0

    if outcome == SeedOutcome.FAILED:
        print(f"Seeding failed (safe to run again): {run.error_message if run else ''}")

        return 1

    assert run is not None
    print(f"Seeded from Acuity: {run.calendars_added} clinics, {run.types_added} exams.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
