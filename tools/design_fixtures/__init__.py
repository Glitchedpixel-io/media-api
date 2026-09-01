"""Design fixture generator.

Captures real API response bodies from a live media-api database and writes them to
disk verbatim, for front-end design work that needs true response shapes rather than
invented data.

Read-only throughout: every capture is a GET, and the supporting database queries used
to *select* records are SELECTs issued through a read-only role.
"""
