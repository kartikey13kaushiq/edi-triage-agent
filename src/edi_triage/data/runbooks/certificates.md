# Certificate expiry and rotation (AS2 signing and encryption)

## Symptoms
- `certificate expired`, `CertificateExpiredException`, `certificate not yet valid`.
- MDN reports `decryption-failed` or `integrity-check-failed` / `authentication-failed`.
- Messages sign successfully on our side but the partner rejects them with `insufficient-message-security`.

## Diagnosis
1. List every certificate in the partner profile with its validity window at the time of the incident.
2. An expired partner encryption certificate means we encrypt to a key the partner no longer accepts, or our library refuses to encrypt at all.
3. `not yet valid` usually means the partner sent a rotated certificate early and it was activated in the profile before its not_before date.
4. `decryption-failed` in the MDN with valid dates usually means the partner rotated their private key and we still use their old public certificate.

## Remediation
- Request the partner's renewed certificate, verify its fingerprint out of band, and schedule the swap in the profile.
- If a future-dated certificate was activated too early, re-activate the previous certificate until the new one's not_before date.
- Send our own renewed certificate at least 30 days before expiry and track acknowledgement of receipt.
- After the swap, resend affected interchanges.
