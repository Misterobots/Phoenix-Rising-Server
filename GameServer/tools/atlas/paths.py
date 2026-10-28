"""Where the atlas reads from and writes to. Override the inputs with ATLAS_SDK / ATLAS_ASSETDUMP."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GAMESERVER = os.path.normpath(os.path.join(HERE, '..', '..'))
REPO = os.path.dirname(GAMESERVER)
WORKSPACE = os.path.normpath(os.path.join(REPO, '..', '..'))   # Hunt_Core

SDK = os.environ.get('ATLAS_SDK') or os.path.join(
    WORKSPACE, 'Mystic-Paradox', 'sdk211', '5.1.1-682875+__phx-archon_release_2.1.1-Archon')
DUMPSPACE = os.path.join(SDK, 'Dumpspace')
CPPSDK = os.path.join(SDK, 'CppSDK', 'SDK')
ASSETDUMP = os.environ.get('ATLAS_ASSETDUMP') or os.path.join(WORKSPACE, 'Mystic-Paradox', 'tools', 'AssetDump')
ASSETDUMP_DLL = os.path.join(ASSETDUMP, 'bin', 'AssetDump.dll')
KISMET_PSEUDO = os.path.join(ASSETDUMP, 'kismet_pseudo.py')

BUILD_DATA = os.path.join(GAMESERVER, 'data', '2.1.1')
OUT = os.environ.get('ATLAS_OUT') or os.path.join(GAMESERVER, 'data', 'atlas')
CLASSES_DIR = os.path.join(OUT, 'classes')
PSEUDO_DIR = os.path.join(OUT, 'pseudo')
VERIFIED = os.path.join(OUT, 'verified.json')          # hand-written, never regenerated
NATIVE_NOTES = os.path.join(OUT, 'native_notes.json')  # hand-written decompile notes, never regenerated
# AssetDump output is large and rebuildable, so it stays out of git.
CACHE = os.environ.get('ATLAS_CACHE') or os.path.join(GAMESERVER, '.atlas_cache')
STARTER = os.path.join(HERE, 'starter.txt')


def class_file(name, ext='.json', folder=None):
    """File for a class; Dumpspace qualifies duplicate names as 'pkg::AName_C', which is not a valid file name."""
    import re
    return os.path.join(folder or CLASSES_DIR, re.sub(r'[^\w.-]', '_', name) + ext)
