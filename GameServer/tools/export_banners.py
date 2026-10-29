"""Generate data/2.1.1/banner_defaults.json (banner catalogId -> {CustomData key: value}) from the 2.1.1 banner
catalog: the customization a new banner item starts with (mpgs/item_defaults.py).
python -m tools.export_banners [all-datatables-211 directory] [output path]
"""
import glob
import json
import os
import sys

from tools.export_recipes import DEFAULT, _rows

OUT = os.path.join(os.path.dirname(__file__), '..', 'data', '2.1.1', 'banner_defaults.json')


def main(src=DEFAULT, out_path=OUT):
    rows = _rows(glob.glob(os.path.join(src, '*_banner_catalog.json'))[0])
    out = {cid: {kv['Key']: kv['Value'] for kv in row.get('CustomData') or [] if kv.get('Key')}
           for cid, row in rows.items() if isinstance(row, dict)}
    json.dump(out, open(out_path, 'w'), indent=0, sort_keys=True)
    print('banners', len(out))


if __name__ == '__main__':
    main(*sys.argv[1:])
