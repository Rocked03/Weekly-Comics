-- Expand 'Comic' into 'Print Comic' + 'Digital Comic'; drop 'Digital Chapter'.
-- ARRAY(SELECT ...) yields {} (not NULL), so a guild that
-- had disabled only 'Digital Chapter' correctly ends with nothing disabled.
UPDATE configuration
SET disabled_formats = ARRAY(
  SELECT DISTINCT f FROM (
    SELECT unnest(disabled_formats) AS f
    UNION ALL SELECT 'Print Comic' WHERE 'Comic' = ANY(disabled_formats)
    UNION ALL SELECT 'Digital Comic' WHERE 'Comic' = ANY(disabled_formats)
  ) s
  WHERE f NOT IN ('Comic', 'Digital Chapter')
)
WHERE 'Comic' = ANY(disabled_formats) OR 'Digital Chapter' = ANY(disabled_formats);
