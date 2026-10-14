from __future__ import annotations

import sys

from app.db.session import SessionLocal
from app.services.fax_portal_acuity import SyncAlreadyRunningError, run_acuity_sync

# Entry point for the hourly Cloud Run job "fax-portal-acuity-sync":
#   python -m app.scripts.fax_portal_acuity_sync
# Exits non-zero when the sync fails so Cloud Run / Scheduler show the failure.


def main() -> int:
    with SessionLocal() as db:
        try:
            run = run_acuity_sync(db, trigger="scheduled")
        except SyncAlreadyRunningError:
            print("Another Acuity sync is already running; skipping.")

            return 0

        if run.status != "success":
            print(f"Acuity sync failed: {run.error_message}")

            return 1

        print(
            "Acuity sync ok: "
            f"calendars +{run.calendars_added} ~{run.calendars_updated} -{run.calendars_removed}, "
            f"types +{run.types_added} ~{run.types_updated} -{run.types_removed}"
        )

        return 0


if __name__ == "__main__":
    sys.exit(main())
