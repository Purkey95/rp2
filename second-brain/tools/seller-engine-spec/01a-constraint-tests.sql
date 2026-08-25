\set ON_ERROR_STOP off
-- Seed the minimum graph needed to exercise the guards.
INSERT INTO marketing.session (id, correlation_id, channel)
  VALUES ('11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000001','ORGANIC_REPORT');
INSERT INTO seller.person (id) VALUES ('22222222-2222-2222-2222-222222222222');
INSERT INTO seller.inquiry (id, correlation_id, person_id, marketing_session_id, idempotency_key)
  VALUES ('33333333-3333-3333-3333-333333333333','aaaaaaaa-0000-0000-0000-000000000001',
          '22222222-2222-2222-2222-222222222222','11111111-1111-1111-1111-111111111111','idem-abc');
INSERT INTO lead.opportunity (id, correlation_id, seller_inquiry_id)
  VALUES ('44444444-4444-4444-4444-444444444444','aaaaaaaa-0000-0000-0000-000000000001',
          '33333333-3333-3333-3333-333333333333');
INSERT INTO marketplace.organization (id, legal_name) VALUES ('55555555-5555-5555-5555-555555555555','Acme Homes LLC');
INSERT INTO marketplace.buyer (id, organization_id) VALUES ('66666666-6666-6666-6666-666666666666','55555555-5555-5555-5555-555555555555');
INSERT INTO marketplace.buyer (id, organization_id) VALUES ('77777777-7777-7777-7777-777777777777','55555555-5555-5555-5555-555555555555');

\echo '=== TEST 1: first EXCLUSIVE assignment must succeed'
INSERT INTO marketplace.assignment (opportunity_id, buyer_id, exclusivity_type, price_cents)
  VALUES ('44444444-4444-4444-4444-444444444444','66666666-6666-6666-6666-666666666666','EXCLUSIVE',12500);

\echo '=== TEST 2: selling the SAME exclusive lead to a second buyer must FAIL'
INSERT INTO marketplace.assignment (opportunity_id, buyer_id, exclusivity_type, price_cents)
  VALUES ('44444444-4444-4444-4444-444444444444','77777777-7777-7777-7777-777777777777','EXCLUSIVE',12500);

\echo '=== TEST 3: duplicate submission via the same idempotency key must FAIL'
INSERT INTO seller.inquiry (correlation_id, person_id, idempotency_key)
  VALUES ('aaaaaaaa-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','idem-abc');

\echo '=== TEST 4: an inquiry cannot be its own duplicate'
INSERT INTO lead.duplicate_link (original_inquiry_id, duplicate_inquiry_id, match_probability)
  VALUES ('33333333-3333-3333-3333-333333333333','33333333-3333-3333-3333-333333333333',0.99);

\echo '=== TEST 5: an invalid status value must FAIL (enum, not free text)'
UPDATE lead.opportunity SET status = 'TOTALLY_MADE_UP' WHERE id = '44444444-4444-4444-4444-444444444444';

\echo '=== TEST 6: buyer_balance view sums the ledger rather than trusting a column'
INSERT INTO finance.ledger_entry (buyer_id, transaction_type, amount_cents) VALUES
  ('66666666-6666-6666-6666-666666666666','DEPOSIT',100000),
  ('66666666-6666-6666-6666-666666666666','LEAD_PURCHASE',-12500),
  ('66666666-6666-6666-6666-666666666666','REFUND_CREDIT',12500);
SELECT balance_cents FROM finance.buyer_balance WHERE buyer_id='66666666-6666-6666-6666-666666666666';
