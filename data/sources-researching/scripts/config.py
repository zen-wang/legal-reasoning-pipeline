# config.py — API tokens and settings
# Token moved to .env file in project root

import os

# CourtListener (register at https://www.courtlistener.com/sign-in/ to get token)
COURTLISTENER_TOKEN = os.environ.get("COURTLISTENER_TOKEN", "")

# Caselaw Access Project — no longer needs a separate token
# CAP now redirects to CourtListener API, so source 4 uses the same token above
CAP_TOKEN = "NOT_NEEDED"

# SEC EDGAR (no token needed, only User-Agent)
# Format: "Organization ContactEmail"
SEC_USER_AGENT = "ASU_CIPS_Lab wwang360@asu.edu"

# General settings
REQUEST_DELAY = 1.0        # delay between requests (seconds)
PDF_DOWNLOAD_DELAY = 1.0   # delay between PDF downloads
MAX_PDFS_PER_CASE = 10     # max PDFs to download per case
MAX_ENTRIES_PER_CASE = 50   # max docket entries to fetch per case
