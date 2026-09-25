# X12 envelope and acknowledgement mismatches (997 / 999 / TA1)

## Symptoms
- Partner returns a TA1 with a rejection code, or a 997/999 with AK9 status R (rejected) or E (accepted with errors).
- The acknowledgement references a group or interchange control number we never sent (ack correlation failure).
- Overdue acknowledgement alerts for interchanges that were delivered at the transport layer.

## Diagnosis
1. Parse the sent interchange: ISA13 must equal IEA02, every GS06 must equal its GE02, every ST02 must equal its SE02, and SE01 must equal the number of segments in the transaction set including ST and SE.
2. A trailer that does not match its header is an outbound mapping or enveloping defect on our side.
3. If the ack's AK1/TA101 references a control number we did not send, the partner is acknowledging a different interchange; check for control-number reuse after a counter reset or a duplicate send.
4. AK9 status R with AK5/IK5 codes points at a specific transaction set; the IK3/IK4 segments identify the failing segment and element.

## Remediation
- Fix the envelope generation (counter or segment count) in the outbound map; regenerate and resend the interchange with a new control number.
- If control numbers were reset, restore the counter from the last acknowledged value; never reuse a control number within the partner's de-duplication window.
- For rejected transactions, correct the data per the IK3/IK4 codes and resend only the rejected transaction sets.
