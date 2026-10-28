"""Generate data/2.1.1/dust.json (catalogId -> salvage result [[catalogId, amount]]) from the 2.1.1 catalog exports:
each catalog row's GenericItemData.DustItemResult names a bundles_dusting_results row, whose
Bundle.BundledItemsWithQuantity is what one dusted item gives.
python -m tools.export_dust [all-datatables-211 directory] [output path]
"""
import glob
import json
import os
import sys

from tools.export_recipes import DEFAULT, _rows

OUT = os.path.join(os.path.dirname(__file__), '..', 'data', '2.1.1', 'dust.json')


def _order(path):
    """obsolete tables first, then base, composite, override: a later table's row wins."""
    name = os.path.basename(path)
    return (0 if 'obsolete' in name else 3 if 'override' in name else 2 if 'composite' in name else 1, name)


def main(src=DEFAULT, out_path=OUT):
    bundles = _rows(glob.glob(os.path.join(src, '*_bundles_dusting_results.json'))[0])
    results = {k: [[i['Item'], int(i['Amount'])] for i in (v.get('Bundle') or {}).get('BundledItemsWithQuantity') or []
                   if i.get('Item') and int(i.get('Amount') or 0) > 0]
               for k, v in bundles.items()}
    out, unknown = {}, set()
    for path in sorted(glob.glob(os.path.join(src, '*Catalogs_*catalog*.json')), key=_order):
        for cid, row in _rows(path).items():
            ref = ((row.get('GenericItemData') or {}) if isinstance(row, dict) else {}).get('DustItemResult')
            if not ref:
                continue
            if results.get(ref):
                out[cid] = results[ref]
            else:
                unknown.add(ref)
    json.dump({'source': 'catalog GenericItemData.DustItemResult -> bundles_dusting_results BundledItemsWithQuantity '
                         '(2.1.1 export)', 'dust': out}, open(out_path, 'w'), indent=1, sort_keys=True)
    print('dustable', len(out), 'unknown bundles', sorted(unknown))


if __name__ == '__main__':
    main(*sys.argv[1:])
