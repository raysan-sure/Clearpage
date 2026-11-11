"""Lossless PDF optimization. Image sampling and page content stay intact."""
from pathlib import Path
import os
import shutil

import pikepdf

TARGET_BYTES = 25_000_000
pikepdf.settings.set_flate_compression_level(9)


def compress_pdf(source, destination, cancelled=lambda: False, progress=lambda value: None):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('Choose a separate output file for compression.')
    temporary = destination.with_suffix('.part.pdf')

    def report(value):
        if cancelled():
            raise InterruptedError('Compression stopped.')
        progress(min(99, value))

    try:
        report(0)
        with pikepdf.Pdf.open(source, attempt_recovery=False, suppress_warnings=True) as pdf:
            if pdf.is_encrypted:
                raise ValueError('Save an unlocked copy before compressing this PDF.')
            pending, visited = [pdf.Root], set()
            while pending:
                if cancelled():
                    raise InterruptedError('Compression stopped.')
                obj = pending.pop()
                if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Array)):
                    continue
                if obj.is_indirect:
                    if obj.objgen in visited:
                        continue
                    visited.add(obj.objgen)
                if isinstance(obj, pikepdf.Array):
                    pending.extend(obj)
                    continue
                if obj.get('/Type') == '/Sig' or obj.get('/FT') == '/Sig':
                    raise ValueError('This PDF contains a digital signature. Compress an unsigned copy to preserve the signed original.')
                pending.extend(obj.values())
            count = len(pdf.pages)
            if count == 0:
                raise ValueError('This PDF has no pages to compress.')
            pdf.save(temporary, compress_streams=True, recompress_flate=True,
                     stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate,
                     normalize_content=False, progress=report)
            if pdf.get_warnings():
                raise ValueError('This PDF needs repair before lossless compression can be verified.')
        report(99)
        with pikepdf.Pdf.open(temporary, attempt_recovery=False, suppress_warnings=True) as check:
            if len(check.pages) != count or check.get_warnings():
                raise ValueError('The compressed PDF could not be verified. Try another copy.')
        original_bytes = source.stat().st_size
        if temporary.stat().st_size >= original_bytes:
            # A fully optimized PDF may grow when rewritten. Keep its exact bytes.
            shutil.copyfile(source, temporary)
        output_bytes = temporary.stat().st_size
        report(99)
        os.replace(temporary, destination)
        return dict(original_bytes=original_bytes, output_bytes=output_bytes,
                    saved_bytes=original_bytes-output_bytes, target_bytes=TARGET_BYTES,
                    target_met=output_bytes <= TARGET_BYTES, reduced=output_bytes < original_bytes,
                    lossless=True, pages=count)
    except pikepdf.PasswordError as error:
        raise ValueError('Save an unlocked copy before compressing this PDF.') from error
    except pikepdf.PdfError as error:
        raise ValueError('This PDF could not be compressed losslessly. Try an unlocked, valid copy.') from error
    finally:
        temporary.unlink(missing_ok=True)
