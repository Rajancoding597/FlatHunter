-- Required for databases created before OTHER became an extraction classification.
ALTER TYPE content_type ADD VALUE IF NOT EXISTS 'OTHER';
