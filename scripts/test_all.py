# scripts/test_all.py — run every scratch-DB scenario harness and report a combined total.
#
#   python -m scripts.test_all        (needs DATABASE_URL = the local sandbox)
#
# Rolls up the booking-engine harness + the commercial-engine harness. Each is self-contained
# (its own scratch club, always rolled back). Exits non-zero if ANY check fails — the gate for
# diary/ + billing/ changes alongside `python -m py_compile` and `python -m db` (twice).

import sys

from scripts import test_booking_scenarios as booking
from scripts import test_billing_scenarios as billing
from scripts import test_statement_reconciliation as statement


def main():
    rc = 0
    print("################  BOOKING ENGINE  ################")
    rc |= booking.main()
    print("\n################  COMMERCIAL ENGINES  ################")
    rc |= billing.main()
    print("\n################  STATEMENT RECONCILIATION  ################")
    rc |= statement.main()
    print("\n" + "#" * 60)
    print("ALL HARNESSES PASSED" if rc == 0 else "SOME CHECKS FAILED (see above)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
