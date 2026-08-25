"""One-time setup utility: list WestFax products to find WESTFAX_PRODUCT_ID.

Only needed once, to discover the product GUID before it's configured -
SendFax/GetFaxStatus use the ID directly and never call this. Safe to
delete this file (and get_product_list/ProductInfo in
app/integrations/westfax_client.py) once you no longer need it.

Usage:
    python -m app.scripts.westfax_list_products
    python -m app.scripts.westfax_list_products --product-type All
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import settings
from app.integrations.westfax_client import WestFaxApiError, get_product_list


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--product-type",
        default="Fax",
        help="WestFax ProductType filter to query (default: Fax).",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if not (settings.westfax_username and settings.westfax_password):
        print(
            "WESTFAX_USERNAME and WESTFAX_PASSWORD must be set in .env first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        products = get_product_list(
            base_url=settings.westfax_base_url,
            username=settings.westfax_username,
            password=settings.westfax_password,
            product_type=args.product_type,
        )
    except WestFaxApiError as exc:
        print(f"WestFax rejected the request: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not products:
        print("No products returned for this account/product type.")
        return

    print("Set WESTFAX_PRODUCT_ID to one of the following:\n")

    for product in products:
        print(f"  {product.id}  {product.name}")


if __name__ == "__main__":
    main()
