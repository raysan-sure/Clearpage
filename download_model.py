"""One-time developer setup; the Windows AI package already includes these weights."""
from pathlib import Path
import urllib.request
from clearpage.restore.neural_restore import verify_model

if __name__=='__main__':
    target=Path(__file__).resolve().parent/'models/big-lama.pt'
    target.parent.mkdir(exist_ok=True)
    partial=target.with_suffix('.download')
    try:
        with urllib.request.urlopen('https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt',timeout=60) as response, partial.open('wb') as output:
            size=0
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>205669692:raise ValueError('Unexpected model size.')
                output.write(chunk)
        verify_model(partial)
        partial.replace(target)
        print('Verified local LaMa model installed.')
    finally:
        partial.unlink(missing_ok=True)
