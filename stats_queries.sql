-- name: table_feature_count
-- type: stat
SELECT COUNT(*) AS feature_count
FROM lbm.{table};

-- name: date_range
-- type: stat
-- requires: vt_created
SELECT
    CAST(MIN(vt_created) AS TEXT) as date_range_min,
    CAST(MAX(vt_created) AS TEXT) as date_range_max
FROM lbm.{table};

-- name: class_feature_count
-- type: stat
-- requires: class
SELECT
    class,
    COUNT(*) AS feature_count
FROM lbm.{table}
GROUP BY class;

-- name: subclass_feature_count
-- type: stat
-- requires: class,subclass
SELECT
    class,
    subclass,
    COUNT(*) AS feature_count
FROM lbm.{table}
GROUP BY class, subclass;

-- name: api_id_count_class
-- type: stat
-- requires: class,api_id
SELECT
    class,
    COUNT(*) AS total_features,
    COUNT(api_id) AS non_null_api_ids
FROM lbm.{table}
GROUP BY class;

-- name: api_id_count_subclass
-- type: stat
-- requires: class,subclass,api_id
SELECT
    class,
    subclass,
    COUNT(api_id) AS non_null_api_ids
FROM lbm.{table}
GROUP BY class, subclass;

-- name: duplicate_ids
-- type: stat
-- requires: feature_id
SELECT COUNT(*) AS duplicate_ids
FROM (
    SELECT feature_id
    FROM lbm.{table}
    GROUP BY feature_id
    HAVING COUNT(*) > 1
) x;

-- name: null_geometries
-- type: stat
-- requires: the_geom
SELECT COUNT(*) AS null_geometries
FROM lbm.{table}
WHERE the_geom IS NULL;

-- name: features_within_bounding_box
-- type: stat
-- requires: the_geom
SELECT
    COUNT(*) FILTER (
        WHERE the_geom && ST_MakeEnvelope(
            662097, 5749899,
            1170526, 6076438,
            3857
        )
    )::float
    / COUNT(*) AS within_bbox_ratio
FROM lbm.{table};

-- name: features_outside_bbox
-- type: features
-- requires: the_geom
SELECT *
FROM lbm.{table}
WHERE NOT (the_geom && ST_MakeEnvelope(
    662097, 5749899,
    1170526, 6076438,
    3857
));