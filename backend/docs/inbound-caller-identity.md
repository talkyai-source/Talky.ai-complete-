# Inbound caller identity — F5-10

This is a conservative carrier-assertion contract, not account authentication
or proof of a person's identity. **Production verification is not activated or
proven.** The protected lifecycle handoff described below remains unfinished;
with that handoff absent, admitted calls are explicitly unverified.

## Durable behavior

The managed Asterisk dialplan transports P-Asserted-Identity and the optional
P-Attestation-Indicator using named base64 Stasis arguments. Existing direction,
DID, context and legacy agent slots retain their positions. The adapter rejects
duplicate, malformed, control-bearing or oversized assertion envelopes. Base64
prevents header commas from becoming Stasis argument separators; it is not
encryption or an authenticity check. Normal Asterisk/ARI diagnostic logging
must still be handled as sensitive call data.

Admission writes only a normalized `route_snapshot.caller_identity` object:
version, `caller_id_verified`, attestation A/B/C/null, normalized verstat and
source (`trusted_carrier`, `untrusted` or `missing`). Raw display names, PAI and
Identity JWTs are not persisted by this feature. No new migration is needed.
Legacy, missing, malformed and private caller identities default to false.

The call-detail API contract exposes `caller_id_verified` and
`caller_id_attestation`; CRM activity text includes both. An attestation shown
alongside false is an unverified assertion, not proof. A verified flag requires
all of: an operator-trusted platform endpoint, one supported PAI telephone URI
matching ANI, passed verification and explicit full attestation A. B/C,
PAI presence alone, unsupported carrier encodings, failed/absent verification,
conflicting identities, private ANI and number mismatch remain unverified.

Automatic voice callbacks at the nonprotected durable action executor and
automatic email/phone/full-name/company capture read the tenant-scoped call
under the existing database transaction before writing. Unverified inbound
identity changes are refused. Payload/model flags cannot grant authority.
Database failure and cancellation cannot permit an effect. Manual edits and
outbound behavior retain their existing authorization requirements. Opt-outs
remain permitted and permanent under the existing DNC writer.

CRM contact creation and automatic contact-link assignment have an independent
guard: an unverified number is not searched and attached to another person's
record. An existing explicit, account-bound contact link can receive a call
activity labeled unverified; that path does not create a contact or change the
link. Fresh source validation also gates a prepared contact-create operation.

## Operator-owned carrier contract

`TALKY_CALLER_ID_TRUSTED_ENDPOINTS` maps to Settings
`inbound_caller_id_trusted_endpoints` and defaults to the empty string. It is a
comma-separated exact list of platform-owned Asterisk endpoint IDs. Wildcards,
anonymous and tenant-managed `trunk-*` endpoints are rejected. Keep it empty
until all of the following are evidenced for the actual carrier:

1. SIP ingress identifies the carrier by reviewed source IP ranges and rejects
   unsolicited sources. Endpoint name or successful SIP registration alone is
   not proof of a trustworthy verification service.
2. The carrier/edge strips or replaces user-supplied identity/verification
   claims and produces its own verified result. Document its attestation and
   verstat semantics, and normalize them at the controlled carrier boundary to
   the supported PAI verstat plus uppercase A/B/C indicator contract.
3. A forged From/PAI/verstat/A assertion from an untrusted source is refused or
   remains unverified. Test failed, absent, mismatching, B/C and private cases,
   then a real carrier-verified A call whose asserted number equals ANI.
4. After the Claude handoff, prove the same normalized result in the durable
   call record, authorized API and fake/staging CRM payload. Confirm callback
   and contact-denial behavior and an opt-out without automatic contact writes.

The code does not install a STIR verifier, certificate trust store, carrier
normalizer or a new provider. It does not fetch an Identity certificate or
decode an unverified JWT into a trusted attestation. The live carrier's header
format and trust behavior remain owner evidence; example provider documents
are not evidence about this installation.

To disable new trust decisions, clear the setting and restart the application
through the normal deployment process. Previously recorded call evidence is
retained; clearing configuration does not rewrite history or revoke a saved
verified assertion for an ongoing call. Handle affected ongoing calls through
the existing approved operational controls before resuming automation. Do not
roll back only the consumer guards to recover automatic inbound behavior.

## Exact Claude handoff — not implemented here

`backend/app/domain/services/telephony/lifecycle.py`, in `_admit_inbound_call`,
constructs `InboundAdmissionRequest.metadata` with only `ingress_endpoint` and
`linked_id` (around lines 2821–2824). Forward the adapter's bounded
`caller_identity_headers` envelope there too. Do not forward an arbitrary
caller-supplied verified boolean, derive a tenant from ANI, or add an independent
adapter database write to bypass that boundary. Admission owns normalization
and persistence in the original call transaction.

Protected `voice_pipeline/action_execution.py` already restricts callback
capabilities to outbound campaigns. This item adds defense at the shared
nonprotected executor; it does not enable inbound callbacks, rewrite that
capability policy or alter agent speech. Claude owns any verified/unverified
context presentation and appropriate manual-review wording. No end-to-end
verified call is claimed while this protected handoff remains absent.

## Primary references and verification scope

- [Asterisk PJSIP_HEADER](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Dialplan_Functions/PJSIP_HEADER/)
  documents reading headers from the inbound channel.
- [Asterisk STIR_SHAKEN](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Dialplan_Functions/STIR_SHAKEN/)
  describes verifier-result access when a verifier is configured; this task
  does not establish that configuration.
- [RFC 3325](https://www.rfc-editor.org/rfc/rfc3325.html) limits asserted identity
  to a defined trust domain.
- [Twilio attestation definitions](https://www.twilio.com/docs/voice/trusted-calling-with-shakenstir)
  distinguish full number-use authority from B/C assertions.
- [Bandwidth verification headers](https://www.bandwidth.com/support/en/articles/12823011-stir-shaken-inbound-verification)
  illustrate PAI verstat and a separate attestation indicator; other carrier
  formats need their own reviewed normalization contract.

Regression tests use synthetic SIP metadata, DB transaction ports and CRM
providers. They are not proof of live Asterisk syntax/reload, actual PostgreSQL
locking, production ingress filtering or carrier verification. Root retains
the failing/passing outputs; the exact deployment and operational evidence
above remain unverified.
