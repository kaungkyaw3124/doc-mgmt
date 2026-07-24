-- Performance indexes on the columns most frequently filtered by.
-- Safe to run multiple times (IF NOT EXISTS). Run against each DB:
--
--   docmgmt DB (document-service):
--     docker compose exec postgres psql -U docmgmt -d docmgmt -f /path/to/this/file
--   catalogue DB (catalogue-service):
--     docker compose exec postgres psql -U docmgmt -d catalogue -f /path/to/this/file
--
-- (Or just paste the relevant block into `psql` directly against each DB —
-- harmless if a table named here doesn't exist in that particular database.)

-- ---------- docmgmt DB ----------
CREATE INDEX IF NOT EXISTS ix_documents_doc_type ON documents (doc_type);
CREATE INDEX IF NOT EXISTS ix_documents_is_deleted ON documents (is_deleted);
CREATE INDEX IF NOT EXISTS ix_documents_customer_id ON documents (customer_id);
CREATE INDEX IF NOT EXISTS ix_documents_project_id ON documents (project_id);
CREATE INDEX IF NOT EXISTS ix_documents_status ON documents (status);
CREATE INDEX IF NOT EXISTS ix_line_items_document_id ON line_items (document_id);
CREATE INDEX IF NOT EXISTS ix_line_items_product_id ON line_items (product_id);

-- ---------- catalogue DB ----------
CREATE INDEX IF NOT EXISTS ix_products_is_deleted ON products (is_deleted);
CREATE INDEX IF NOT EXISTS ix_products_category ON products (category);
