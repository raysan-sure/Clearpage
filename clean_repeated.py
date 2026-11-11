"""Offline repeated-watermark cleanup for a scan or PDF; no AI or OCR.

Usage: python clean_repeated.py input.jpg output.png
       python clean_repeated.py input.pdf output.pdf
Creates a separate output, refuses overwrites, and reports unresolved overlaps.
"""
import argparse
import json
from pathlib import Path
import tempfile
from PIL import Image, ImageOps
from clearpage.presets.scan_patterns import repeated_document


def run(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError('Input file does not exist.')
    if source == destination or destination.exists():
        raise ValueError('Choose a new output filename. Originals and existing files are never overwritten.')
    if not destination.parent.is_dir():
        raise ValueError('The output folder does not exist.')
    if source.suffix.lower() == '.pdf':
        if destination.suffix.lower() != '.pdf':
            raise ValueError('PDF input requires a .pdf output filename.')
        from clearpage.pdf import engine
        count, _ = engine.inspect_pdf(source)
        with tempfile.TemporaryDirectory(prefix='ClearPage-offline-') as temporary:
            folder = Path(temporary); edits = {}; results = []
            for index in range(count):
                before = engine.render_page(source, index)
                pixels, _, details = repeated_document(before)
                results.append(dict(page=index+1, **details))
                if details['changed']:
                    a, b = folder/f'{index}-before.png', folder/f'{index}-after.png'
                    before.save(a); Image.fromarray(pixels).save(b)
                    edits[index] = (a, b)
            target = folder/'output.pdf'
            engine.write_pdf(source, target, edits)
            # Exclusive creation also protects against an output created mid-run.
            with destination.open('xb') as output:
                output.write(target.read_bytes())
    else:
        if destination.suffix.lower() != '.png':
            raise ValueError('Image input requires a .png output to avoid JPEG recompression.')
        with Image.open(source) as image:
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Use the application for multipage TIFF files.')
            before = ImageOps.exif_transpose(image).convert('RGB')
        pixels, _, details = repeated_document(before)
        with destination.open('xb') as output:
            Image.fromarray(pixels).save(output, format='PNG')
        results = [dict(page=1, **details)]
    return dict(output=str(destination), no_ai=True, pages=results,
                limitation='Opaque watermark/document intersections can remain. Review the result; no 100% accuracy claim is made.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        result = run(args.input, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
