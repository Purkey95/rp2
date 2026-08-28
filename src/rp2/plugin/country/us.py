# Copyright 2021 eprbell
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from datetime import date, datetime
from typing import Set

from rp2.abstract_country import AbstractCountry
from rp2.rp2_main import rp2_main


# US-specific class
class US(AbstractCountry):
    def __init__(self) -> None:
        super().__init__("us", "usd")

    # Measured in days. Kept for introspection and for backward compatibility with the
    # plugin API; the actual classification is done by is_long_term_capital_gains() below,
    # because the US rule is a calendar anniversary and cannot be expressed as a day count.
    def get_long_term_capital_gain_period(self) -> int:
        return 365

    def is_long_term_capital_gains(self, acquired_timestamp: datetime, taxable_timestamp: datetime) -> bool:
        # IRS Topic 409 / Pub. 544: the holding period begins the day AFTER the asset is
        # acquired and includes the day of disposal, and the gain is long-term only if the
        # asset was held for MORE than one year. So the disposal date must fall strictly
        # after the one-year anniversary of the acquisition date.
        #
        # This is compared on calendar dates rather than instants, because the holding
        # period is measured in whole days: the time of day is irrelevant.
        #
        # A day count cannot express this rule. ">= 365 days" classifies a sale on the
        # one-year anniversary as long-term in a normal year (365 days elapsed), and a
        # leap year makes the anniversary 366 days out, so both cases were misclassified
        # as long-term, understating the tax owed.
        acquired_date: date = acquired_timestamp.date()
        taxable_date: date = taxable_timestamp.date()
        try:
            anniversary: date = acquired_date.replace(year=acquired_date.year + 1)
        except ValueError:
            # February 29 has no anniversary in a non-leap year. Roll forward to March 1,
            # which delays the long-term date by one day: the conservative direction, since
            # short-term is taxed at the higher rate and this can never understate the tax.
            anniversary = date(acquired_date.year + 1, 3, 1)
        return taxable_date > anniversary

    # Default accounting method to use if the user doesn't specify one on the command line
    def get_default_accounting_method(self) -> str:
        return "fifo"

    # Set of accounting methods accepted in the country
    def get_accounting_methods(self) -> Set[str]:
        return {"fifo", "hifo", "lifo", "lofo"}

    # Default set of generators to use if the user doesn't specify them on the command line
    def get_report_generators(self) -> Set[str]:
        return {
            "open_positions",
            "rp2_full_report",
            "us.tax_report_us",
        }

    # Default language to use at report generation if the user doesn't specify it on the command line (in ISO 639-1 format)
    def get_default_generation_language(self) -> str:
        return "en"


# US-specific entry point
def rp2_entry() -> None:
    rp2_main(US())
