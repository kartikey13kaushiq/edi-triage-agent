# AS2 transport and TLS handshake failures

## Symptoms
- `SSLHandshakeException`, `handshake_failure`, `protocol_version` alerts, or `PKIX path building failed` when posting to the partner URL.
- HTTP connection refused, connection reset, or timeouts before any HTTP status is returned.
- No MDN is received because the message never reached the partner's AS2 server.

## Diagnosis
1. Probe the partner endpoint from the integration runner and record the negotiated TLS version and cipher.
2. If the server only offers TLS 1.0/1.1 our client policy (minimum TLS 1.2) will refuse the connection. This is a partner-side configuration problem, not ours.
3. `PKIX path building failed` means the partner's TLS server certificate chains to a CA that is not in our trust store, often after the partner renewed their certificate with a new intermediate.
4. Connection timeouts with a reachable DNS name usually mean an IP allow-list change on the partner firewall. Confirm our egress IPs are still allow-listed.

## Remediation
- Ask the partner to enable TLS 1.2 or 1.3 on their AS2 endpoint. Do not lower our minimum TLS version.
- Import the partner's new intermediate CA into the trust store only after verifying the chain out of band.
- Send the partner our current egress IP list and request re-allow-listing.
- Once connectivity is restored, resend the failed interchanges from the retry queue. Resending is reversible only if the partner de-duplicates on ISA13.
