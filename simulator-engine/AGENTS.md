# Contributor Notes

- Treat flight CSV files as immutable input data.
- Keep atmospheric wind separate from drone velocity and heading fields.
- Track fine and coarse particulate mass independently; derive PM10 as their sum.
- Keep the core pipeline free of Streamlit state and keep the VoxMaps export schema isolated in `exports.py`.
- Preserve deterministic behaviour for a fixed seed.
- Run `pytest` after changes and use a small fixture/config for smoke tests.
- All demonstration constants are assumptions, not calibrated VoxMaps or regulatory values.
