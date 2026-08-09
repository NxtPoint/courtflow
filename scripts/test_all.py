# scripts/test_all.py — run every gate and report a combined total.
#
#   python -m scripts.test_all        (needs DATABASE_URL = the local sandbox)
#
# Rolls up the frontend-JS parse gate + the booking-engine harness + the commercial-engine
# harness. The DB harnesses are self-contained (own scratch club, always rolled back); the JS
# gate needs no DB and runs first. Exits non-zero if ANY check fails — the gate for diary/ +
# billing/ + frontend/js changes alongside `python -m py_compile` and `python -m db` (twice).
#
# Run the JS gate alone (no DATABASE_URL needed) with `python -m scripts.check_frontend_js`.

import sys

from scripts import check_frontend_js as frontend_js
from scripts import test_booking_scenarios as booking
from scripts import test_billing_scenarios as billing
from scripts import test_statement_reconciliation as statement


def main():
    rc = 0
    # First: no DB, no env, ~1s. A frontend file that does not parse is dead in the browser,
    # so surface that before spending a minute on scratch-DB harnesses.
    print("################  FRONTEND JS PARSE  ################")
    rc |= frontend_js.main()
    print("\n################  BOOKING ENGINE  ################")
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
