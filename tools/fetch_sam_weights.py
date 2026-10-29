"""Fetch the optional SAM mask checkpoint from its upstream publisher; never load it."""
import hashlib
import os
from pathlib import Path
import ssl
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

URL = 'https://huggingface.co/lkeab/hq-sam/resolve/09b02a333b37772133eff3997bdba997867374b7/sam_hq_vit_tiny.pth'
SHA256 = '0f32c075ccdd870ae54db2f7630e7a0878ede5a2b06d05d6fe02c65a82fb7196'
SIZE = 42526881


def plain(path):
    for node in (path, *path.parents):
        if node.exists() and (node.is_symlink() or getattr(node.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Linked SAM output rejected')


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        value = urlsplit(newurl)
        host = (value.hostname or '').lower()
        if value.scheme != 'https' or value.username or value.password or value.port not in (None, 443) or not (host == 'huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.hf.co')):
            raise ValueError('Unexpected SAM publisher redirect')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def main():
    folder = Path(__file__).resolve().parents[1] / 'assets/cloud-annotation'
    plain(folder)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / 'sam_hq_vit_tiny.pth'
    plain(destination)
    if destination.exists():
        with destination.open('rb') as file:
            valid = destination.stat().st_size == SIZE and hashlib.file_digest(file, 'sha256').hexdigest() == SHA256
        if valid:
            print('SAM checkpoint already verified.')
            return
        raise ValueError('Existing SAM checkpoint differs; it was preserved. Move it aside before retrying.')
    opener = urllib.request.build_opener(Redirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    partial = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='sam-download-', suffix='.partial', delete=False) as output:
            partial = Path(output.name)
            checksum, total, deadline = hashlib.sha256(), 0, time.monotonic() + 300
            with opener.open(URL, timeout=15) as response:
                while chunk := response.read(64 * 1024):
                    if time.monotonic() > deadline:
                        raise TimeoutError('SAM download deadline exceeded')
                    total += len(chunk)
                    if total > SIZE:
                        raise ValueError('SAM download exceeded expected size')
                    checksum.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != SIZE or checksum.hexdigest() != SHA256:
            raise ValueError('SAM checkpoint failed upstream integrity verification')
        plain(folder)
        os.link(partial, destination)  # create-new, never replace a concurrent file
        print('SAM checkpoint downloaded and verified. Review its upstream license before redistribution.')
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
