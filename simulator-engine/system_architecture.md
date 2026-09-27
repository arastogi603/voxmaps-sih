# VoxMaps architecture notes

## Wind policy boundary

The desktop and engine wind policies intentionally have different names so old developer configurations remain compatible:

- Desktop `measured` maps to engine `measured_interpolated`. It requires at least two paired atmospheric observations, preserves exact pairs, vector-interpolates every internal missing interval, and holds the nearest measured vector at the flight boundaries.
- Desktop `strict_measured` maps to engine `measured`. It enforces `minimum_numeric_coverage` and `maximum_interpolation_gap_s` and keeps unresolved wind missing.
- Desktop `synthetic_fallback` maps to engine `synthetic` and is never selected silently.

Only normalized `wind_speed_mps` and `wind_direction_from_deg` can drive atmospheric wind. Drone speed, x/y/z velocity, compass heading, and trajectory direction are never wind inputs. Raw and used wind remain separate in HDF5/CSV, with exact samples labelled `measured` and filled samples labelled `interpolated`.

## Simplified Format Data boundary

- `.xlsx` flight uploads are accepted only when the workbook contains the exact `Complete Wind Data` worksheet.
- SFD automatically maps `elapsed_time_ms`, `utc_datetime`, WGS84 coordinates, `altitude(feet)`, `wind_speed_complete(mph)`, and `wind_direction_complete_from(degrees)`.
- `wind_speed_original(mph)` and `wind_direction_original_from(degrees)` remain the raw audit fields. Complete wind drives transport, while `wind_data_source`, `wind_confidence`, `wind_gap_id`, and `calm_flag` retain measured/generated provenance.
- Existing AirData CSV ingestion and mappings remain backward compatible.
