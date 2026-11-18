#!/usr/bin/env python3
"""Export only the verified release payload, without local environments or runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from common import ROOT, integrity, read, release_version, sha


def export_release(output, root=ROOT):
    root = Path(root).resolve()
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a new archive path')
    if root == output.resolve() or root in output.resolve().parents:
        raise ValueError('Export outside the team package')
    if integrity(root):
        raise ValueError('Release integrity failed; no archive was exported')
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.harness-export-', suffix='.zip', dir=output.parent)
    os.close(descriptor)
    pending = Path(temporary)
    try:
        manifest_hash = sha(root / 'release-files.json')
        files = read(root / 'release-files.json')
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted([*files, 'release-files.json']):
                data = (root / relative).read_bytes()
                wanted = manifest_hash if relative == 'release-files.json' else files[relative]
                if hashlib.sha256(data).hexdigest() != wanted:
                    raise ValueError('Release changed during export: ' + relative)
                metadata = zipfile.ZipInfo.from_file(root / relative, 'android-harmony-migration-harness/' + relative)
                metadata.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(metadata, data)
        if integrity(root) or sha(root / 'release-files.json') != manifest_hash:
            raise ValueError('Release changed during export')
        # Exclusive publication also preserves a file created while ZIP generation ran.
        with output.open('xb') as destination, pending.open('rb') as source:
            shutil.copyfileobj(source, destination)
        return {'version': release_version(root), 'archive': str(output), 'sha256': sha(output),
                'files': len(files) + 1, 'installed': False}
    finally:
        pending.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export_release(args.output), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
