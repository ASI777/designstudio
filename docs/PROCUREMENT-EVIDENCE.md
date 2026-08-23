# Procurement and datasheet evidence

Supplier searches are discovery; an orderable BOM decision uses an exact-MPN,
quantity-specific `design-studio.procurement-quote/1` record. DigiKey, Mouser,
and Nexar offers retain vendor SKU, stock, lifecycle, retrieval time, source URL,
the original currency and selected price break. INR values are explicitly
estimates tied to the named FX profile, not live currency quotes.

Vendor requests are rate-limited, bounded, retried only for transient errors,
and report per-vendor failures. A response for a tape/reel or package suffix that
does not exactly match the requested normalized MPN is excluded. The software
does not interpret “no result” as “out of stock” when a vendor errored.

Every datasheet crossing into component extraction receives
`design-studio.datasheet-evidence/1`: source/final URL or local path, retrieval
time, byte count, SHA-256 digest, requested MPN, and exact text-match status.
Remote downloads require credential-free public HTTPS, reject private-network
targets and redirect escapes, cap PDFs at 50 MiB, and verify PDF magic. The
extractor output MPN must match the requested MPN and then pass the existing
symbol/footprint trust-boundary validator before it can be saved.

When configured distributors provide no usable PDF, public web search is used
only to discover candidate HTTPS PDF links. Those links remain untrusted and
must pass the same download, digest, exact-MPN and component validation gates.

DigiKey OAuth tokens and the DesignStudio credential environment file are
written owner-read/write only (`0600`). Credential values with shell control
characters are rejected before the launch environment file is rewritten.
