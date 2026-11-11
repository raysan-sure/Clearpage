"""One-time developer setup. The employee Windows package includes this model."""
from pathlib import Path
import hashlib
import urllib.request
from clearpage.restore.text_restore import MODEL_NAME, MODEL_SHA256

if __name__ == '__main__':
    target=Path(__file__).resolve().parent/'models'/MODEL_NAME
    target.parent.mkdir(exist_ok=True)
    partial=target.with_suffix('.download')
    try:
        url='https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/'+MODEL_NAME
        with urllib.request.urlopen(url,timeout=60) as response, partial.open('wb') as output:
            size=0
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>21234383:raise ValueError('Unexpected text model size.')
                output.write(chunk)
        if hashlib.sha256(partial.read_bytes()).hexdigest()!=MODEL_SHA256:
            raise ValueError('Text model integrity check failed.')
        partial.replace(target)
        print('Verified offline text model installed.')
    finally:
        partial.unlink(missing_ok=True)
