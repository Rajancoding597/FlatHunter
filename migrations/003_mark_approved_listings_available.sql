-- Admin approval is the availability confirmation for FlatHunter inventory.
-- The predicates keep this migration idempotent and exclude unrelated UNKNOWN rows.
UPDATE listings AS listing
SET availability_status = 'AVAILABLE'
FROM listing_drafts AS draft
WHERE listing.created_from_draft_id = draft.id
  AND listing.availability_status = 'UNKNOWN'
  AND draft.extraction_status = 'APPROVED';
