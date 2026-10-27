# Database connection security (agreed 27 Oct 2026, required before the database agent goes public)

Chetan's requirement: full security for the "type your database details" area. Nothing in this list is optional.

## Done (27 Oct)
- Private, local, link-local, metadata (169.254.169.254) and our own addresses refused; the resolved address is pinned.
- Ports below 1024 refused.
- Limits per browser, per network address, per target database and in total.
- Driver errors never shown to the visitor; MySQL local-file sending off; PostgreSQL never reads saved passwords.
- Read-only queries only; changes only with preview, approval and Undo.

## To do before public launch of the database agent
1. Nothing to steal: database details used for the session only, never stored (or, if the user asks to keep them,
   encrypted with a key held outside the app server, auto-deleted after 24 hours, "Forget this database" button).
2. Isolated connection box: a separate container/user that only runs outside database queries; no access to our
   files, secrets, database or internal network; outbound only to database ports; the main app gets results only.
3. VPS firewall for the Goal Engine user only (partner sites untouched): no loopback, private ranges or ports < 1024.
4. Watching: alerts on limit hits and odd patterns (many hosts, many failures); automatic block of that address.
5. Attack test suite on every change: localhost tricks (0.0.0.0, 127.1, decimal/hex/octal IPs, IPv6 ::1, IPv4-mapped),
   DNS switching, redirects, fake database servers (file requests, huge or endless answers, slow answers), SQL that
   tries to write, oversized forms, and session/cookie tricks.
6. Outside-style penetration test of the whole site before the database agent opens to the public.
7. Weekly: new known attack tricks added to the test suite; dependencies updated for security fixes.
