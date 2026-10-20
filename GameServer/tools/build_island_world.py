"""Build a hunt bootstrap from local AssetDump exports; no services are started."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mpgs import hunts, island_world, template
from mpgs.genworld import GenWorld
from mpgs.repl import model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hunt', required=True)
    parser.add_argument('--map')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--source', type=Path, help='directory of raw AssetDump exports')
    inputs.add_argument('--contract', type=Path, help='previously extracted island contract')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--slot', type=int, default=0)
    args = parser.parse_args()
    selected = hunts.select(args.hunt, args.map)
    contract = (json.loads(args.contract.read_text(encoding='utf-8')) if args.contract else
                island_world.extract_contract(selected, args.source))
    if any(contract.get(k) != selected[k] for k in ('huntId', 'map', 'gameMode')):
        raise ValueError('Island contract does not match selected hunt')
    base = GenWorld.from_template(ROOT / 'data/2.1.1/ramsgate_world.json')
    world = island_world.build_world(base, contract, args.slot)
    hunts.validate_world(selected, world)
    # Encode every entry before publishing any output.
    for entry in world.entries:
        model.encode(entry.parsed, world.ctx, entry.cls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    template.write_template(world, str(args.output), {'islandContract': contract, 'spawnSlot': args.slot,
                            'baseSha256': template.file_sha256(ROOT / 'data/2.1.1/ramsgate_world.json'),
                            'acceptance': 'offline encoding only; stock-client join not yet validated'})
    args.output.with_suffix('.contract.json').write_text(json.dumps(contract, indent=2) + '\n', encoding='utf-8')
    print(f'Encoded {len(world.entries)} entries for {selected["huntId"]}: {args.output}')


if __name__ == '__main__':
    main()
