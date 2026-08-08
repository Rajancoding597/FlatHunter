# FlatHunter — Future Backlog & Deferred Decisions

## 1. Purpose

This document records good ideas intentionally deferred from V0. The goal is to prevent repeated re-discussion and prevent coding agents from pulling future scope into the MVP.

Items here are not commitments; they are candidates to evaluate after the core vertical slice works.

## 2. Listing source acquisition

### Automated permitted source adapters

Potential future adapters for rental portals or feeds where automation is allowed.

### Facebook/group ingestion automation

Investigate only with a compliant technical/product approach. Do not make the product dependent on fragile scraping.

### User-contributed listings

Allow renters to forward/share listings into FlatHunter.

Potential model:

- User-submitted listings enter an unverified queue.
- Admin or automated checks approve before becoming trusted inventory.

## 3. Communication channels

### Full WhatsApp automation

Make WhatsApp a first-class property-side channel after official API integration is stable.

### Telegram regular-user/MTProto adapter

Potentially use a dedicated regular Telegram account and official client APIs for phone/contact resolution where allowed. This is separate from the renter-facing Bot API.

### SMS

Fallback for phone-only contacts.

### Voice calling agent

Agent can call brokers/owners and extract qualification facts from calls.

This should be considered only after text workflow is proven.

## 4. Advanced autonomy

### Auto-contact mode

Allow renter to opt in:

> Automatically contact any match above my configured threshold.

### Negotiation agent

Negotiate rent, deposit, brokerage, or move-in conditions within renter-authorized bounds.

Requires explicit autonomy policy.

### Automatic visit confirmation windows

Renter may authorize:

> Auto-confirm any Saturday visit between 10 AM and 2 PM.

## 5. Location intelligence

### Geocoding

Normalize listings to coordinates.

### Commute-time scoring

Use actual travel estimates rather than only locality buckets.

### Hyderabad locality/landmark dictionary

Support aliases such as landmarks, colloquial names, office clusters, and neighborhood boundaries.

### Multi-city rollout

Add Bengaluru/Pune/Mumbai/etc. without changing core domain model.

## 6. Visit optimization

### Calendar integration

- Google Calendar
- Outlook Calendar

Read availability and optionally create visit events.

### Route optimization

Cluster visits geographically and temporally to reduce physical fatigue.

Example:

```text
11:00 Gachibowli
12:30 Kondapur
14:00 Madhapur
```

### Visit-day brief

Send renter a morning itinerary with photos, contacts, key questions, and route order.

## 7. Property image intelligence

V0 stores photos only.

Future optional visual analysis:

- Furnishing detection
- Visible appliance detection
- Natural light
- Approximate room spaciousness
- Property-condition signals
- Balcony detection

Treat subjective visual inference carefully and do not present it as verified fact.

## 8. Trust and risk layer

Potential signals:

- Same phone number across many unrelated listings
- Unusual advance-payment requests
- Price anomalies
- Duplicate/reposted inventory
- Stale contact behavior
- Contact consistency

Could produce a listing/contact trust indicator.

## 9. Deduplication

### V0

Optional simple warning:

- Same contact number
- Same locality
- Similar rent

### Later

- Text similarity
- Image similarity
- Address/society normalization
- Cross-source identity resolution

## 10. LLM infrastructure

### Multi-provider router

Potential providers:

- Gemini
- Groq
- OpenRouter
- Cloudflare Workers AI
- OpenAI
- Local models

Route based on:

- Vision capability
- Structured output quality
- Latency
- Cost/quota
- Reliability

### Local models

Evaluate based on actual developer hardware.

Potential use cases:

- Classification
- Basic extraction
- Embeddings
- Spam detection
- Cheap preprocessing

Local-only is not a V0 requirement.

## 11. Data/privacy layer

Deferred from V0 brainstorming, but required before serious production usage.

Potential work:

- PII minimization/redaction before model calls
- Retention policy
- User consent/disclosure
- Contact-data access controls
- Audit logs
- Data deletion flows
- Provider data-processing review

## 12. Product UI

### Web admin dashboard

Only when Telegram admin UX becomes limiting.

### Renter web/app UI

Could support richer browsing, maps, filters, photo galleries, and visit planning.

Telegram remains a useful conversational channel even if a web UI is added.

## 13. Multiple searches

Allow renter to maintain multiple simultaneous searches, for example:

- Private room for self
- 2BHK with friend

Requires better Telegram navigation and notification routing.

## 14. Smarter preference learning

Learn from renter feedback after visits.

Examples:

- “Room felt too small.”
- “Location was great.”
- “I'd pay more for something like this.”

Use feedback to update ranking preferences, with renter visibility/control.

## 15. Marketplace analytics

Potential admin insights:

- Search demand by locality
- Median rent by room type
- Match-to-response conversion
- Response time by channel
- Qualification failure reasons
- Visit conversion
- Staleness rate

## 16. Production infrastructure

Only when usage justifies it:

- Dedicated worker service
- Redis/queue
- Horizontal scaling
- Monitoring/alerting
- Central object storage
- Rate-limit coordination
- Backups and disaster recovery

## 17. Product expansion principle

Prioritize future work by asking:

> Which feature most increases the conversion from active renter search to qualified, worthwhile property visit while reducing user effort?

Do not prioritize features merely because they make the architecture look more sophisticated.
