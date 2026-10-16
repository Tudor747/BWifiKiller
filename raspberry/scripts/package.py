"""Package source and a built ESP32 companion image without local data/secrets."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def package(image):
    image = image.resolve()
    if not image.is_file():
        raise SystemExit('Build and merge pi-companion-full.bin first; see README.md')
    destination = ROOT / 'raspberry/artifacts/bwifikill-pi-console.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    excluded = {'__pycache__', '.venv', 'data', 'artifacts', 'node_modules'}
    sources = [ROOT / name for name in ('README.md', 'LICENSE', 'BUILD_MANUAL_EN.md', 'platformio.ini', '.gitignore')]
    for folder in ('src', 'include', 'raspberry'):
        for path in (ROOT / folder).rglob('*'):
            parts = path.relative_to(ROOT).parts
            permitted = folder != 'raspberry' or parts[1] in ('console', 'scripts', 'static', 'tests') or path.name in ('README.md', 'VALIDATION.md', 'config.example.json', 'requirements.txt')
            if permitted and path.is_file() and not excluded.intersection(parts) and path.name != 'config.local.json' and path.suffix not in ('.pyc', '.token'):
                sources.append(path)
    hashes = {}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(sources):
            name = source.relative_to(ROOT).as_posix()
            content = source.read_bytes()
            # Preserve Linux script syntax even after a Windows Git checkout.
            if source.suffix == '.sh':
                content = content.replace(b'\r\n', b'\n')
            archive.writestr(name, content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        binary = image.read_bytes()
        archive.writestr('firmware/pi-companion-full.bin', binary)
        hashes['firmware/pi-companion-full.bin'] = hashlib.sha256(binary).hexdigest()
        archive.writestr('SHA256SUMS.json', json.dumps(hashes, indent=2))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest + '  ' + destination.name + '\n')
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise SystemExit('Archive integrity check failed')
    print(f'{destination}\n{len(hashes)} files, {destination.stat().st_size:,} bytes\nSHA256 {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=ROOT/'raspberry/artifacts/pi-companion-full.bin')
    package(parser.parse_args().image)