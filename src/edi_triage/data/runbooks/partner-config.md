# Partner configuration errors (identifiers, endpoints, credentials)

## Symptoms
- AS2 MDN or HTTP error says `unknown-trading-partner`, `authentication-failed` for an AS2 identifier, or HTTP 404 on the receive path.
- SFTP `Permission denied (publickey,password)` or `No such file or directory` on the upload path.
- Partner rejects the interchange because ISA08/GS03 receiver identifiers are wrong.

## Diagnosis
1. Compare the AS2-From and AS2-To headers on the wire with the identifiers in the partner profile; they are case-sensitive.
2. Compare the URL or SFTP path we connected to with the profile endpoint; a stale URL after the partner migrated servers returns 404.
3. For SFTP, check that we authenticated as the profile user and that the upload directory exists.
4. Compare the ISA08 receiver id in the sent interchange with the partner's expected receiver id.

## Remediation
- Correct the identifier, URL, user or directory in the partner profile after confirming the value with the partner. Profile changes go through change control.
- If the partner changed their identifiers, request written confirmation before updating.
- Resend the failed interchanges once the profile is corrected.
