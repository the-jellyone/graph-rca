"""Lossless v3 analysis projection of repeated Docker stream messages."""
from .contracts import canonical, timestamp


def unique_stats(raw, resources=None):
    """Collapse identical messages without altering raw artifacts or timestamps.

    Different observations sharing a selected source identity and instant are
    ambiguous and remain an error. Unused streams are retained unchanged;
    validating them cannot gate tools that never consume them. Coverage and
    window validation still run downstream for every selected evidence source.
    """
    seen, rows = {}, []
    selected = None if resources is None else frozenset(resources)
    for row in raw.get('stats', []):
        if selected is not None and row['resource_id'] not in selected:
            rows.append(row)
            continue
        key = (row['resource_id'], row['container_id'], timestamp(row['sample_time']))
        encoded = canonical(row)
        if key in seen:
            if seen[key] != encoded:
                raise ValueError('conflicting v3 stats samples share one timestamp')
            continue
        seen[key] = encoded
        rows.append(row)
    return {**raw, 'stats': rows}
