# SFTP connection and SSH handshake failures

## Symptoms
- `Host key verification failed` or `REMOTE HOST IDENTIFICATION HAS CHANGED`.
- `Connection timed out`, `No route to host`, or `Connection refused` on port 22/2222.
- `no matching key exchange method found` or `no matching host key type found`.

## Diagnosis
1. Compare the host key fingerprint presented by the server with the pinned fingerprint in the partner profile. A changed host key is either a legitimate server migration or a man-in-the-middle; it must be confirmed with the partner out of band before anything else.
2. A timeout while DNS resolves points to a firewall or allow-list change on the partner side, or the partner server being down.
3. Key exchange mismatches happen when the partner upgrades OpenSSH and drops legacy algorithms such as diffie-hellman-group1-sha1 or ssh-rsa.

## Remediation
- Host key change: open a ticket with the partner's security contact, confirm the new fingerprint over a second channel, then update the pinned fingerprint. Never auto-accept a new host key.
- Timeout: verify our egress IPs are allow-listed and ask the partner to confirm the service is up.
- Algorithm mismatch: enable a modern algorithm set on our client (curve25519-sha256, rsa-sha2-512). Do not re-enable deprecated algorithms.
