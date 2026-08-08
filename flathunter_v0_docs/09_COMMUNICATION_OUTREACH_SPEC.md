# FlatHunter V0 — Communication & Outreach Specification

## 1. Purpose

This document defines how FlatHunter contacts property owners, brokers, and current tenants after a renter approves outreach.

## 2. Separation of communication roles

### Renter-facing channel

Telegram Bot API is the primary V0 product UI.

### Property-side channels

Potential channels extracted from listings:

- WhatsApp
- Email
- Phone
- Telegram

The existence of a phone number does not automatically mean WhatsApp is explicitly available.

## 3. Contact extraction

The listing LLM should extract all explicit channels.

Example:

```json
{
  "name": "Ravi",
  "role": "CURRENT_TENANT",
  "channels": [
    {"type":"WHATSAPP","value":"+91XXXXXXXXXX","explicit":true},
    {"type":"EMAIL","value":"ravi@example.com","explicit":true}
  ]
}
```

## 4. V0 channel-selection policy

Conceptual preference:

1. Explicit WhatsApp, if the integration is available and permitted for the required outbound message
2. Email
3. Explicit Telegram contact where technically usable
4. Phone-only -> manual fallback until a phone/SMS/voice adapter exists

This policy should be configuration-driven, not buried in prompts.

## 5. Do not blast all channels

Never send the same initial cold outreach simultaneously across every discovered channel.

Use one best channel first.

If it fails or receives no response under the configured policy, a later fallback channel may be attempted.

## 6. Renter approval boundary

Before first outreach to a new lead:

- Show the renter the promising property.
- Require explicit `Contact Them` approval.

After approval, the system owns routine qualification within known renter boundaries.

## 7. Logical conversation model

One conversation is identified by:

- Search
- Listing
- Contact

The conversation may have multiple channel attempts over time.

Example:

```text
Conversation C123
  - WhatsApp initial attempt
  - Email fallback
  - Email reply
```

The qualification state remains one logical thread.

## 8. Message generation

Application code determines intent, for example:

- `CHECK_AVAILABILITY`
- `ASK_MISSING_FIELDS`
- `CLARIFY_RESPONSE`
- `ASK_VISIT_WINDOW`
- `CONFIRM_VISIT`
- `CANCEL_VISIT`

The LLM turns intent + relevant context into concise natural language.

The model must not independently expand the action scope.

## 9. First outreach content

The first message should be concise and transparent.

Conceptual example:

> Hi, I'm an automated assistant helping a renter with a property search. I came across your listing for the room in Kondapur. Is it still available?

Exact disclosure wording can be adjusted before production.

## 10. Qualification messaging behavior

Prefer small groups of related questions.

Good:

> Could you confirm the deposit and whether there is any brokerage?

Avoid:

> Please answer these 14 questions...

Question selection is determined by qualification logic.

## 11. Incoming replies

Every inbound reply should:

1. Be stored raw.
2. Be parsed using the reply parsing LLM contract.
3. Update only explicit facts.
4. Re-run hard-constraint evaluation.
5. Determine next workflow action.

## 12. Allowed autonomous actions after renter approval

The agent may:

- Check whether listing is available
- Ask missing factual questions
- Clarify ambiguous answers
- State known renter facts when asked
- Ask for possible visit windows after qualification
- Send one polite follow-up after no response

## 13. Actions requiring renter escalation

The agent must escalate before:

- Accepting rent above renter hard max
- Accepting a new/changed brokerage obligation beyond stored preference
- Agreeing to a deposit/lock-in outside known boundary
- Agreeing to a different move-in commitment if renter preference cannot answer it
- Sharing new personal information
- Making payment or deposit commitments
- Committing an exact visit time

## 14. Follow-up policy

V0 policy:

- Initial message
- Wait configured period
- One polite follow-up
- If still no response, mark conversation `NO_RESPONSE`

The exact number of hours should be configuration, not a business-rule constant embedded everywhere.

## 15. Email adapter V0

Email is the easiest fully automatable initial property-side channel.

Required capabilities:

- Send outbound email
- Correlate replies to conversation
- Store message IDs/thread IDs
- Poll or receive inbound replies

Implementation may initially use a dedicated FlatHunter mailbox.

## 16. WhatsApp adapter

WhatsApp is strategically valuable for the actual rental use case.

However, implementation must respect the official platform's current message-template, initiation, conversation-window, and pricing rules.

Therefore:

- Keep a dedicated adapter boundary.
- Do not make core qualification depend on WhatsApp availability.
- Verify current official API requirements at implementation time.

## 17. Telegram property-side outreach

A normal Telegram bot cannot be treated as a universal phone-number cold-DM mechanism.

Potential regular-user/MTProto outreach is a future adapter and is not part of V0.

## 18. Phone-only contact

If only a phone number is available and no automated supported channel is usable:

- Mark contact as manual-only.
- Generate a suggested message or call checklist.
- Do not block the listing from matching.

## 19. Communication failure handling

Record:

- Attempted channel
- Send status
- External ID if available
- Failure reason
- Time

A send failure may trigger fallback according to channel policy.

## 20. Future communication features

Deferred:

- SMS adapter
- Voice-call agent
- Telegram regular-user/TDLib adapter
- Advanced channel retry strategy
- Negotiation agent
- Automatic multi-channel campaign behavior
