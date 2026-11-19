"""Generate reproducible, fictitious portfolio ledgers as JSONL on stdout.

No database connection or customer data is used. Each business has invoices,
bank receipts, a balanced journal, and open invoice-evidence exceptions.
"""

import argparse
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5


def generate(count: int, seed: int):
    for index in range(count):
        rng = random.Random(f"{seed}:{index}")
        business_id = str(uuid5(NAMESPACE_URL, f"balanc:test:{seed}:{index}"))
        journals, exceptions = [], []
        for sequence in range(1, 19):
            day = date(2024, 1, 1) + timedelta(days=rng.randrange(730))
            amount = str(Decimal(rng.randint(1, 250000)) / 100)
            invoice = f"INV-{sequence:04d}"
            journals.append((day.isoformat(), invoice, "receivables", "revenue", amount))
            if rng.randrange(4):
                paid = day + timedelta(days=rng.randrange(1, 46))
                journals.append((paid.isoformat(), invoice, "bank", "receivables", amount))
            if rng.randrange(5) == 0:
                exceptions.append({"reference": invoice, "kind": "missing_evidence"})
        yield {
            "id": business_id, "name": f"Pilot Company {index:04d}",
            "currency": ("USD", "PKR", "GBP", "AED", "EUR")[index % 5],
            "journal_columns": ["date", "reference", "debit_account", "credit_account", "amount"],
            "journals": sorted(journals), "exceptions": exceptions,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261119)
    parser.add_argument("--businesses", type=int, default=500)
    args = parser.parse_args()
    if args.businesses < 1:
        parser.error("--businesses must be positive")
    for row in generate(args.businesses, args.seed):
        print(json.dumps(row, separators=(",", ":")))
