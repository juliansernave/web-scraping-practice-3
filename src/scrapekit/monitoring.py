"""RunReport: pages fetched, records extracted/valid/invalid/duped, retry counts, duration,
extraction-rate-per-page — plus a drift alert when extraction rate drops below threshold.

Day 4. Selector drift is the #1 silent failure mode of production scrapers; this catches it.
Reports are written to reports/ as JSON and logged as a summary.
"""

# TODO(Day 4): RunReport dataclass + drift_alert(threshold) check.
