-- Deleting a raw_document now deletes its enriched_document.
--
-- Was ON DELETE SET NULL, which turned a parent delete into a silent orphan
-- rather than a removal. That mattered because enriched_documents is what the
-- digest reads and what db.fetch_enriched_urls dedupes urls against: an
-- orphaned row keeps suppressing re-enrichment of a url whose raw row is gone.
--
-- document_team_buckets.enriched_document_id is already ON DELETE CASCADE, so
-- a raw delete now cascades the full two levels: raw -> enriched -> buckets.
--
-- enriched_documents.company_id deliberately stays ON DELETE SET NULL --
-- companies is reference data, and removing one should not destroy the leads
-- that referenced it.
--
-- Both clauses in one ALTER: a single statement, a single lock, atomic.

ALTER TABLE enriched_documents
    DROP CONSTRAINT enriched_documents_raw_document_id_fkey,
    ADD  CONSTRAINT enriched_documents_raw_document_id_fkey
         FOREIGN KEY (raw_document_id) REFERENCES raw_documents(id) ON DELETE CASCADE;
