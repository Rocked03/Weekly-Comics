-- Expand 'Comic' into 'Print Comic' + 'Digital Comic'; map 'Digital Chapter'
-- to 'Digital Comic' (preserving those guilds' suppression intent, consistent
-- with the locg-api week-cache migration). ARRAY(SELECT ...) yields {} (not
-- NULL) when nothing remains.
UPDATE configuration
SET disabled_formats = ARRAY(
  SELECT DISTINCT f FROM (
    SELECT unnest(disabled_formats) AS f
    UNION ALL SELECT 'Print Comic' WHERE 'Comic' = ANY(disabled_formats)
    UNION ALL SELECT 'Digital Comic' WHERE 'Comic' = ANY(disabled_formats)
    UNION ALL SELECT 'Digital Comic' WHERE 'Digital Chapter' = ANY(disabled_formats)
  ) s
  WHERE f NOT IN ('Comic', 'Digital Chapter')
)
WHERE 'Comic' = ANY(disabled_formats) OR 'Digital Chapter' = ANY(disabled_formats);
